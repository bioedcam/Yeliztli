"""Tests for dbNSFP SQLite loader (P2-11).

Covers:
- T2-11: dbNSFP lookup returns correct CADD, REVEL scores for rs1801133 (MTHFR C677T)
- TSV line parsing (valid, missing fields, no scores, invalid chrom)
- Multi-transcript score parsing (semicolon-separated values)
- CSV loading into dbnsfp_scores table
- Batch lookup by rsid and by (chrom, pos, ref, alt)
- Table creation and index creation
- Version recording in database_versions
- Download function structure
- Ensemble pathogenicity helpers (count_deleterious, is_ensemble_pathogenic)
- LoadStats dataclass
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from backend.annotation.dbnsfp import (
    BATCH_SIZE,
    DBNSFP_BITMASK,
    DBNSFP_FIELDS,
    LOOKUP_BATCH_SIZE,
    DbNSFPAnnotation,
    DbNSFPRecord,
    LoadStats,
    _normalize_chrom,
    _parse_dbnsfp_float,
    _parse_dbnsfp_pred,
    _parse_float,
    count_deleterious,
    create_dbnsfp_tables,
    is_ensemble_pathogenic,
    load_dbnsfp_from_csv,
    lookup_dbnsfp_by_positions,
    lookup_dbnsfp_by_rsids,
    parse_dbnsfp_tsv_line,
    record_dbnsfp_version,
)
from backend.db.tables import database_versions, reference_metadata

# ── Fixtures ────────────────────────────────────────────────────────────

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
DBNSFP_SEED_CSV = FIXTURES_DIR / "seed_csvs" / "dbnsfp_seed.csv"


@pytest.fixture
def dbnsfp_engine() -> sa.Engine:
    """In-memory dbNSFP engine with tables created."""
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_dbnsfp_tables(engine)
    return engine


@pytest.fixture
def dbnsfp_engine_with_data(dbnsfp_engine: sa.Engine) -> sa.Engine:
    """dbNSFP engine loaded from seed CSV."""
    load_dbnsfp_from_csv(DBNSFP_SEED_CSV, dbnsfp_engine, clear_existing=False)
    return dbnsfp_engine


@pytest.fixture
def reference_engine() -> sa.Engine:
    """In-memory reference engine for version tracking."""
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    reference_metadata.create_all(engine)
    return engine


# ── Helper parsing tests ─────────────────────────────────────────────────


class TestParseHelpers:
    def test_normalize_chrom_valid(self):
        assert _normalize_chrom("1") == "1"
        assert _normalize_chrom("chr1") == "1"
        assert _normalize_chrom("chrX") == "X"
        assert _normalize_chrom("22") == "22"
        assert _normalize_chrom("MT") == "MT"

    def test_normalize_chrom_invalid(self):
        assert _normalize_chrom("chr0") is None
        assert _normalize_chrom("chrUn") is None
        assert _normalize_chrom("") is None

    def test_parse_float_valid(self):
        assert _parse_float("1.5") == 1.5
        assert _parse_float("0") == 0.0
        assert _parse_float("-1.2") == -1.2

    def test_parse_float_missing(self):
        assert _parse_float(None) is None
        assert _parse_float(".") is None
        assert _parse_float("") is None
        assert _parse_float("-") is None

    def test_parse_dbnsfp_float_single(self):
        assert _parse_dbnsfp_float("0.5") == 0.5

    def test_parse_dbnsfp_float_multi_transcript(self):
        """dbNSFP stores multiple scores separated by semicolons."""
        assert _parse_dbnsfp_float("0.3;0.5;0.7") == 0.3
        assert _parse_dbnsfp_float(".;0.5;0.7") == 0.5
        assert _parse_dbnsfp_float(".;.;0.7") == 0.7

    def test_parse_dbnsfp_float_all_missing(self):
        assert _parse_dbnsfp_float(".;.;.") is None
        assert _parse_dbnsfp_float(".") is None

    def test_parse_dbnsfp_pred_single(self):
        assert _parse_dbnsfp_pred("D") == "D"
        assert _parse_dbnsfp_pred("T") == "T"

    def test_parse_dbnsfp_pred_multi(self):
        assert _parse_dbnsfp_pred("D;T;D") == "D"
        assert _parse_dbnsfp_pred(".;T;D") == "T"

    def test_parse_dbnsfp_pred_missing(self):
        assert _parse_dbnsfp_pred(None) is None
        assert _parse_dbnsfp_pred(".") is None
        assert _parse_dbnsfp_pred("") is None


# ── TSV line parsing tests ───────────────────────────────────────────────


class TestParseDbnsfpTsvLine:
    """Test TSV line parsing from dbNSFP dict format."""

    def _make_fields(self, **overrides) -> dict[str, str]:
        """Build a minimal valid dbNSFP TSV row dict."""
        base = {
            "#chr": "19",
            "pos(1-based)": "44908684",
            "ref": "T",
            "alt": "C",
            "rs_dbSNP": "rs429358",
            "CADD_phred": "28.3",
            "SIFT4G_score": "0.001",
            "SIFT4G_pred": "D",
            "Polyphen2_HVAR_score": "0.998",
            "Polyphen2_HVAR_pred": "D",
            "REVEL_score": "0.812",
            "MutPred_score": "0.780",
            "VEST4_score": "0.891",
            "MetaSVM_score": "0.920",
            "MetaLR_score": "0.885",
            "GERP++_RS": "5.48",
            "phyloP100way_vertebrate": "7.92",
            "MPC_score": "1.85",
            "PrimateAI_score": "0.91",
        }
        base.update(overrides)
        return base

    def test_valid_line(self):
        fields = self._make_fields()
        record, skip = parse_dbnsfp_tsv_line(fields)

        assert skip is None
        assert record is not None
        assert record.rsid == "rs429358"
        assert record.chrom == "19"
        assert record.pos == 44908684
        assert record.ref == "T"
        assert record.alt == "C"
        assert record.cadd_phred == pytest.approx(28.3)
        assert record.sift_score == pytest.approx(0.001)
        assert record.sift_pred == "D"
        assert record.revel == pytest.approx(0.812)

    def test_chr_prefix_stripped(self):
        fields = self._make_fields(**{"#chr": "chr19"})
        record, skip = parse_dbnsfp_tsv_line(fields)
        assert record is not None
        assert record.chrom == "19"

    def test_invalid_chrom(self):
        fields = self._make_fields(**{"#chr": "chrUn"})
        record, skip = parse_dbnsfp_tsv_line(fields)
        assert record is None
        assert skip == "invalid_chrom"

    def test_missing_pos(self):
        fields = self._make_fields(**{"pos(1-based)": ""})
        record, skip = parse_dbnsfp_tsv_line(fields)
        assert record is None
        assert skip == "malformed"

    def test_missing_ref(self):
        fields = self._make_fields(ref="")
        record, skip = parse_dbnsfp_tsv_line(fields)
        assert record is None
        assert skip == "malformed"

    def test_no_rsid_still_loads(self):
        """Variants without rsids should still be loaded (keyed on chrom/pos/ref/alt)."""
        fields = self._make_fields(**{"rs_dbSNP": "."})
        record, skip = parse_dbnsfp_tsv_line(fields)
        assert record is not None
        assert record.rsid is None

    def test_multi_rsid_takes_first(self):
        fields = self._make_fields(**{"rs_dbSNP": "rs429358;rs12345"})
        record, skip = parse_dbnsfp_tsv_line(fields)
        assert record is not None
        assert record.rsid == "rs429358"

    def test_no_scores_skipped(self):
        fields = {
            "#chr": "1",
            "pos(1-based)": "100",
            "ref": "A",
            "alt": "G",
            "rs_dbSNP": "rs1",
        }
        record, skip = parse_dbnsfp_tsv_line(fields)
        assert record is None
        assert skip == "no_scores"

    def test_multi_transcript_scores(self):
        """Multi-transcript semicolon-separated scores take first non-missing."""
        fields = self._make_fields(
            CADD_phred="28.3",
            SIFT4G_score=".;0.002;0.005",
            SIFT4G_pred=".;D;T",
        )
        record, skip = parse_dbnsfp_tsv_line(fields)
        assert record is not None
        assert record.sift_score == pytest.approx(0.002)
        assert record.sift_pred == "D"

    def test_partial_scores(self):
        """Record with only some scores should still be loaded."""
        fields = self._make_fields(
            SIFT4G_score=".",
            SIFT4G_pred=".",
            Polyphen2_HVAR_score=".",
            Polyphen2_HVAR_pred=".",
            REVEL_score=".",
            MutPred_score=".",
            VEST4_score=".",
            MetaSVM_score=".",
            MetaLR_score=".",
            MPC_score=".",
            PrimateAI_score=".",
        )
        record, skip = parse_dbnsfp_tsv_line(fields)
        assert record is not None
        assert record.cadd_phred == pytest.approx(28.3)
        assert record.sift_score is None


# ── Table creation tests ─────────────────────────────────────────────────


class TestCreateDbnsfpTables:
    def test_creates_table(self, dbnsfp_engine: sa.Engine):
        with dbnsfp_engine.connect() as conn:
            result = conn.execute(
                sa.text(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='dbnsfp_scores'"
                )
            ).fetchone()
        assert result is not None

    def test_creates_indexes(self, dbnsfp_engine: sa.Engine):
        with dbnsfp_engine.connect() as conn:
            indexes = conn.execute(
                sa.text(
                    "SELECT name FROM sqlite_master"
                    " WHERE type='index' AND tbl_name='dbnsfp_scores'"
                )
            ).fetchall()
        index_names = {r[0] for r in indexes}
        assert "idx_dbnsfp_rsid" in index_names
        assert "idx_dbnsfp_chrom_pos" in index_names

    def test_idempotent(self, dbnsfp_engine: sa.Engine):
        """Calling create_dbnsfp_tables twice should not error."""
        create_dbnsfp_tables(dbnsfp_engine)
        # Should not raise


# ── CSV loading tests ────────────────────────────────────────────────────


class TestLoadDbnsfpFromCsv:
    def test_loads_seed_data(self, dbnsfp_engine_with_data: sa.Engine):
        with dbnsfp_engine_with_data.connect() as conn:
            count = conn.execute(sa.text("SELECT COUNT(*) FROM dbnsfp_scores")).scalar()
        assert count == 61  # 62 lines - 1 header = 61 data rows

    def test_stats_correct(self, dbnsfp_engine: sa.Engine):
        stats = load_dbnsfp_from_csv(DBNSFP_SEED_CSV, dbnsfp_engine)
        assert stats.total_lines == 61
        assert stats.variants_loaded == 61

    def test_clear_existing(self, dbnsfp_engine_with_data: sa.Engine):
        """Loading with clear_existing=True replaces data."""
        stats = load_dbnsfp_from_csv(DBNSFP_SEED_CSV, dbnsfp_engine_with_data, clear_existing=True)
        assert stats.variants_loaded == 61

    def test_known_variant_rs1801133(self, dbnsfp_engine_with_data: sa.Engine):
        """T2-11: Verify CADD and REVEL scores for rs1801133 (MTHFR C677T)."""
        with dbnsfp_engine_with_data.connect() as conn:
            row = conn.execute(
                sa.text("SELECT * FROM dbnsfp_scores WHERE rsid = 'rs1801133'")
            ).fetchone()
        assert row is not None
        assert row.cadd_phred == pytest.approx(24.8)
        assert row.revel == pytest.approx(0.689)
        assert row.chrom == "1"
        assert row.pos == 11856378

    def test_null_scores_preserved(self, dbnsfp_engine_with_data: sa.Engine):
        """Rows with some NULL scores should have NULLs in the DB."""
        with dbnsfp_engine_with_data.connect() as conn:
            row = conn.execute(
                sa.text("SELECT * FROM dbnsfp_scores WHERE rsid = 'rs80357906'")
            ).fetchone()
        assert row is not None
        assert row.cadd_phred == pytest.approx(35.0)
        # These are empty in seed CSV → NULL
        assert row.sift_score is None
        assert row.sift_pred is None


# ── Lookup tests ─────────────────────────────────────────────────────────


class TestLookupDbnsfpByRsids:
    def test_returns_correct_scores(self, dbnsfp_engine_with_data: sa.Engine):
        """T2-11: dbNSFP lookup returns correct CADD, REVEL for rs1801133."""
        results = lookup_dbnsfp_by_rsids(["rs1801133"], dbnsfp_engine_with_data)
        assert "rs1801133" in results
        annot = results["rs1801133"]
        assert annot.cadd_phred == pytest.approx(24.8)
        assert annot.revel == pytest.approx(0.689)
        assert annot.sift_pred == "D"
        assert annot.chrom == "1"
        assert annot.pos == 11856378

    def test_batch_lookup(self, dbnsfp_engine_with_data: sa.Engine):
        results = lookup_dbnsfp_by_rsids(
            ["rs429358", "rs7412", "rs_nonexistent"], dbnsfp_engine_with_data
        )
        assert "rs429358" in results
        assert "rs7412" in results
        assert "rs_nonexistent" not in results
        assert results["rs429358"].cadd_phred == pytest.approx(28.3)
        assert results["rs7412"].cadd_phred == pytest.approx(26.1)

    def test_empty_rsids(self, dbnsfp_engine_with_data: sa.Engine):
        results = lookup_dbnsfp_by_rsids([], dbnsfp_engine_with_data)
        assert len(results) == 0

    def test_large_batch_chunking(self, dbnsfp_engine_with_data: sa.Engine):
        """Ensure batching works for lists larger than LOOKUP_BATCH_SIZE."""
        # Create 600 rsids (beyond the 500 batch limit), mostly non-existent
        rsids = [f"rs{i}" for i in range(600)]
        rsids.append("rs429358")  # one real one
        results = lookup_dbnsfp_by_rsids(rsids, dbnsfp_engine_with_data)
        assert "rs429358" in results

    def test_deleterious_count_computed(self, dbnsfp_engine_with_data: sa.Engine):
        """DbNSFPAnnotation.deleterious_count should be auto-computed."""
        results = lookup_dbnsfp_by_rsids(["rs429358"], dbnsfp_engine_with_data)
        annot = results["rs429358"]
        # rs429358 has: SIFT=0.001(D), PP2=0.998(D), CADD=28.3, REVEL=0.812, MetaSVM=0.920
        # All 5 should be deleterious
        assert annot.deleterious_count == 5


class TestLookupDbnsfpByPositions:
    def test_returns_correct_scores(self, dbnsfp_engine_with_data: sa.Engine):
        results = lookup_dbnsfp_by_positions([("19", 44908684, "T", "C")], dbnsfp_engine_with_data)
        key = ("19", 44908684, "T", "C")
        assert key in results
        annot = results[key]
        assert annot.cadd_phred == pytest.approx(28.3)
        assert annot.rsid == "rs429358"

    def test_empty_positions(self, dbnsfp_engine_with_data: sa.Engine):
        results = lookup_dbnsfp_by_positions([], dbnsfp_engine_with_data)
        assert len(results) == 0

    def test_nonexistent_position(self, dbnsfp_engine_with_data: sa.Engine):
        results = lookup_dbnsfp_by_positions([("99", 1, "A", "T")], dbnsfp_engine_with_data)
        assert len(results) == 0

    def test_large_position_batch_chunking(self, dbnsfp_engine_with_data: sa.Engine):
        """Ensure batching works for lists larger than internal batch size."""
        positions = [(str(i % 22 + 1), i, "A", "T") for i in range(300)]
        positions.append(("19", 44908684, "T", "C"))  # one real one
        results = lookup_dbnsfp_by_positions(positions, dbnsfp_engine_with_data)
        assert ("19", 44908684, "T", "C") in results


# ── Ensemble pathogenicity tests ─────────────────────────────────────────


class TestEnsemblePathogenicity:
    """T2-12: Ensemble pathogenicity flag fires when ≥3 tools deleterious."""

    def _make_annot(self, **kwargs) -> DbNSFPAnnotation:
        defaults = {
            "rsid": "rs1",
            "chrom": "1",
            "pos": 100,
            "ref": "A",
            "alt": "G",
        }
        defaults.update(kwargs)
        return DbNSFPAnnotation(**defaults)

    def test_all_deleterious(self):
        annot = self._make_annot(
            sift_score=0.001,
            polyphen2_hsvar_score=0.999,
            cadd_phred=30.0,
            revel=0.8,
            metasvm=0.5,
        )
        assert count_deleterious(annot) == 5
        assert is_ensemble_pathogenic(annot)

    def test_exactly_three_deleterious(self):
        annot = self._make_annot(
            sift_score=0.001,  # D: < 0.05
            polyphen2_hsvar_score=0.999,  # D: > 0.453
            cadd_phred=25.0,  # D: >= 20
            revel=0.3,  # T: < 0.5
            metasvm=-0.5,  # T: <= 0
        )
        assert count_deleterious(annot) == 3
        assert is_ensemble_pathogenic(annot)

    def test_two_deleterious_not_pathogenic(self):
        annot = self._make_annot(
            sift_score=0.001,  # D
            polyphen2_hsvar_score=0.999,  # D
            cadd_phred=15.0,  # T: < 20
            revel=0.3,  # T
            metasvm=-0.5,  # T
        )
        assert count_deleterious(annot) == 2
        assert not is_ensemble_pathogenic(annot)

    def test_none_deleterious(self):
        annot = self._make_annot(
            sift_score=0.5,
            polyphen2_hsvar_score=0.1,
            cadd_phred=5.0,
            revel=0.1,
            metasvm=-1.0,
        )
        assert count_deleterious(annot) == 0
        assert not is_ensemble_pathogenic(annot)

    def test_all_null_scores(self):
        annot = self._make_annot()
        assert count_deleterious(annot) == 0
        assert not is_ensemble_pathogenic(annot)

    def test_boundary_values(self):
        """Test exact threshold boundaries."""
        # SIFT boundary: exactly 0.05 is NOT deleterious (< 0.05 required)
        annot = self._make_annot(sift_score=0.05)
        assert count_deleterious(annot) == 0

        # PolyPhen boundary: exactly 0.453 is NOT deleterious (> 0.453 required)
        annot = self._make_annot(polyphen2_hsvar_score=0.453)
        assert count_deleterious(annot) == 0

        # CADD boundary: exactly 20 IS deleterious (>= 20)
        annot = self._make_annot(cadd_phred=20.0)
        assert count_deleterious(annot) == 1

        # REVEL boundary: exactly 0.5 IS deleterious (>= 0.5)
        annot = self._make_annot(revel=0.5)
        assert count_deleterious(annot) == 1

        # MetaSVM boundary: exactly 0 is NOT deleterious (> 0 required)
        annot = self._make_annot(metasvm=0.0)
        assert count_deleterious(annot) == 0


# ── Version tracking tests ───────────────────────────────────────────────


class TestRecordDbnsfpVersion:
    def test_inserts_version(self, reference_engine: sa.Engine):
        record_dbnsfp_version(
            reference_engine,
            version="4.5a",
            file_path="/data/dbnsfp.db",
            file_size_bytes=1_500_000_000,
            checksum="abc123",
        )
        with reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "dbnsfp")
            ).fetchone()
        assert row is not None
        assert row.version == "4.5a"
        assert row.file_size_bytes == 1_500_000_000
        assert row.checksum_sha256 == "abc123"

    def test_updates_existing_version(self, reference_engine: sa.Engine):
        record_dbnsfp_version(reference_engine, version="4.4a")
        record_dbnsfp_version(reference_engine, version="4.5a")
        with reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "dbnsfp")
            ).fetchone()
        assert row.version == "4.5a"


# ── Constants tests ──────────────────────────────────────────────────────


class TestConstants:
    def test_bitmask_value(self):
        assert DBNSFP_BITMASK == 8  # bit 3

    def test_batch_sizes(self):
        assert BATCH_SIZE == 10_000
        # LOOKUP_BATCH_SIZE is dynamically computed from SQLITE_MAX_VARIABLE_NUMBER
        # (P4-22 optimization) — at least 500, but may be higher on Linux.
        assert LOOKUP_BATCH_SIZE >= 500

    def test_fields_tuple(self):
        assert len(DBNSFP_FIELDS) == 14
        assert "cadd_phred" in DBNSFP_FIELDS
        assert "revel" in DBNSFP_FIELDS
        assert "primateai" in DBNSFP_FIELDS


# ── Data class tests ─────────────────────────────────────────────────────


class TestDbNSFPRecord:
    def test_default_none_scores(self):
        record = DbNSFPRecord(rsid="rs1", chrom="1", pos=100, ref="A", alt="G")
        assert record.cadd_phred is None
        assert record.sift_score is None


class TestLoadStats:
    def test_defaults(self):
        stats = LoadStats()
        assert stats.total_lines == 0
        assert stats.variants_loaded == 0
        assert stats.skipped_no_rsid == 0
        assert stats.sha256 is None


class TestIndexAfterLoad:
    """The load path builds indexes AFTER the bulk insert (speed + smaller lock window)."""

    def test_load_on_fresh_engine_creates_indexes_and_data(self) -> None:
        # Fresh engine with NO tables: load must create the table, insert, then
        # build the indexes — all three must exist afterward and be queryable.
        engine = sa.create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        stats = load_dbnsfp_from_csv(DBNSFP_SEED_CSV, engine)
        assert stats.variants_loaded == 61

        with engine.connect() as conn:
            count = conn.execute(sa.text("SELECT COUNT(*) FROM dbnsfp_scores")).scalar()
            indexes = conn.execute(
                sa.text(
                    "SELECT name FROM sqlite_master"
                    " WHERE type='index' AND tbl_name='dbnsfp_scores'"
                )
            ).fetchall()
        index_names = {r[0] for r in indexes}
        assert count == 61
        assert "idx_dbnsfp_rsid" in index_names
        assert "idx_dbnsfp_chrom_pos" in index_names
        assert "idx_dbnsfp_rsid_covering" in index_names
