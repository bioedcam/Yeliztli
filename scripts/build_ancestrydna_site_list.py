#!/usr/bin/env python3
"""Build the AncestryDNA v2.0 per-vendor site list from N raw exports (Plan §0b).

Unions several AncestryDNA ``*.txt`` raw exports into a single per-vendor site
TSV — the ``rsid<TAB>chrom<TAB>pos`` catalog that ``build_union_catalog.py``
(Phase A4) later merges with the 23andMe v5 catalog. Pooling ~5 representative
exports plus the operator's local export cancels out per-user no-call gaps, so
the result approximates the full AncestryDNA v2.0 chip site list (~700k sites).

Pipeline (Plan §0b):

1. Parse each input through :func:`backend.ingestion.dispatcher.parse`, which
   re-uses the AncestryDNA parser (CRLF handling, header-variant detection,
   indel/no-call canonicalization). Every data line yields a ``ParsedVariant``.
2. For each ``ParsedVariant`` emit ``(rsid, chrom, pos)``. **This script applies
   no chromosome normalization of its own** — the per-vendor catalog is left for
   ``build_union_catalog.py`` to collapse, which owns the per-vendor PAR/MT
   logic.

   *Reconciliation note.* ``parser_ancestrydna`` already runs the AncestryDNA
   collapse (``23→X``, ``24→Y``, ``25→X`` PAR, ``26→MT``) inside
   ``ParsedVariant.chrom``, so the emitted chrom is canonical
   (``1``…``22``/``X``/``Y``/``MT``), not a raw ``23``/``25``/``26`` code. That
   is harmless: ``build_union_catalog.py`` re-runs
   ``normalize_for(ANCESTRYDNA, …)`` over these values, and the AncestryDNA map
   is a no-op on the already-canonical set — so the final union is byte-identical
   to feeding raw codes. The invariant the plan cares about ("this builder does
   not own the collapse") holds: this script never normalizes; it passes the
   parser's chrom through verbatim.
3. Take the **union** of ``(rsid, chrom, pos)`` tuples across all inputs (a site
   called in *any* export belongs to the chip's site list). Conflicting rsIDs at
   one ``(chrom, pos)`` are *not* resolved here — both tuples survive into the
   per-vendor TSV and ``build_union_catalog.py`` applies the dedup rule.
4. Sort by ``(chrom, pos, rsid)`` using the canonical ``1``…``22``, ``X``, ``Y``,
   ``MT`` chromosome order for a deterministic, byte-stable output.
5. Assert ``union_count ≥ 690_000`` (the AncestryDNA v2.0 chip is ~700k; tolerate
   roughly -1.5%). Below the floor is a hard fail (non-zero exit).

The same pattern can build the 23andMe v5 catalog if the cluster copy is ever
lost (the dispatcher auto-detects vendor); that is out of scope for v2.0.0 —
see Phase A1.

**PII:** the input exports are individually identifiable. This script writes
only ``(rsid, chrom, pos)`` site tuples and SHA-256/count provenance — never row
genotypes and never input file paths (which could embed an operator username or
donor ID); each input is named in the report by an ordinal ``input_N`` label
plus its SHA-256. Never commit the exports or the per-vendor TSV; keep both
inside the operator-controlled ``$WORKDIR``.

Usage::

    python scripts/build_ancestrydna_site_list.py \\
      --input AncestryDNA.txt \\
      --input arvados_export_1.txt \\
      --input arvados_export_2.txt \\
      --output ancestrydna_v2_sites.tsv \\
      --report-json ancestrydna_v2_report.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import OrderedDict, defaultdict
from datetime import date
from pathlib import Path

# Allow running from the repo root without installing the package.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.ingestion.base import ParserError, ParseResult  # noqa: E402
from backend.ingestion.dispatcher import parse  # noqa: E402
from backend.ingestion.vcf_export import _chrom_sort_key  # noqa: E402

# AncestryDNA v2.0 chip is ~700k sites; tolerate ~-1.5% across the pooled exports.
DEFAULT_MIN_UNION = 690_000


# ---------------------------------------------------------------------------
# Input loading
# ---------------------------------------------------------------------------


def sites_from_result(result: ParseResult) -> set[tuple[str, str, int]]:
    """Project a :class:`ParseResult` onto its ``(rsid, chrom, pos)`` site set.

    Genotypes (including no-calls) are dropped — a position is part of the chip's
    site list whether or not this particular donor had a successful call there.
    ``chrom`` is the parser's already-canonical value (see the module docstring
    reconciliation note); this function applies no further normalization.
    """
    return {(v.rsid, v.chrom, v.pos) for v in result.variants}


def build_union(
    inputs: list[Path],
) -> tuple[list[tuple[str, str, int]], list[dict]]:
    """Parse every export and union their site tuples into the catalog rows.

    Returns ``(union_rows, per_input)`` where ``union_rows`` is the deduplicated
    ``(rsid, chrom, pos)`` union sorted by ``(chrom, pos, rsid)`` and
    ``per_input`` carries one provenance dict per input (SHA-256 + counts only,
    no row content). Any :class:`ParserError` from the dispatcher propagates so
    the caller can report the offending file/line.
    """
    union: set[tuple[str, str, int]] = set()
    per_input: list[dict] = []
    for i, path in enumerate(inputs, start=1):
        result = parse(path)
        sites = sites_from_result(result)
        union |= sites
        # PII: the report is a host-leaving provenance artifact, so the input is
        # identified only by an ordinal label + its SHA-256 (the canonical
        # cross-reference recorded in the audit log) — never by its file path,
        # which could embed an operator username or donor ID (Plan §0b PII rule;
        # mirrors build_union_catalog.py's "SHA-256s and counts only").
        per_input.append(
            {
                "label": f"input_{i}",
                "sha256": _sha256_file(path),
                "vendor": result.vendor.value,
                "version": result.version,
                "variant_count": len(result.variants),
                "nocall_count": result.nocall_count,
                "site_count": len(sites),
            }
        )

    union_rows = sorted(union, key=lambda r: (_chrom_sort_key(r[1]), r[2], r[0]))
    return union_rows, per_input


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _classify_prefix(rsid: str) -> str:
    """Bucket an rsID by known prefix; fall back to its leading letters.

    Mirrors ``build_union_catalog.py`` so the two reports speak the same
    vocabulary (rs / kgp / VG / i, else the leading-letter run).
    """
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
    union_rows: list[tuple[str, str, int]],
    per_input: list[dict],
    *,
    sha256_output: str,
    git_commit: str,
    build_date: str,
) -> dict:
    """Assemble the ``ancestrydna_v2_report.json`` payload (counts + provenance)."""
    per_chrom: dict[str, int] = defaultdict(int)
    prefix_counts: dict[str, int] = defaultdict(int)
    for rsid, chrom, _ in union_rows:
        per_chrom[chrom] += 1
        prefix_counts[_classify_prefix(rsid)] += 1

    per_chrom_ordered = OrderedDict(
        (c, per_chrom[c]) for c in sorted(per_chrom, key=_chrom_sort_key)
    )
    prefix_ordered = OrderedDict(
        (k, prefix_counts[k])
        for k in sorted(prefix_counts, key=lambda k: (-prefix_counts[k], k))
    )

    return {
        "input_count": len(per_input),
        "input_files": per_input,
        "union_count": len(union_rows),
        "per_chrom_counts": per_chrom_ordered,
        "rsid_prefix_counts": prefix_ordered,
        "sha256_output": sha256_output,
        "git_commit": git_commit,
        "build_date": build_date,
    }


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------


def check_union_floor(
    union_count: int, *, min_union: int = DEFAULT_MIN_UNION
) -> list[str]:
    """Return hard-failure messages if ``union_count`` is below the chip floor.

    Empty list means the catalog cleared the floor. The threshold is a keyword
    arg so callers/tests can exercise the gate; production uses the
    ``DEFAULT_MIN_UNION`` (690k) AncestryDNA v2.0 floor.
    """
    if union_count < min_union:
        return [f"union_count {union_count} < {min_union}"]
    return []


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


def write_report(path: Path, report: dict) -> None:
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run(
    *,
    inputs: list[Path],
    output: Path,
    report_json: Path,
) -> tuple[dict, list[tuple[str, str, int]]]:
    """Parse inputs, union sites, write both outputs, return ``(report, rows)``.

    Outputs are written *before* the caller runs :func:`check_union_floor` so the
    report JSON is always available for inspection — even on a below-floor build.
    """
    union_rows, per_input = build_union(inputs)

    write_union(output, union_rows)
    report = build_report(
        union_rows,
        per_input,
        sha256_output=_sha256_file(output),
        git_commit=_git_commit(),
        build_date=date.today().isoformat(),
    )
    write_report(report_json, report)
    return report, union_rows


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Union N AncestryDNA raw exports into the AncestryDNA v2.0 "
            "per-vendor site TSV consumed by build_union_catalog.py."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  %(prog)s \\\n"
            "    --input AncestryDNA.txt \\\n"
            "    --input arvados_export_1.txt \\\n"
            "    --input arvados_export_2.txt \\\n"
            "    --output ancestrydna_v2_sites.tsv \\\n"
            "    --report-json ancestrydna_v2_report.json\n"
        ),
    )
    parser.add_argument(
        "--input",
        dest="inputs",
        action="append",
        type=Path,
        required=True,
        metavar="EXPORT",
        help=(
            "An AncestryDNA raw export (*.txt). Repeat once per export; all are "
            "unioned. At least one is required."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output per-vendor site TSV path (rsid<TAB>chrom<TAB>pos).",
    )
    parser.add_argument(
        "--report-json",
        dest="report_json",
        type=Path,
        required=True,
        help="Output JSON summary (counts, per-chrom distribution, SHA-256s).",
    )

    args = parser.parse_args(argv)

    for path in args.inputs:
        if not path.exists():
            print(f"Error: --input file not found: {path}", file=sys.stderr)
            sys.exit(1)

    try:
        report, _ = run(
            inputs=args.inputs,
            output=args.output,
            report_json=args.report_json,
        )
    except ParserError as exc:
        print(f"Error: failed to parse an input export: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Inputs:       {report['input_count']}", file=sys.stderr)
    print(f"Union sites:  {report['union_count']:,}", file=sys.stderr)
    print(f"Output:       {args.output}", file=sys.stderr)

    hard_failures = check_union_floor(report["union_count"])
    if hard_failures:
        print("\nHARD-FAIL assertions:", file=sys.stderr)
        for line in hard_failures:
            print(f"  ✗ {line}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
