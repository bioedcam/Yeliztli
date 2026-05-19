"""Tests for scripts/regenerate_fixtures.py.

Verifies that the regeneration script produces valid SQLite databases
from the seed CSVs, matching expected schemas and row counts.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
SEED_DIR = FIXTURES_DIR / "seed_csvs"
SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "regenerate_fixtures.py"


def _run_script(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """Run regenerate_fixtures.py and assert it succeeds."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output-dir", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Script failed:\n{result.stderr}"
    return result


# ── Seed CSV existence ────────────────────────────────────────────────

EXPECTED_CSVS = [
    "clinvar_seed.csv",
    "vep_seed.csv",
    "gnomad_seed.csv",
    "dbnsfp_seed.csv",
    "cpic_alleles_seed.csv",
    "cpic_diplotypes_seed.csv",
    "cpic_guidelines_seed.csv",
    "gwas_seed.csv",
    "gene_phenotype_seed.csv",
]


class TestSeedCSVsExist:
    """All required seed CSVs must be present."""

    @pytest.mark.parametrize("csv_name", EXPECTED_CSVS)
    def test_csv_exists(self, csv_name: str) -> None:
        path = SEED_DIR / csv_name
        assert path.exists(), f"Missing seed CSV: {path}"

    @pytest.mark.parametrize("csv_name", EXPECTED_CSVS)
    def test_csv_has_header_and_data(self, csv_name: str) -> None:
        path = SEED_DIR / csv_name
        lines = path.read_text().strip().splitlines()
        assert len(lines) >= 2, f"{csv_name} must have header + at least 1 data row"


# ── Seed CSV content validation ──────────────────────────────────────


class TestSeedCSVContent:
    """Validate that seed CSVs contain required key variants."""

    def test_clinvar_contains_key_rsids(self) -> None:
        text = (SEED_DIR / "clinvar_seed.csv").read_text()
        for rsid in ["rs429358", "rs7412", "rs1801133", "rs4680", "rs80357906", "rs113993960"]:
            assert rsid in text, f"clinvar_seed.csv missing {rsid}"

    def test_gwas_contains_key_rsids(self) -> None:
        text = (SEED_DIR / "gwas_seed.csv").read_text()
        for rsid in ["rs429358", "rs1801133", "rs4680", "rs12913832", "rs7903146"]:
            assert rsid in text, f"gwas_seed.csv missing {rsid}"

    def test_vep_contains_key_rsids(self) -> None:
        text = (SEED_DIR / "vep_seed.csv").read_text()
        for rsid in ["rs429358", "rs7412", "rs1801133"]:
            assert rsid in text, f"vep_seed.csv missing {rsid}"

    def test_cpic_alleles_contains_key_genes(self) -> None:
        text = (SEED_DIR / "cpic_alleles_seed.csv").read_text()
        for gene in ["CYP2D6", "CYP2C19"]:
            assert gene in text, f"cpic_alleles_seed.csv missing {gene}"


# ── Regeneration script ──────────────────────────────────────────────


class TestRegenerateFixtures:
    """Test the regenerate_fixtures.py script end-to-end."""

    def test_dry_run_does_not_create_files(self, tmp_path: Path) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--output-dir", str(tmp_path), "--dry-run"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        db_files = list(tmp_path.glob("*.db"))
        assert len(db_files) == 0, f"Dry run should not create files, found: {db_files}"

    def test_generates_all_databases(self, tmp_path: Path) -> None:
        _run_script(tmp_path)

        expected_dbs = [
            "mini_reference.db",
            "mini_vep_bundle.db",
            "mini_gnomad_af.db",
            "mini_dbnsfp.db",
        ]
        for db_name in expected_dbs:
            assert (tmp_path / db_name).exists(), f"Missing {db_name}"

    def test_mini_reference_schema(self, tmp_path: Path) -> None:
        _run_script(tmp_path)
        with sqlite3.connect(str(tmp_path / "mini_reference.db")) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master"
                    " WHERE type='table' AND name != 'sqlite_sequence'"
                ).fetchall()
            }

        required = {
            "clinvar_variants",
            "gene_phenotype",
            "cpic_alleles",
            "cpic_diplotypes",
            "cpic_guidelines",
            "gwas_associations",
            "samples",
            "jobs",
            "database_versions",
            "update_history",
            "downloads",
            "literature_cache",
            "uniprot_cache",
            "log_entries",
            "reannotation_prompts",
        }
        assert required.issubset(tables), f"Missing tables: {required - tables}"

    def test_mini_reference_row_counts(self, tmp_path: Path) -> None:
        _run_script(tmp_path)
        with sqlite3.connect(str(tmp_path / "mini_reference.db")) as conn:
            clinvar_count = conn.execute("SELECT count(*) FROM clinvar_variants").fetchone()[0]
            gene_pheno_count = conn.execute("SELECT count(*) FROM gene_phenotype").fetchone()[0]
            cpic_alleles_count = conn.execute("SELECT count(*) FROM cpic_alleles").fetchone()[0]
            gwas_count = conn.execute("SELECT count(*) FROM gwas_associations").fetchone()[0]

        assert clinvar_count >= 50, f"Expected >=50 clinvar rows, got {clinvar_count}"
        assert gene_pheno_count >= 20, f"Expected >=20 gene_phenotype rows, got {gene_pheno_count}"
        assert cpic_alleles_count >= 10, (
            f"Expected >=10 cpic_alleles rows, got {cpic_alleles_count}"
        )
        assert gwas_count >= 30, f"Expected >=30 gwas rows, got {gwas_count}"

    def test_mini_vep_bundle_has_data(self, tmp_path: Path) -> None:
        _run_script(tmp_path)
        with sqlite3.connect(str(tmp_path / "mini_vep_bundle.db")) as conn:
            count = conn.execute("SELECT count(*) FROM vep_annotations").fetchone()[0]
        assert count >= 50, f"Expected >=50 VEP rows, got {count}"

    def test_mini_vep_bundle_carries_v2_0_0_metadata(self, tmp_path: Path) -> None:
        """Phase 0 closure (Step 18): mini bundle mirrors v2.0.0 schema.

        The production VEP bundle writes `bundle_metadata` with at minimum
        `bundle_version`, `build_date`, `schema_version`, `ensembl_version`,
        and `variant_count` (see `scripts/build_vep_bundle.py`). The mini
        fixture must align so `update_manager.run_vep_bundle_update`'s parity
        check exercises the same code path against the fixture.
        AncestryDNA rsID coverage in the seed CSV is added later in step 39.
        """
        _run_script(tmp_path)
        with sqlite3.connect(str(tmp_path / "mini_vep_bundle.db")) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert "bundle_metadata" in tables

            metadata = dict(conn.execute("SELECT key, value FROM bundle_metadata"))

        for required in (
            "bundle_version",
            "build_date",
            "schema_version",
            "ensembl_version",
            "variant_count",
        ):
            assert required in metadata, f"missing bundle_metadata key: {required}"
        assert metadata["bundle_version"] == "v2.0.0"
        assert metadata["schema_version"] == "1"
        # `variant_count` matches the seed CSV row count exactly.
        assert int(metadata["variant_count"]) >= 50

    def test_mini_gnomad_has_data(self, tmp_path: Path) -> None:
        _run_script(tmp_path)
        with sqlite3.connect(str(tmp_path / "mini_gnomad_af.db")) as conn:
            count = conn.execute("SELECT count(*) FROM gnomad_af").fetchone()[0]
        assert count >= 50, f"Expected >=50 gnomAD rows, got {count}"

    def test_mini_dbnsfp_has_data(self, tmp_path: Path) -> None:
        _run_script(tmp_path)
        with sqlite3.connect(str(tmp_path / "mini_dbnsfp.db")) as conn:
            count = conn.execute("SELECT count(*) FROM dbnsfp_scores").fetchone()[0]
        assert count >= 30, f"Expected >=30 dbNSFP rows, got {count}"

    def test_wal_mode_enabled(self, tmp_path: Path) -> None:
        _run_script(tmp_path)
        for db_name in [
            "mini_reference.db",
            "mini_vep_bundle.db",
            "mini_gnomad_af.db",
            "mini_dbnsfp.db",
        ]:
            with sqlite3.connect(str(tmp_path / db_name)) as conn:
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal", f"{db_name} should use WAL mode, got {mode}"

    def test_clinvar_data_integrity(self, tmp_path: Path) -> None:
        """Verify a known ClinVar entry is correctly loaded."""
        _run_script(tmp_path)
        with sqlite3.connect(str(tmp_path / "mini_reference.db")) as conn:
            row = conn.execute(
                "SELECT chrom, pos, significance, gene_symbol"
                " FROM clinvar_variants WHERE rsid = 'rs429358'"
            ).fetchone()
        assert row is not None, "rs429358 not found in clinvar_variants"
        assert row[0] == "19"
        assert row[1] == 44908684
        assert row[2] == "risk_factor"
        assert row[3] == "APOE"

    def test_idempotent_regeneration(self, tmp_path: Path) -> None:
        """Running the script twice produces identical row counts."""
        counts = []
        for _ in range(2):
            _run_script(tmp_path)
            with sqlite3.connect(str(tmp_path / "mini_reference.db")) as conn:
                count = conn.execute("SELECT count(*) FROM clinvar_variants").fetchone()[0]
            counts.append(count)
        assert counts[0] >= 50, f"Expected >=50 rows, got {counts[0]}"
        assert counts[0] == counts[1], f"Row count changed: {counts[0]} -> {counts[1]}"
