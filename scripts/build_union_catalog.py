#!/usr/bin/env python3
"""Build the bundle-v2.0.0 union site catalog from per-vendor site lists.

Takes two per-vendor ``rsid<TAB>chrom_raw<TAB>pos`` TSVs — the 23andMe v5 site
list (Phase A1) and the AncestryDNA v2.0 site list (Phase A3) — applies each
vendor's PAR/MT chromosome collapse via
``backend.ingestion.chromosomes.normalize_for`` (so the catalog's chromosome
encoding matches what the annotation engine sees at runtime), then merges the
two into one deterministic union keyed on ``(chrom, pos)``.

The merge is reproducible: the same inputs always yield a byte-identical
``union_sites.tsv``. Conflicting rsIDs at the same site are resolved by a
fixed, bio-validator-approved rule (see ``build_union`` below) and every
collapsed conflict is logged to the audit TSV for forensics.

Three outputs are emitted (Plan §0a):

* ``--output`` — ``rsid<TAB>chrom<TAB>pos`` union, one row per ``(chrom, pos)``,
  sorted ``1``…``22``, ``X``, ``Y``, ``MT`` then by position.
* ``--audit-log`` — ``chrom<TAB>pos<TAB>winner_rsid<TAB>loser_rsid<TAB>reason``
  for every collapsed conflict.
* ``--report-json`` — counts, per-chrom + per-prefix distributions, input/output
  SHA-256s, git commit, and build date.

Tiered assertions run after the outputs are written so the report JSON is always
available for inspection: hard fails (``union_count``, ``intersection_count``,
``rs_count``, per-autosome + chrX floors, empty rsIDs) exit non-zero; warn-only
checks (chrY/chrMT floors, unknown rsID prefixes) log a ``WARN:`` line and
continue.

Usage::

    python scripts/build_union_catalog.py \\
      --twentythreeandme-sites twentythreeandme_v5_sites.tsv \\
      --ancestrydna-sites ancestrydna_v2_sites.tsv \\
      --vep-bundle-rsids existing_vep_bundle_rsids.tsv \\
      --output union_sites.tsv \\
      --audit-log union_sites_audit.tsv \\
      --report-json union_sites_report.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import OrderedDict, defaultdict
from collections.abc import Iterator
from datetime import date
from pathlib import Path

# Allow running from the repo root without installing the package.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.ingestion.base import SourceVendor  # noqa: E402
from backend.ingestion.chromosomes import normalize_for  # noqa: E402
from backend.ingestion.vcf_export import _chrom_sort_key  # noqa: E402

# Autosomes whose per-chrom count is a hard-fail floor (chr21 ~6k is smallest).
_AUTOSOMES: tuple[str, ...] = tuple(str(i) for i in range(1, 23))

# Permissive rsID shape; covers rs* (dbSNP), kgp* (AncestryDNA 1000G proxy),
# i* (23andMe internal), VG* (AncestryDNA legacy). Unknown prefixes warn only.
_KNOWN_RSID_RE = re.compile(r"^(rs|kgp|i|VG)\w+$")

# Production assertion thresholds (Plan §0a tiered-assertion table). Exposed as
# keyword args on ``check_assertions`` so tests can exercise the per-chrom logic
# with small fixtures.
DEFAULT_MIN_UNION = 800_000
DEFAULT_MIN_INTERSECTION = 400_000
DEFAULT_MIN_RS = 800_000
DEFAULT_MIN_AUTOSOME = 5_000
DEFAULT_MIN_CHRX = 5_000
DEFAULT_WARN_CHRY = 50
DEFAULT_WARN_CHRMT = 50


# ---------------------------------------------------------------------------
# Input loading
# ---------------------------------------------------------------------------


def _iter_vendor_rows(path: Path, vendor: SourceVendor) -> Iterator[tuple[str, str, int]]:
    """Yield ``(rsid, chrom, pos)`` from a per-vendor site TSV.

    The raw chromosome code is collapsed through ``normalize_for(vendor, …)``
    so 23andMe slot ``25`` lands on ``MT`` while AncestryDNA slot ``25`` lands
    on ``X`` (PAR) — running collapse per vendor *before* grouping is what keeps
    those divergent conventions correct. Blank and ``#``-comment lines are
    skipped. An empty rsID is an upstream parser bug and aborts the build.
    """
    with path.open(encoding="utf-8") as fh:
        for line_num, raw in enumerate(fh, start=1):
            line = raw.rstrip("\n\r")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 3:
                raise ValueError(
                    f"{path}:{line_num}: expected 3 tab-separated columns "
                    f"(rsid, chrom, pos), got {len(parts)}"
                )
            rsid, chrom_raw, pos_raw = (p.strip() for p in parts)
            if not rsid:
                raise ValueError(f"{path}:{line_num}: empty rsid")
            try:
                pos = int(pos_raw)
            except ValueError:
                raise ValueError(
                    f"{path}:{line_num}: non-numeric position {pos_raw!r}"
                ) from None
            chrom = normalize_for(vendor, chrom_raw)
            yield rsid, chrom, pos


def load_vendor_sites(path: Path, vendor: SourceVendor) -> list[tuple[str, str, int]]:
    """Load + chrom-collapse a per-vendor site TSV into ``(rsid, chrom, pos)``."""
    return list(_iter_vendor_rows(path, vendor))


def load_vep_bundle_rsids(path: Path) -> set[str]:
    """Load the optional VEP-bundle rsID set for conflict tie-breaking.

    Auto-detects the layout from the first non-blank line's column count:

    * **1 column** — one rsID per line.
    * **3 columns** — ``rsid<TAB>chrom<TAB>pos`` (the
      ``scripts/extract_vep_bundle_rsids.py`` Phase 0g output); only the first
      column is consumed.
    * **2 or > 3 columns** — aborts with a clear error.

    Either way only the rsID set is returned; chrom/pos are discarded (the
    tiebreak in :func:`build_union` is membership-only).
    """
    rsids: set[str] = set()
    detected_cols: int | None = None
    with path.open(encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n\r")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if detected_cols is None:
                detected_cols = len(parts)
                if detected_cols not in (1, 3):
                    raise ValueError(
                        f"unexpected --vep-bundle-rsids column count: {detected_cols}"
                    )
            rsids.add(parts[0].strip())
    return rsids


# ---------------------------------------------------------------------------
# Union merge + conflict resolution
# ---------------------------------------------------------------------------


def _resolve_conflict(
    rsids: list[str], vep_rsids: set[str]
) -> tuple[str, list[tuple[str, str]]]:
    """Pick the winning rsID for one ``(chrom, pos)`` and explain each loser.

    Resolution order (Plan §0a dedup rule):

    1. **rs\\* beats non-rs\\*** (rule 5, irrespective of step 4) — VEP can only
       annotate ``rs*`` IDs, so a ``kgp*``/``i*``/``VG*`` sibling can never win
       when an ``rs*`` ID is present; it becomes ``alt_rsid``.
    2. Among the surviving contenders, if exactly one is in the VEP bundle set
       (step 4a) it wins.
    3. Otherwise the lexicographically smallest contender wins (step 4b) —
       deterministic across re-runs.

    Returns ``(winner, [(loser, reason), …])`` with losers carrying the reason
    they lost for the audit log.
    """
    candidates = sorted(set(rsids))
    rs_candidates = [r for r in candidates if r.startswith("rs")]

    if rs_candidates and len(rs_candidates) < len(candidates):
        contenders = rs_candidates
        pruned_non_rs = {r for r in candidates if not r.startswith("rs")}
    else:
        contenders = candidates
        pruned_non_rs = set()

    vep_hits = [r for r in contenders if r in vep_rsids]
    if vep_rsids and len(vep_hits) == 1:
        winner = vep_hits[0]
        tiebreak_reason = "vep_bundle"
    else:
        winner = min(contenders)
        tiebreak_reason = "lexicographic"

    losers: list[tuple[str, str]] = []
    for loser in candidates:
        if loser == winner:
            continue
        reason = "rs_over_non_rs" if loser in pruned_non_rs else tiebreak_reason
        losers.append((loser, reason))
    return winner, losers


def build_union(
    v5_rows: list[tuple[str, str, int]],
    adna_rows: list[tuple[str, str, int]],
    vep_rsids: set[str] | None = None,
) -> tuple[list[tuple[str, str, int]], list[tuple[str, int, str, str, str]], int]:
    """Merge two per-vendor row lists into the deterministic union catalog.

    Groups every rsID (from both vendors) by ``(chrom, pos)``, resolves
    conflicts via :func:`_resolve_conflict`, and returns:

    * ``union_rows`` — ``(rsid, chrom, pos)`` sorted by ``(chrom, pos)`` using
      the canonical ``1``…``22``, ``X``, ``Y``, ``MT`` chromosome order.
    * ``audit_rows`` — ``(chrom, pos, winner, loser, reason)`` for every
      collapsed conflict, sorted ``(chrom, pos, loser)``.
    * ``conflict_count`` — number of sites that had ≥ 2 distinct rsIDs.
    """
    vep_rsids = vep_rsids or set()
    by_site: dict[tuple[str, int], set[str]] = defaultdict(set)
    for rsid, chrom, pos in v5_rows:
        by_site[(chrom, pos)].add(rsid)
    for rsid, chrom, pos in adna_rows:
        by_site[(chrom, pos)].add(rsid)

    union_rows: list[tuple[str, str, int]] = []
    audit_rows: list[tuple[str, int, str, str, str]] = []
    conflict_count = 0

    for (chrom, pos), rsids in by_site.items():
        if len(rsids) == 1:
            union_rows.append((next(iter(rsids)), chrom, pos))
            continue
        conflict_count += 1
        winner, losers = _resolve_conflict(list(rsids), vep_rsids)
        union_rows.append((winner, chrom, pos))
        for loser, reason in losers:
            audit_rows.append((chrom, pos, winner, loser, reason))

    union_rows.sort(key=lambda r: (_chrom_sort_key(r[1]), r[2]))
    audit_rows.sort(key=lambda r: (_chrom_sort_key(r[0]), r[1], r[3]))
    return union_rows, audit_rows, conflict_count


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _classify_prefix(rsid: str) -> str:
    """Bucket an rsID by known prefix; fall back to its leading letters."""
    if rsid.startswith("rs"):
        return "rs"
    if rsid.startswith("kgp"):
        return "kgp"
    if rsid.startswith("VG"):
        return "VG"
    if rsid.startswith("i"):
        return "i"
    match = re.match(r"^[A-Za-z]+", rsid)
    return match.group(0) if match else "other"


def build_report(
    v5_rows: list[tuple[str, str, int]],
    adna_rows: list[tuple[str, str, int]],
    union_rows: list[tuple[str, str, int]],
    conflict_count: int,
    *,
    sha256_inputs: dict[str, str],
    sha256_output: str,
    git_commit: str,
    build_date: str,
) -> dict:
    """Assemble the ``union_sites_report.json`` payload (counts + provenance)."""
    v5_positions = {(c, p) for _, c, p in v5_rows}
    adna_positions = {(c, p) for _, c, p in adna_rows}

    per_chrom: dict[str, int] = defaultdict(int)
    prefix_counts: dict[str, int] = defaultdict(int)
    for rsid, chrom, _ in union_rows:
        per_chrom[chrom] += 1
        prefix_counts[_classify_prefix(rsid)] += 1

    per_chrom_ordered = OrderedDict(
        (c, per_chrom[c]) for c in sorted(per_chrom, key=_chrom_sort_key)
    )
    prefix_ordered = OrderedDict(
        (k, prefix_counts[k]) for k in sorted(prefix_counts, key=lambda k: (-prefix_counts[k], k))
    )
    rs_count = prefix_counts.get("rs", 0)

    return {
        "input_counts": {
            "twentythreeandme": len(v5_positions),
            "ancestrydna": len(adna_positions),
        },
        "union_count": len(union_rows),
        "intersection_count": len(v5_positions & adna_positions),
        "collapsed_rsid_conflicts": conflict_count,
        "per_chrom_counts": per_chrom_ordered,
        "rsid_prefix_counts": prefix_ordered,
        "rs_count": rs_count,
        "non_rs_count": len(union_rows) - rs_count,
        "sha256_inputs": sha256_inputs,
        "sha256_output": sha256_output,
        "git_commit": git_commit,
        "build_date": build_date,
    }


# ---------------------------------------------------------------------------
# Assertions (tiered)
# ---------------------------------------------------------------------------


def check_assertions(
    report: dict,
    union_rows: list[tuple[str, str, int]],
    *,
    min_union: int = DEFAULT_MIN_UNION,
    min_intersection: int = DEFAULT_MIN_INTERSECTION,
    min_rs: int = DEFAULT_MIN_RS,
    min_autosome: int = DEFAULT_MIN_AUTOSOME,
    min_chrx: int = DEFAULT_MIN_CHRX,
    warn_chry: int = DEFAULT_WARN_CHRY,
    warn_chrmt: int = DEFAULT_WARN_CHRMT,
) -> tuple[list[str], list[str]]:
    """Evaluate the tiered assertions over a report.

    Returns ``(warnings, hard_failures)`` — neither raises. ``main`` exits
    non-zero when ``hard_failures`` is non-empty. Thresholds are keyword args so
    tests can drive the per-chrom logic with small fixtures.
    """
    warnings: list[str] = []
    hard_failures: list[str] = []
    per_chrom = report["per_chrom_counts"]

    if report["union_count"] < min_union:
        hard_failures.append(
            f"union_count {report['union_count']} < {min_union}"
        )
    if report["intersection_count"] < min_intersection:
        hard_failures.append(
            f"intersection_count {report['intersection_count']} < {min_intersection}"
        )
    if report["rs_count"] < min_rs:
        hard_failures.append(f"rs_count {report['rs_count']} < {min_rs}")

    for chrom in _AUTOSOMES:
        count = per_chrom.get(chrom, 0)
        if count < min_autosome:
            hard_failures.append(
                f"chromosome {chrom} count {count} < {min_autosome}"
            )

    chrx = per_chrom.get("X", 0)
    if chrx < min_chrx:
        hard_failures.append(f"chrX count {chrx} < {min_chrx}")

    chry = per_chrom.get("Y", 0)
    if chry < warn_chry:
        warnings.append(f"WARN: chrY count {chry} < {warn_chry}")

    chrmt = per_chrom.get("MT", 0)
    if chrmt < warn_chrmt:
        warnings.append(f"WARN: chrMT count {chrmt} < {warn_chrmt}")

    for rsid, chrom, pos in union_rows:
        if not rsid:
            hard_failures.append(f"empty rsid at {chrom}:{pos}")
        elif not _KNOWN_RSID_RE.match(rsid):
            warnings.append(
                f"WARN: rsid {rsid!r} at {chrom}:{pos} does not match "
                f"known prefix pattern (rs|kgp|i|VG)"
            )

    return warnings, hard_failures


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return "unknown"


def write_union(path: Path, union_rows: list[tuple[str, str, int]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for rsid, chrom, pos in union_rows:
            fh.write(f"{rsid}\t{chrom}\t{pos}\n")


def write_audit(path: Path, audit_rows: list[tuple[str, int, str, str, str]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        fh.write("chrom\tpos\twinner_rsid\tloser_rsid\treason\n")
        for chrom, pos, winner, loser, reason in audit_rows:
            fh.write(f"{chrom}\t{pos}\t{winner}\t{loser}\t{reason}\n")


def write_report(path: Path, report: dict) -> None:
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run(
    *,
    twentythreeandme_sites: Path,
    ancestrydna_sites: Path,
    output: Path,
    audit_log: Path,
    report_json: Path,
    vep_bundle_rsids: Path | None = None,
) -> tuple[dict, list[tuple[str, str, int]]]:
    """Load inputs, build the union, write all three outputs, return the report.

    Outputs are written *before* assertions run (caller's job) so the report
    JSON is always available for inspection even on a hard-fail build.
    """
    v5_rows = load_vendor_sites(twentythreeandme_sites, SourceVendor.TWENTYTHREEANDME)
    adna_rows = load_vendor_sites(ancestrydna_sites, SourceVendor.ANCESTRYDNA)

    sha256_inputs = {
        "twentythreeandme": _sha256_file(twentythreeandme_sites),
        "ancestrydna": _sha256_file(ancestrydna_sites),
    }
    vep_rsids: set[str] = set()
    if vep_bundle_rsids is not None:
        vep_rsids = load_vep_bundle_rsids(vep_bundle_rsids)
        sha256_inputs["vep_bundle_rsids"] = _sha256_file(vep_bundle_rsids)

    union_rows, audit_rows, conflict_count = build_union(v5_rows, adna_rows, vep_rsids)

    write_union(output, union_rows)
    write_audit(audit_log, audit_rows)

    report = build_report(
        v5_rows,
        adna_rows,
        union_rows,
        conflict_count,
        sha256_inputs=sha256_inputs,
        sha256_output=_sha256_file(output),
        git_commit=_git_commit(),
        build_date=date.today().isoformat(),
    )
    write_report(report_json, report)
    return report, union_rows


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build the bundle-v2.0.0 union site catalog from per-vendor "
            "(23andMe v5 + AncestryDNA v2.0) site TSVs."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  %(prog)s \\\n"
            "    --twentythreeandme-sites twentythreeandme_v5_sites.tsv \\\n"
            "    --ancestrydna-sites ancestrydna_v2_sites.tsv \\\n"
            "    --output union_sites.tsv \\\n"
            "    --audit-log union_sites_audit.tsv \\\n"
            "    --report-json union_sites_report.json\n"
        ),
    )
    parser.add_argument(
        "--twentythreeandme-sites",
        dest="twentythreeandme_sites",
        type=Path,
        required=True,
        help="23andMe v5 per-vendor site TSV (rsid<TAB>chrom_raw<TAB>pos).",
    )
    parser.add_argument(
        "--ancestrydna-sites",
        dest="ancestrydna_sites",
        type=Path,
        required=True,
        help="AncestryDNA v2.0 per-vendor site TSV (rsid<TAB>chrom_raw<TAB>pos).",
    )
    parser.add_argument(
        "--vep-bundle-rsids",
        dest="vep_bundle_rsids",
        type=Path,
        default=None,
        help=(
            "Optional VEP-bundle rsID list used to break rsID conflicts at a "
            "shared site. Auto-detects a 1-column (one rsid per line) or "
            "3-column (rsid<TAB>chrom<TAB>pos, from extract_vep_bundle_rsids.py) "
            "file; aborts on 2 or > 3 columns. Only the rsID set is consumed."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output union TSV path (rsid<TAB>chrom<TAB>pos).",
    )
    parser.add_argument(
        "--audit-log",
        dest="audit_log",
        type=Path,
        required=True,
        help="Output audit TSV of every collapsed rsID conflict.",
    )
    parser.add_argument(
        "--report-json",
        dest="report_json",
        type=Path,
        required=True,
        help="Output JSON summary (counts, distributions, SHA-256s).",
    )

    args = parser.parse_args(argv)

    for label, path in (
        ("--twentythreeandme-sites", args.twentythreeandme_sites),
        ("--ancestrydna-sites", args.ancestrydna_sites),
    ):
        if not path.exists():
            print(f"Error: {label} file not found: {path}", file=sys.stderr)
            sys.exit(1)
    if args.vep_bundle_rsids is not None and not args.vep_bundle_rsids.exists():
        print(
            f"Error: --vep-bundle-rsids file not found: {args.vep_bundle_rsids}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        report, union_rows = run(
            twentythreeandme_sites=args.twentythreeandme_sites,
            ancestrydna_sites=args.ancestrydna_sites,
            output=args.output,
            audit_log=args.audit_log,
            report_json=args.report_json,
            vep_bundle_rsids=args.vep_bundle_rsids,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    warnings, hard_failures = check_assertions(report, union_rows)

    for line in warnings:
        print(line, file=sys.stderr)

    print(f"Union sites:        {report['union_count']:,}", file=sys.stderr)
    print(f"Intersection:       {report['intersection_count']:,}", file=sys.stderr)
    print(f"Collapsed conflicts:{report['collapsed_rsid_conflicts']:,}", file=sys.stderr)
    print(
        f"rs / non-rs:        {report['rs_count']:,} / {report['non_rs_count']:,}",
        file=sys.stderr,
    )
    print(f"Output:             {args.output}", file=sys.stderr)

    if hard_failures:
        print("\nHARD-FAIL assertions:", file=sys.stderr)
        for line in hard_failures:
            print(f"  ✗ {line}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
