"""Tests for the 007 + 008 backfill migrations.

007 covers:
- ``auto_update_settings`` table creation + idempotent default seeding
- Backfill of ``database_versions`` for an extracted LAI bundle directory
  and an existing ``encode_ccres.db`` file when no row exists yet
- No backfill when rows already exist or files are absent
- Downgrade drops the table but preserves backfilled rows

008 covers (AncestryDNA Plan §7.4 step 2):
- Per-sample ``annotation_state`` created with
  ``vep_bundle_version='v1.0.0'`` for every sample listed in ``samples``
- Idempotent on re-run: pre-existing rows are not overwritten
- Missing / corrupt per-sample DBs are logged and skipped (no raise)
- No reference-DB schema change; downgrade leaves rows in place

009 covers (AncestryDNA Plan §9.2):
- ``individuals`` table created with expected columns + defaults
- ``samples.individual_id`` nullable FK column added with index
- ``ON DELETE SET NULL`` cascade fires when an individual is deleted
- Upgrade → downgrade → upgrade leaves the DB in a known-good shape
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from structlog.testing import capture_logs

from alembic import command

REPO_ROOT = Path(__file__).resolve().parents[2]


def _has_event(cap_logs: list[dict], event: str) -> list[dict]:
    return [e for e in cap_logs if e.get("event") == event]


def _alembic_config(db_path: Path) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def _upgrade(db_path: Path, revision: str = "head") -> None:
    command.upgrade(_alembic_config(db_path), revision)


def _downgrade(db_path: Path, revision: str) -> None:
    command.downgrade(_alembic_config(db_path), revision)


def _tables(db_path: Path) -> set[str]:
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'alembic_%' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    return {r[0] for r in rows}


def _columns(db_path: Path, table: str) -> dict[str, dict[str, object]]:
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()  # noqa: S608
    return {r[1]: {"type": r[2], "notnull": bool(r[3]), "pk": bool(r[5])} for r in rows}


@pytest.fixture
def data_dir_with_reference(tmp_path: Path) -> Path:
    """Provide a data directory whose reference.db is at the canonical path."""
    return tmp_path


# ── Table creation + seeding ──────────────────────────────────────────


class TestAutoUpdateSettingsTable:
    def test_table_created_with_expected_columns(self, data_dir_with_reference: Path) -> None:
        db = data_dir_with_reference / "reference.db"
        _upgrade(db)

        assert "auto_update_settings" in _tables(db)

        cols = _columns(db, "auto_update_settings")
        assert cols["db_name"]["pk"] is True
        assert cols["enabled"]["notnull"] is True
        assert cols["updated_at"]["notnull"] is True

    def test_defaults_seeded(self, data_dir_with_reference: Path) -> None:
        db = data_dir_with_reference / "reference.db"
        _upgrade(db)

        with sqlite3.connect(str(db)) as conn:
            rows = dict(
                conn.execute("SELECT db_name, enabled FROM auto_update_settings").fetchall()
            )

        expected = {
            "clinvar": 1,
            "gwas_catalog": 1,
            "gnomad": 1,
            "dbnsfp": 1,
            "dbsnp": 1,
            "mondo_hpo": 1,
            "vep_bundle": 0,
            "cpic": 1,
            "encode_ccres": 1,
            "ancestry_pca": 1,
        }
        assert rows == expected

    def test_seeding_idempotent(self, data_dir_with_reference: Path) -> None:
        """Re-running the migration is safe — no duplicate rows."""
        db = data_dir_with_reference / "reference.db"
        _upgrade(db)
        # Downgrade just the 007 step then re-apply: defaults should still be a single row each
        _downgrade(db, "006")
        _upgrade(db)

        with sqlite3.connect(str(db)) as conn:
            count_total = conn.execute("SELECT COUNT(*) FROM auto_update_settings").fetchone()[0]
            count_distinct = conn.execute(
                "SELECT COUNT(DISTINCT db_name) FROM auto_update_settings"
            ).fetchone()[0]
        assert count_total == count_distinct == 10

    def test_pre_existing_row_preserved_on_seeding(self, data_dir_with_reference: Path) -> None:
        """A user-modified toggle survives a downgrade/upgrade cycle's reseed."""
        db = data_dir_with_reference / "reference.db"
        _upgrade(db)
        # Flip clinvar off
        with sqlite3.connect(str(db)) as conn:
            conn.execute("UPDATE auto_update_settings SET enabled = 0 WHERE db_name = 'clinvar'")

        # Re-running the upgrade (idempotent: same state) must not reset clinvar
        _upgrade(db)

        with sqlite3.connect(str(db)) as conn:
            value = conn.execute(
                "SELECT enabled FROM auto_update_settings WHERE db_name = 'clinvar'"
            ).fetchone()[0]
        assert value == 0


# ── Backfill: LAI bundle + ENCODE cCREs ───────────────────────────────


class TestBackfillBundleVersions:
    def _seed_lai_bundle_dir(self, data_dir: Path) -> Path:
        bundle = data_dir / "lai_bundle"
        bundle.mkdir()
        (bundle / "metadata.json").write_text('{"version": "v1.1"}')
        nested = bundle / "gnomix_models" / "chr1"
        nested.mkdir(parents=True)
        (nested / "smoother.json").write_bytes(b"x" * 1024)
        return bundle

    def _seed_encode_db_file(self, data_dir: Path) -> Path:
        path = data_dir / "encode_ccres.db"
        path.write_bytes(b"\x00" * 4096)
        return path

    def test_lai_bundle_backfill(self, data_dir_with_reference: Path) -> None:
        db = data_dir_with_reference / "reference.db"
        bundle = self._seed_lai_bundle_dir(data_dir_with_reference)

        _upgrade(db)

        with sqlite3.connect(str(db)) as conn:
            row = conn.execute(
                "SELECT version, file_path, file_size_bytes, checksum_sha256 "
                "FROM database_versions WHERE db_name = 'lai_bundle'"
            ).fetchone()

        assert row is not None
        version, file_path, file_size, sha = row
        assert version == "unknown-pre-manifest"
        assert file_path == str(bundle)
        # 1024 from smoother.json + a few bytes from metadata.json
        assert file_size and file_size >= 1024
        assert sha is None

    def test_encode_ccres_backfill(self, data_dir_with_reference: Path) -> None:
        db = data_dir_with_reference / "reference.db"
        encode = self._seed_encode_db_file(data_dir_with_reference)

        _upgrade(db)

        with sqlite3.connect(str(db)) as conn:
            row = conn.execute(
                "SELECT version, file_path, file_size_bytes "
                "FROM database_versions WHERE db_name = 'encode_ccres'"
            ).fetchone()

        assert row is not None
        version, file_path, size = row
        assert version == "unknown-pre-manifest"
        assert file_path == str(encode)
        assert size == 4096

    def test_no_backfill_when_files_absent(self, data_dir_with_reference: Path) -> None:
        db = data_dir_with_reference / "reference.db"
        _upgrade(db)

        with sqlite3.connect(str(db)) as conn:
            rows = conn.execute(
                "SELECT db_name FROM database_versions "
                "WHERE db_name IN ('lai_bundle', 'encode_ccres')"
            ).fetchall()
        assert rows == []

    def test_no_backfill_when_row_already_present(self, data_dir_with_reference: Path) -> None:
        """An existing database_versions row is not overwritten by the backfill."""
        db = data_dir_with_reference / "reference.db"
        bundle = self._seed_lai_bundle_dir(data_dir_with_reference)

        # Run prior migrations first so database_versions exists, then pre-seed a row
        _upgrade(db, revision="006")
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "INSERT INTO database_versions "
                "(db_name, version, file_path, file_size_bytes) "
                "VALUES ('lai_bundle', 'v1.0', :p, 1)",
                {"p": str(bundle)},
            )

        _upgrade(db)

        with sqlite3.connect(str(db)) as conn:
            rows = conn.execute(
                "SELECT version FROM database_versions WHERE db_name = 'lai_bundle'"
            ).fetchall()
        assert rows == [("v1.0",)]

    def test_only_lai_backfilled_when_encode_missing(self, data_dir_with_reference: Path) -> None:
        db = data_dir_with_reference / "reference.db"
        self._seed_lai_bundle_dir(data_dir_with_reference)

        _upgrade(db)

        with sqlite3.connect(str(db)) as conn:
            names = {
                r[0]
                for r in conn.execute(
                    "SELECT db_name FROM database_versions "
                    "WHERE db_name IN ('lai_bundle', 'encode_ccres')"
                ).fetchall()
            }
        assert names == {"lai_bundle"}


# ── Downgrade ─────────────────────────────────────────────────────────


class TestDowngrade:
    def test_drops_auto_update_settings(self, data_dir_with_reference: Path) -> None:
        db = data_dir_with_reference / "reference.db"
        _upgrade(db)
        assert "auto_update_settings" in _tables(db)

        _downgrade(db, "006")
        assert "auto_update_settings" not in _tables(db)

    def test_backfilled_rows_preserved_on_downgrade(self, data_dir_with_reference: Path) -> None:
        db = data_dir_with_reference / "reference.db"
        bundle = data_dir_with_reference / "lai_bundle"
        bundle.mkdir()
        (bundle / "f.bin").write_bytes(b"x" * 16)

        _upgrade(db)
        with sqlite3.connect(str(db)) as conn:
            assert conn.execute(
                "SELECT version FROM database_versions WHERE db_name = 'lai_bundle'"
            ).fetchone() == ("unknown-pre-manifest",)

        _downgrade(db, "006")
        with sqlite3.connect(str(db)) as conn:
            # database_versions itself still exists (created in 001),
            # and the backfilled row is intentionally not rolled back.
            row = conn.execute(
                "SELECT version FROM database_versions WHERE db_name = 'lai_bundle'"
            ).fetchone()
        assert row == ("unknown-pre-manifest",)


# ── Helper coverage ───────────────────────────────────────────────────


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "_mig_007_loader",
        REPO_ROOT / "alembic" / "versions" / "007_add_auto_update_settings.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestDataDirHelper:
    def test_returns_parent_of_db_path(self, tmp_path: Path) -> None:
        module = _load_migration_module()
        engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
        with engine.connect() as conn:
            assert module._data_dir_from_bind(conn) == tmp_path.resolve()

    def test_returns_none_for_memory_db(self) -> None:
        module = _load_migration_module()
        engine = sa.create_engine("sqlite://")
        with engine.connect() as conn:
            assert module._data_dir_from_bind(conn) is None


# ── 008: per-sample annotation_state backfill ─────────────────────────


def _load_008_module():
    spec = importlib.util.spec_from_file_location(
        "_mig_008_loader",
        REPO_ROOT / "alembic" / "versions" / "008_annotation_state_backfill.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed_sample_row(reference_db: Path, sample_id: int, sample_db_path: Path) -> None:
    """Insert a row into ``samples`` after migrations have built the table."""
    with sqlite3.connect(str(reference_db)) as conn:
        conn.execute(
            "INSERT INTO samples (id, name, db_path, file_format, file_hash) "
            "VALUES (?, ?, ?, ?, ?)",
            (sample_id, f"sample-{sample_id}", str(sample_db_path), "23andme_v5", "h"),
        )


def _make_empty_sqlite(path: Path) -> None:
    """Create a fresh SQLite file with no application tables."""
    with sqlite3.connect(str(path)) as conn:
        conn.execute("PRAGMA user_version = 0")


def _read_annotation_state(sample_db: Path) -> list[tuple[str, str]]:
    with sqlite3.connect(str(sample_db)) as conn:
        return conn.execute("SELECT key, value FROM annotation_state ORDER BY key").fetchall()


class TestMigration008AnnotationStateBackfill:
    def test_creates_annotation_state_and_seeds_v1(self, tmp_path: Path) -> None:
        """A sample DB with no annotation_state gets the table + seeded row."""
        reference_db = tmp_path / "reference.db"
        sample_db = tmp_path / "sample_1.db"
        _make_empty_sqlite(sample_db)

        # Build reference schema up to 007, then seed a sample row, then run 008.
        _upgrade(reference_db, revision="007")
        _seed_sample_row(reference_db, 1, sample_db)
        _upgrade(reference_db, revision="008")

        rows = _read_annotation_state(sample_db)
        assert rows == [("vep_bundle_version", "v1.0.0")]

    def test_idempotent_preserves_existing_row(self, tmp_path: Path) -> None:
        """A freshly re-annotated sample's row is NOT overwritten on re-run."""
        reference_db = tmp_path / "reference.db"
        sample_db = tmp_path / "sample_42.db"
        _make_empty_sqlite(sample_db)

        # Pre-seed the sample with a newer bundle version (simulates a sample
        # re-annotated after migration 008 has already run once).
        with sqlite3.connect(str(sample_db)) as conn:
            conn.execute(
                "CREATE TABLE annotation_state ("
                "key TEXT PRIMARY KEY, value TEXT NOT NULL, "
                "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            conn.execute(
                "INSERT INTO annotation_state (key, value) VALUES ('vep_bundle_version', 'v2.0.0')"
            )

        _upgrade(reference_db, revision="007")
        _seed_sample_row(reference_db, 42, sample_db)

        # First run + a second run for full idempotency.
        _upgrade(reference_db, revision="008")
        _upgrade(reference_db, revision="008")

        rows = _read_annotation_state(sample_db)
        assert rows == [("vep_bundle_version", "v2.0.0")]

    def test_missing_sample_db_logged_and_skipped(self, tmp_path: Path) -> None:
        """A samples row whose db_path is missing on disk is logged + skipped.

        The migration must NOT raise — a single missing per-sample DB cannot
        block the schema bump for the whole reference DB.
        """
        reference_db = tmp_path / "reference.db"
        missing_sample_db = tmp_path / "sample_999_gone.db"

        _upgrade(reference_db, revision="007")
        _seed_sample_row(reference_db, 999, missing_sample_db)

        with capture_logs() as cap_logs:
            _upgrade(reference_db, revision="008")  # must not raise

        # Sample file was never created → no annotation_state DB to inspect.
        assert not missing_sample_db.exists()
        events = _has_event(cap_logs, "alembic_008_sample_db_skipped")
        assert events, "expected alembic_008_sample_db_skipped warning"
        assert any(e.get("reason") == "missing" for e in events)
        assert any("sample_999_gone.db" in str(e.get("sample_db", "")) for e in events)

    def test_corrupt_sample_db_logged_and_skipped(self, tmp_path: Path) -> None:
        """A non-SQLite blob at samples.db_path is logged + skipped, not raised."""
        reference_db = tmp_path / "reference.db"
        corrupt_db = tmp_path / "sample_7_corrupt.db"
        corrupt_db.write_bytes(b"not a sqlite file at all\x00\x01\x02")

        _upgrade(reference_db, revision="007")
        _seed_sample_row(reference_db, 7, corrupt_db)

        with capture_logs() as cap_logs:
            _upgrade(reference_db, revision="008")

        events = _has_event(cap_logs, "alembic_008_sample_db_skipped")
        assert events, "expected alembic_008_sample_db_skipped warning"
        assert any(e.get("reason") == "sqlalchemy_error" for e in events)
        assert any("sample_7_corrupt.db" in str(e.get("sample_db", "")) for e in events)

    def test_empty_db_path_logged_and_skipped(self, tmp_path: Path) -> None:
        """A samples row with an empty db_path is logged + skipped."""
        reference_db = tmp_path / "reference.db"
        _upgrade(reference_db, revision="007")
        # Insert a samples row with empty db_path. NOT NULL is satisfied by ''.
        with sqlite3.connect(str(reference_db)) as conn:
            conn.execute(
                "INSERT INTO samples (id, name, db_path, file_format, file_hash) "
                "VALUES (?, ?, ?, ?, ?)",
                (5, "empty-path", "", "23andme_v5", "h"),
            )

        with capture_logs() as cap_logs:
            _upgrade(reference_db, revision="008")

        events = _has_event(cap_logs, "alembic_008_sample_db_skipped")
        assert events, "expected alembic_008_sample_db_skipped warning"
        assert any(e.get("reason") == "empty_db_path" for e in events)

    def test_relative_db_path_resolved_against_data_dir(self, tmp_path: Path) -> None:
        """``samples.db_path`` stored as relative resolves against data_dir."""
        reference_db = tmp_path / "reference.db"
        sample_db = tmp_path / "sample_3.db"
        _make_empty_sqlite(sample_db)

        _upgrade(reference_db, revision="007")
        # Store path as a bare filename relative to data_dir.
        _seed_sample_row(reference_db, 3, Path("sample_3.db"))
        _upgrade(reference_db, revision="008")

        rows = _read_annotation_state(sample_db)
        assert rows == [("vep_bundle_version", "v1.0.0")]

    def test_no_samples_rows_is_noop(self, tmp_path: Path) -> None:
        """Empty samples table → migration completes without touching anything."""
        reference_db = tmp_path / "reference.db"
        _upgrade(reference_db, revision="007")
        _upgrade(reference_db, revision="008")
        # No assertion error == success; verify reference DB still healthy.
        with sqlite3.connect(str(reference_db)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        assert count == 0

    def test_no_reference_schema_change(self, tmp_path: Path) -> None:
        """008 must not add or drop any reference-DB tables."""
        ref_a = tmp_path / "before.db"
        ref_b = tmp_path / "after.db"
        _upgrade(ref_a, revision="007")
        _upgrade(ref_b, revision="008")
        assert _tables(ref_a) == _tables(ref_b)

    def test_downgrade_is_noop(self, tmp_path: Path) -> None:
        """Downgrade from 008 → 007 leaves per-sample rows untouched."""
        reference_db = tmp_path / "reference.db"
        sample_db = tmp_path / "sample_8.db"
        _make_empty_sqlite(sample_db)

        _upgrade(reference_db, revision="007")
        _seed_sample_row(reference_db, 8, sample_db)
        _upgrade(reference_db, revision="008")

        _downgrade(reference_db, "007")

        # Per-sample annotation_state row is intentionally preserved.
        rows = _read_annotation_state(sample_db)
        assert rows == [("vep_bundle_version", "v1.0.0")]


class TestMigration008Helpers:
    def test_data_dir_from_bind_returns_parent(self, tmp_path: Path) -> None:
        module = _load_008_module()
        engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
        with engine.connect() as conn:
            assert module._data_dir_from_bind(conn) == tmp_path.resolve()

    def test_data_dir_from_bind_returns_none_for_memory(self) -> None:
        module = _load_008_module()
        engine = sa.create_engine("sqlite://")
        with engine.connect() as conn:
            assert module._data_dir_from_bind(conn) is None

    def test_resolve_absolute_path_passes_through(self, tmp_path: Path) -> None:
        module = _load_008_module()
        absolute = tmp_path / "sample_x.db"
        assert module._resolve_sample_db_path(str(absolute), tmp_path) == absolute

    def test_resolve_relative_path_joins_data_dir(self, tmp_path: Path) -> None:
        module = _load_008_module()
        resolved = module._resolve_sample_db_path("sub/sample_x.db", tmp_path)
        assert resolved == (tmp_path / "sub" / "sample_x.db").resolve()

    def test_resolve_relative_path_without_data_dir(self) -> None:
        module = _load_008_module()
        # When data_dir is None (e.g. :memory: bind), relative paths fall
        # through to the literal path.
        assert module._resolve_sample_db_path("sample_x.db", None) == Path("sample_x.db")


# ── 009: individuals table + samples.individual_id FK ─────────────────


def _samples_fk_to_individuals(reference_db: Path) -> tuple[str, str, str] | None:
    """Return (from_col, to_table, on_delete) for the FK on samples, if present."""
    with sqlite3.connect(str(reference_db)) as conn:
        rows = conn.execute("PRAGMA foreign_key_list(samples)").fetchall()
    for row in rows:
        # (id, seq, table, from, to, on_update, on_delete, match)
        if row[2] == "individuals":
            return (row[3], row[2], row[6])
    return None


def _sample_indexes(reference_db: Path) -> set[str]:
    with sqlite3.connect(str(reference_db)) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name='samples' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    return {r[0] for r in rows}


class TestMigration009Individuals:
    def test_creates_individuals_table_with_expected_columns(self, tmp_path: Path) -> None:
        db = tmp_path / "reference.db"
        _upgrade(db, revision="009")

        assert "individuals" in _tables(db)

        cols = _columns(db, "individuals")
        assert cols["id"]["pk"] is True
        assert cols["display_name"]["notnull"] is True
        # nullable text columns
        assert cols["notes"]["notnull"] is False
        assert cols["biological_sex"]["notnull"] is False
        assert cols["created_at"]["notnull"] is False
        assert cols["updated_at"]["notnull"] is False

    def test_notes_default_seeds_empty_string(self, tmp_path: Path) -> None:
        db = tmp_path / "reference.db"
        _upgrade(db, revision="009")
        with sqlite3.connect(str(db)) as conn:
            conn.execute("INSERT INTO individuals (display_name) VALUES ('Ada')")
            row = conn.execute("SELECT notes FROM individuals WHERE display_name='Ada'").fetchone()
        assert row == ("",)

    def test_adds_individual_id_column_to_samples(self, tmp_path: Path) -> None:
        db = tmp_path / "reference.db"
        _upgrade(db, revision="009")
        cols = _columns(db, "samples")
        assert "individual_id" in cols
        assert cols["individual_id"]["notnull"] is False

    def test_samples_fk_targets_individuals_with_set_null(self, tmp_path: Path) -> None:
        db = tmp_path / "reference.db"
        _upgrade(db, revision="009")
        fk = _samples_fk_to_individuals(db)
        assert fk == ("individual_id", "individuals", "SET NULL")

    def test_index_on_samples_individual_id_created(self, tmp_path: Path) -> None:
        db = tmp_path / "reference.db"
        _upgrade(db, revision="009")
        assert "ix_samples_individual_id" in _sample_indexes(db)

    def test_fk_set_null_fires_on_individual_delete(self, tmp_path: Path) -> None:
        """Plan §9.2: deleting an individual NULLs samples.individual_id."""
        db = tmp_path / "reference.db"
        _upgrade(db, revision="009")
        with sqlite3.connect(str(db)) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("INSERT INTO individuals (id, display_name) VALUES (1, 'Ada')")
            conn.execute(
                "INSERT INTO samples (id, name, db_path, individual_id) "
                "VALUES (1, 's1', '/tmp/s1.db', 1)"
            )
            conn.commit()
            conn.execute("DELETE FROM individuals WHERE id = 1")
            conn.commit()
            row = conn.execute("SELECT individual_id FROM samples WHERE id = 1").fetchone()
        assert row == (None,)

    def test_existing_samples_get_null_individual_id(self, tmp_path: Path) -> None:
        """Pre-009 rows surface as 'Unassigned' (individual_id IS NULL)."""
        db = tmp_path / "reference.db"
        _upgrade(db, revision="008")
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "INSERT INTO samples (id, name, db_path) VALUES (42, 'legacy', '/tmp/legacy.db')"
            )
            conn.commit()

        _upgrade(db, revision="009")

        with sqlite3.connect(str(db)) as conn:
            row = conn.execute("SELECT individual_id FROM samples WHERE id = 42").fetchone()
        assert row == (None,)

    def test_downgrade_drops_index_column_and_table(self, tmp_path: Path) -> None:
        db = tmp_path / "reference.db"
        _upgrade(db, revision="009")
        _downgrade(db, "008")

        assert "individuals" not in _tables(db)
        assert "individual_id" not in _columns(db, "samples")
        assert "ix_samples_individual_id" not in _sample_indexes(db)

    def test_upgrade_downgrade_upgrade_round_trip(self, tmp_path: Path) -> None:
        """Round-trip leaves a known-good schema: FK + index + table all back."""
        db = tmp_path / "reference.db"
        _upgrade(db, revision="009")
        _downgrade(db, "008")
        _upgrade(db, revision="009")

        assert "individuals" in _tables(db)
        assert "individual_id" in _columns(db, "samples")
        assert "ix_samples_individual_id" in _sample_indexes(db)
        assert _samples_fk_to_individuals(db) == (
            "individual_id",
            "individuals",
            "SET NULL",
        )

    def test_idempotent_when_run_to_head_twice(self, tmp_path: Path) -> None:
        """Alembic short-circuits re-runs at head; schema is stable across calls."""
        db = tmp_path / "reference.db"
        _upgrade(db, revision="head")
        before_tables = _tables(db)
        before_samples_cols = set(_columns(db, "samples").keys())
        before_indexes = _sample_indexes(db)

        _upgrade(db, revision="head")

        assert _tables(db) == before_tables
        assert set(_columns(db, "samples").keys()) == before_samples_cols
        assert _sample_indexes(db) == before_indexes


# ── 011: database_versions.genome_build (F30) ─────────────────────────


class TestMigration011GenomeBuild:
    def test_upgrade_adds_genome_build_column(self, tmp_path: Path) -> None:
        db = tmp_path / "reference.db"
        _upgrade(db, revision="011")

        cols = _columns(db, "database_versions")
        assert "genome_build" in cols
        # Nullable TEXT — build-agnostic sources record NULL.
        assert cols["genome_build"]["notnull"] is False

    def test_pre_011_db_lacks_column(self, tmp_path: Path) -> None:
        db = tmp_path / "reference.db"
        _upgrade(db, revision="010")
        assert "genome_build" not in _columns(db, "database_versions")

    def test_downgrade_drops_genome_build_column(self, tmp_path: Path) -> None:
        db = tmp_path / "reference.db"
        _upgrade(db, revision="011")
        assert "genome_build" in _columns(db, "database_versions")

        _downgrade(db, "010")
        assert "genome_build" not in _columns(db, "database_versions")

    def test_upgrade_downgrade_upgrade_round_trip(self, tmp_path: Path) -> None:
        db = tmp_path / "reference.db"
        _upgrade(db, revision="011")
        _downgrade(db, "010")
        _upgrade(db, revision="011")
        assert "genome_build" in _columns(db, "database_versions")

    def test_existing_rows_survive_with_null_build(self, tmp_path: Path) -> None:
        """A row recorded before 011 keeps its data; new column reads NULL."""
        db = tmp_path / "reference.db"
        _upgrade(db, revision="010")
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "INSERT INTO database_versions (db_name, version) VALUES ('clinvar', '20260101')"
            )

        _upgrade(db, revision="011")

        with sqlite3.connect(str(db)) as conn:
            row = conn.execute(
                "SELECT version, genome_build FROM database_versions WHERE db_name = 'clinvar'"
            ).fetchone()
        assert row == ("20260101", None)
