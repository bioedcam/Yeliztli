"""Tests for the Phase 0j coverage-report catalog parser.

Covers ``_load_catalog_rsids`` (1-col / 3-col auto-detect, rs-only filter,
column-count validation) and its wiring into ``coverage_report`` — see
plan §0j (``docs/bundle-v2.0.0-build-plan.md``). The headline regression is
that a 3-column ``union_sites.tsv`` yields *identical* coverage numbers to the
legacy 1-column catalog, instead of collapsing ``coverage_percent`` to 0%.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Import build script functions directly (mirrors test_build_vep_bundle.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from build_vep_bundle import _load_catalog_rsids, coverage_report

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
UNION_3COL_FIXTURE = FIXTURES_DIR / "union_sites_3col.tsv"

# VEP rows shared across the 1-col vs 3-col equivalence tests. rs1/rs2 are in
# the catalog, rs99 is not — so coverage is 2/5 = 40%.
_VEP_ROWS = [
    {"rsid": "rs1", "consequence": "missense_variant"},
    {"rsid": "rs2", "consequence": "intron_variant"},
    {"rsid": "rs99", "consequence": "synonymous_variant"},
]


class TestLoadCatalogRsids:
    def test_one_column(self, tmp_path: Path) -> None:
        catalog = tmp_path / "catalog_1col.txt"
        catalog.write_text("rs1\nrs2\nrs3\n")
        assert _load_catalog_rsids(catalog) == {"rs1", "rs2", "rs3"}

    def test_three_column(self, tmp_path: Path) -> None:
        catalog = tmp_path / "catalog_3col.tsv"
        catalog.write_text("rs1\t1\t100\nrs2\t1\t200\nrs3\t2\t300\n")
        # Only the first column is consumed; chrom/pos are discarded.
        assert _load_catalog_rsids(catalog) == {"rs1", "rs2", "rs3"}

    def test_skips_blank_and_comment_lines(self, tmp_path: Path) -> None:
        catalog = tmp_path / "catalog_comments.tsv"
        catalog.write_text("# header comment\n\nrs1\t1\t100\n\nrs2\t1\t200\n")
        assert _load_catalog_rsids(catalog) == {"rs1", "rs2"}

    def test_committed_union_fixture_non_empty(self) -> None:
        # Mirrors the Step 4 Done-check + Phase 0 DoD (plan line 711). The
        # fixture carries kgp*/i*/VG* IDs that must be filtered out.
        rsids = _load_catalog_rsids(UNION_3COL_FIXTURE)
        assert rsids == {
            "rs1801133",
            "rs429358",
            "rs7412",
            "rs1799945",
            "rs1800562",
        }
        assert rsids  # non-empty, per the Done-check command

    # Case: 2-column catalog → ValueError carrying the offending count.
    def test_two_columns_raises(self, tmp_path: Path) -> None:
        catalog = tmp_path / "catalog_2col.tsv"
        catalog.write_text("rs1\t1\nrs2\t1\n")
        with pytest.raises(ValueError, match="unexpected column count 2"):
            _load_catalog_rsids(catalog)

    # Case: 4-column catalog → ValueError carrying the offending count.
    def test_four_columns_raises(self, tmp_path: Path) -> None:
        catalog = tmp_path / "catalog_4col.tsv"
        catalog.write_text("rs1\t1\t100\tfoo\nrs2\t1\t200\tbar\n")
        with pytest.raises(ValueError, match="unexpected column count 4"):
            _load_catalog_rsids(catalog)

    # Case: mixed column counts (first row 3-col, later row 1-col) → raises
    # with BOTH counts in the message.
    def test_mixed_column_counts_raises(self, tmp_path: Path) -> None:
        catalog = tmp_path / "catalog_mixed.tsv"
        catalog.write_text("rs1\t1\t100\nrs2\n")
        with pytest.raises(ValueError, match=r"row column count 1 != header column count 3"):
            _load_catalog_rsids(catalog)

    # Case: rs-only filter — kgp*/i*/VG* IDs excluded from the set.
    def test_non_rs_ids_excluded(self, tmp_path: Path) -> None:
        catalog = tmp_path / "catalog_nonrs.tsv"
        catalog.write_text(
            "rs1\t1\t100\nkgp123\t2\t200\ni12345\t3\t300\nVG500\t4\t400\nrs2\t5\t500\n"
        )
        assert _load_catalog_rsids(catalog) == {"rs1", "rs2"}


class TestCoverageReportCatalogFormats:
    # Case 1: legacy 1-column catalog → correct hit/total/coverage_percent.
    def test_one_column_catalog(self, tmp_path: Path) -> None:
        catalog = tmp_path / "catalog.txt"
        catalog.write_text("rs1\nrs2\nrs3\nrs4\nrs5\n")
        report = coverage_report(_VEP_ROWS, catalog)
        assert report["catalog_size"] == 5
        assert report["catalog_covered"] == 2
        assert report["coverage_percent"] == 40.0

    # Case 2 (the auto-detect lock-in): same rsids in a 3-column TSV must
    # produce IDENTICAL numbers to the 1-column case.
    def test_three_column_catalog_identical_to_one_column(self, tmp_path: Path) -> None:
        one_col = tmp_path / "catalog_1col.txt"
        one_col.write_text("rs1\nrs2\nrs3\nrs4\nrs5\n")
        three_col = tmp_path / "catalog_3col.tsv"
        three_col.write_text("rs1\t1\t100\nrs2\t1\t200\nrs3\t2\t300\nrs4\t3\t400\nrs5\tX\t500\n")
        report_1 = coverage_report(_VEP_ROWS, one_col)
        report_3 = coverage_report(_VEP_ROWS, three_col)
        assert report_3["catalog_size"] == report_1["catalog_size"] == 5
        assert report_3["catalog_covered"] == report_1["catalog_covered"] == 2
        assert report_3["coverage_percent"] == report_1["coverage_percent"] == 40.0

    # Case 5 via the public entry point: non-rs IDs do not inflate the
    # denominator (catalog_size), so coverage_percent is unaffected by them.
    def test_non_rs_ids_excluded_from_denominator(self, tmp_path: Path) -> None:
        catalog = tmp_path / "catalog_nonrs.tsv"
        catalog.write_text("rs1\t1\t100\nrs2\t1\t200\nkgp99\t2\t300\ni777\t3\t400\nVG12\t4\t500\n")
        report = coverage_report(_VEP_ROWS, catalog)
        # Denominator is the 2 rs-prefix entries only, not 5.
        assert report["catalog_size"] == 2
        assert report["catalog_covered"] == 2
        assert report["coverage_percent"] == 100.0

    # Bad-format catalogs propagate the ValueError through coverage_report.
    def test_bad_column_count_propagates(self, tmp_path: Path) -> None:
        catalog = tmp_path / "catalog_2col.tsv"
        catalog.write_text("rs1\t1\nrs2\t1\n")
        with pytest.raises(ValueError, match="unexpected column count 2"):
            coverage_report(_VEP_ROWS, catalog)
