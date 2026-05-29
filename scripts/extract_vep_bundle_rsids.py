#!/usr/bin/env python3
"""Extract the 23andMe v5 site list from the v1.0.0 VEP bundle (Plan §0g).

The v1.0.0 VEP bundle (`vep_bundle.db`, GitHub release ``bundle-v1.0.0``) was
built against the full 23andMe v5 catalog **including X/Y/MT**, so its
``vep_annotations.rsid`` column is an authoritative v5 site list. Phase A1 uses
this script to recover that site list instead of rsync'ing v1.1's
``array_sites_grch37.tsv`` from the cluster — that file is the output of v1.1's
``02_prepare_sites.sh``, which filters to autosomes only (``$2 ~ /^[0-9]+$/``)
and would silently drop every sex/mitochondrial v5 site.

Pipeline (Plan §0g):

1. Open the bundle SQLite **read-only** (``file:…?mode=ro``).
2. Read ``bundle_metadata`` and assert ``bundle_version`` starts with ``v1.0`` —
   refuse to extract from a non-v1.0 bundle (guards against accidentally seeding
   v2 from v2).
3. ``SELECT DISTINCT rsid, chrom, pos FROM vep_annotations`` (skipping NULL/empty
   rsIDs). ``vep_annotations`` is the canonical table
   (``build_vep_bundle.py::TABLE_NAME``). We deliberately **do not** ``ORDER BY``
   in SQL — SQLite lex-sorts ``chrom`` (``"1","10","11"…"2"``), which would
   interleave chromosomes.
4. Sort the rows in Python with
   :func:`backend.ingestion.vcf_export._chrom_sort_key` as the chrom key (the
   same canonical primitive ``generate_vep_input.py`` uses), then by ``pos``
   ascending, then ``rsid`` as a final tiebreak for full determinism. Emit
   ``rsid<TAB>chrom<TAB>pos``. Autosomes (``1``…``22``) order identically to
   ``sort -k2,2V -k3,3n``; the sex/mitochondrial contigs follow the project's
   canonical order ``X < Y < MT`` (which differs from ``sort -V``'s alphabetical
   ``MT < X < Y``) so the catalog stays consistent with the rest of the pipeline.
5. Write ``--report-json`` with provenance + per-chrom counts.

Assertions (the script aborts non-zero):

- ``bundle_version`` starts with ``v1.0`` (else a hard refusal before extraction).
- ``row_count >= 600_000`` (the v1.0.0 bundle covers ~605k v5 sites).
- ``per_chrom_counts`` carries all of ``1``…``22``, ``X``, ``Y``, ``MT`` (any
  missing → upstream bundle corruption).
- ``per_chrom_counts["MT"] >= 30`` (sanity floor; v1.0.0 typically has ~85).

The bundle is opened read-only and never modified. Only the resulting site TSV
and SHA-256/count provenance leave the host — no genotypes are involved (the
VEP bundle is a sites-only annotation table, not a donor genotype file).

Usage::

    python scripts/extract_vep_bundle_rsids.py \\
      --vep-bundle vep_bundle_v1.0.0.db \\
      --output twentythreeandme_v5_sites.tsv \\
      --report-json twentythreeandme_v5_report.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from collections import OrderedDict, defaultdict
from datetime import date
from pathlib import Path

# Allow running from the repo root without installing the package.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.ingestion.vcf_export import _chrom_sort_key  # noqa: E402

# Canonical table holding the VEP annotations (see build_vep_bundle.py::TABLE_NAME).
TABLE_NAME = "vep_annotations"

# The v1.0.0 bundle covers ~605k 23andMe v5 sites incl. X/Y/MT.
DEFAULT_MIN_ROWS = 600_000
# Sanity floor for the mitochondrial contig (v1.0.0 typically has ~85 MT rows).
DEFAULT_MIN_MT = 30
# Every chromosome the 23andMe v5 catalog (and thus the v1.0.0 bundle) must cover.
REQUIRED_CHROMS: frozenset[str] = frozenset(
    [str(i) for i in range(1, 23)] + ["X", "Y", "MT"]
)


class ExtractError(RuntimeError):
    """Raised when the source bundle cannot be used as a v5 site source.

    Covers a missing/non-``v1.0`` ``bundle_version`` and an empty/absent
    ``vep_annotations`` table — structural problems that mean extraction cannot
    proceed at all (distinct from the post-extraction quality floors, which are
    surfaced as :func:`check_floors` messages so the report is still written).
    """


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def _read_bundle_version(conn: sqlite3.Connection) -> str | None:
    """Return ``bundle_metadata.bundle_version`` or ``None`` if absent.

    Tolerates a bundle with no ``bundle_metadata`` table (returns ``None``) so
    the caller can raise a single, clear :class:`ExtractError`.
    """
    try:
        row = conn.execute(
            "SELECT value FROM bundle_metadata WHERE key = 'bundle_version'"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row and row[0] else None


def extract_sites(
    bundle_path: Path,
) -> tuple[list[tuple[str, str, int]], str, str]:
    """Extract the distinct ``(rsid, chrom, pos)`` site list from the bundle.

    Returns ``(rows, bundle_version, source_sha256)`` where ``rows`` is sorted by
    ``(chrom, pos, rsid)`` using the canonical chromosome order. Raises
    :class:`ExtractError` if the bundle is not a ``v1.0`` build or holds no
    annotated sites. The connection is opened read-only and never written.
    """
    source_sha256 = _sha256_file(bundle_path)
    # Build the URI from the resolved path via as_uri(), which percent-encodes
    # any '?', '#', or '%' in the path. A naive f"file:{path}?mode=ro" would let
    # SQLite treat those as URI delimiters and silently open the wrong file.
    try:
        conn = sqlite3.connect(f"{bundle_path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:
        raise ExtractError(
            f"{bundle_path}: could not open the bundle read-only ({exc})."
        ) from exc
    try:
        version = _read_bundle_version(conn)
        if version is None:
            raise ExtractError(
                f"{bundle_path}: bundle_metadata.bundle_version is missing; "
                "cannot confirm this is a v1.0 bundle."
            )
        if not version.startswith("v1.0"):
            raise ExtractError(
                f"{bundle_path}: refusing to extract from bundle_version "
                f"{version!r} — only v1.0 bundles are an authoritative v5 site "
                "source (guards against seeding v2 from v2)."
            )

        try:
            cursor = conn.execute(
                f"SELECT DISTINCT rsid, chrom, pos FROM {TABLE_NAME} "
                "WHERE rsid IS NOT NULL AND rsid != ''"
            )
            raw_rows = cursor.fetchall()
        except sqlite3.OperationalError as exc:
            raise ExtractError(
                f"{bundle_path}: could not read {TABLE_NAME!r} ({exc})."
            ) from exc
    finally:
        conn.close()

    if not raw_rows:
        raise ExtractError(
            f"{bundle_path}: {TABLE_NAME!r} yielded no rsID-bearing rows — "
            "the bundle is empty or corrupt."
        )

    rows = [(str(rsid), str(chrom), int(pos)) for rsid, chrom, pos in raw_rows]
    # Python-side sort: a SQLite ORDER BY would lex-sort chrom and interleave
    # chromosomes ("1","10","11"…"2"). We use the canonical _chrom_sort_key
    # (X < Y < MT) so the order matches the rest of the pipeline; rsid is the
    # final tiebreak so equal (chrom, pos) rows are byte-stable regardless of the
    # order SELECT DISTINCT happens to return them in.
    rows.sort(key=lambda r: (_chrom_sort_key(r[1]), r[2], r[0]))
    return rows, version, source_sha256


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def build_report(
    rows: list[tuple[str, str, int]],
    *,
    source_bundle_version: str,
    source_bundle_sha256: str,
    output_sha256: str,
    git_commit: str,
    build_date: str,
) -> dict:
    """Assemble the ``twentythreeandme_v5_report.json`` payload."""
    per_chrom: dict[str, int] = defaultdict(int)
    for _, chrom, _ in rows:
        per_chrom[chrom] += 1
    per_chrom_ordered = OrderedDict(
        (c, per_chrom[c]) for c in sorted(per_chrom, key=_chrom_sort_key)
    )

    return {
        "source_bundle_sha256": source_bundle_sha256,
        "source_bundle_version": source_bundle_version,
        "row_count": len(rows),
        "per_chrom_counts": per_chrom_ordered,
        "output_sha256": output_sha256,
        "git_commit": git_commit,
        "build_date": build_date,
    }


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------


def check_floors(
    row_count: int,
    per_chrom_counts: dict[str, int],
    *,
    min_rows: int = DEFAULT_MIN_ROWS,
    min_mt: int = DEFAULT_MIN_MT,
) -> list[str]:
    """Return hard-failure messages for the post-extraction quality gates.

    An empty list means every gate cleared. Thresholds are keyword args so
    tests can exercise the gates on small fixtures; production uses the
    ``DEFAULT_MIN_ROWS`` (600k) / ``DEFAULT_MIN_MT`` (30) floors.
    """
    failures: list[str] = []
    if row_count < min_rows:
        failures.append(f"row_count {row_count} < {min_rows}")
    missing = sorted(REQUIRED_CHROMS - set(per_chrom_counts), key=_chrom_sort_key)
    if missing:
        failures.append(f"missing chromosomes: {', '.join(missing)}")
    mt = per_chrom_counts.get("MT", 0)
    if mt < min_mt:
        failures.append(f"MT count {mt} < {min_mt}")
    return failures


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


def write_sites(path: Path, rows: list[tuple[str, str, int]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for rsid, chrom, pos in rows:
            fh.write(f"{rsid}\t{chrom}\t{pos}\n")


def write_report(path: Path, report: dict) -> None:
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run(
    *,
    vep_bundle: Path,
    output: Path,
    report_json: Path,
) -> tuple[dict, list[tuple[str, str, int]]]:
    """Extract sites, write both outputs, return ``(report, rows)``.

    Outputs are written *before* the caller runs :func:`check_floors` so the
    report JSON is always available for inspection — even on a below-floor
    extraction. The structural :class:`ExtractError` checks (bad version / empty
    table) fire inside :func:`extract_sites` and abort before any output.
    """
    rows, version, source_sha256 = extract_sites(vep_bundle)

    write_sites(output, rows)
    report = build_report(
        rows,
        source_bundle_version=version,
        source_bundle_sha256=source_sha256,
        output_sha256=_sha256_file(output),
        git_commit=_git_commit(),
        build_date=date.today().isoformat(),
    )
    write_report(report_json, report)
    return report, rows


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Extract the 23andMe v5 site list (incl. X/Y/MT) from the v1.0.0 "
            "VEP bundle into a rsid<TAB>chrom<TAB>pos catalog."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  %(prog)s \\\n"
            "    --vep-bundle vep_bundle_v1.0.0.db \\\n"
            "    --output twentythreeandme_v5_sites.tsv \\\n"
            "    --report-json twentythreeandme_v5_report.json\n"
        ),
    )
    parser.add_argument(
        "--vep-bundle",
        dest="vep_bundle",
        type=Path,
        required=True,
        metavar="BUNDLE_DB",
        help="Path to the v1.0.0 vep_bundle.db (read-only).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output site TSV path (rsid<TAB>chrom<TAB>pos).",
    )
    parser.add_argument(
        "--report-json",
        dest="report_json",
        type=Path,
        required=True,
        help="Output JSON summary (provenance, per-chrom distribution).",
    )

    args = parser.parse_args(argv)

    if not args.vep_bundle.exists():
        print(f"Error: --vep-bundle not found: {args.vep_bundle}", file=sys.stderr)
        sys.exit(1)

    try:
        report, _ = run(
            vep_bundle=args.vep_bundle,
            output=args.output,
            report_json=args.report_json,
        )
    except ExtractError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Source bundle: {report['source_bundle_version']}", file=sys.stderr)
    print(f"Sites:         {report['row_count']:,}", file=sys.stderr)
    print(f"Output:        {args.output}", file=sys.stderr)

    hard_failures = check_floors(report["row_count"], report["per_chrom_counts"])
    if hard_failures:
        print("\nHARD-FAIL assertions:", file=sys.stderr)
        for line in hard_failures:
            print(f"  ✗ {line}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
