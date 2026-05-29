"""Unit tests for ``backend.db.database_registry`` helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

from backend.db.database_registry import _build_encode_ccres_db, _record_db_version
from backend.db.tables import database_versions, reference_metadata


def test_record_db_version_inserts_new_row(reference_engine: sa.Engine) -> None:
    _record_db_version(
        reference_engine,
        db_name="lai_bundle",
        version="v1.1",
        file_size_bytes=523_801_111,
        sha256="959ed0fd9ebe2ad8fa542776a59ce73072d928c7ce59839ea81d0f1e78a5c18e",
    )

    with reference_engine.connect() as conn:
        rows = conn.execute(sa.select(database_versions)).fetchall()

    assert len(rows) == 1
    row = rows[0]
    assert row.db_name == "lai_bundle"
    assert row.version == "v1.1"
    assert row.file_size_bytes == 523_801_111
    assert (
        row.checksum_sha256 == "959ed0fd9ebe2ad8fa542776a59ce73072d928c7ce59839ea81d0f1e78a5c18e"
    )
    assert row.downloaded_at is not None


def test_record_db_version_updates_existing_row(reference_engine: sa.Engine) -> None:
    _record_db_version(
        reference_engine,
        db_name="encode_ccres",
        version="20260101",
        file_size_bytes=30_000_000,
    )
    _record_db_version(
        reference_engine,
        db_name="encode_ccres",
        version="20260508",
        file_size_bytes=31_000_000,
        sha256="aa" * 32,
    )

    with reference_engine.connect() as conn:
        rows = conn.execute(sa.select(database_versions)).fetchall()

    assert len(rows) == 1
    row = rows[0]
    assert row.db_name == "encode_ccres"
    assert row.version == "20260508"
    assert row.file_size_bytes == 31_000_000
    assert row.checksum_sha256 == "aa" * 32


def test_record_db_version_sha256_defaults_to_null(reference_engine: sa.Engine) -> None:
    _record_db_version(
        reference_engine,
        db_name="vep_bundle",
        version="2026-04-07",
        file_size_bytes=12_000_000,
    )

    with reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(database_versions).where(database_versions.c.db_name == "vep_bundle")
        ).fetchone()

    assert row is not None
    assert row.checksum_sha256 is None
    assert row.version == "2026-04-07"
    assert row.file_size_bytes == 12_000_000


def test_record_db_version_update_clears_sha256(reference_engine: sa.Engine) -> None:
    """Re-recording without sha256 should overwrite the prior checksum."""
    _record_db_version(
        reference_engine,
        db_name="ancestry_pca",
        version="v1.0",
        file_size_bytes=414_432,
        sha256="bb" * 32,
    )
    _record_db_version(
        reference_engine,
        db_name="ancestry_pca",
        version="v1.1",
        file_size_bytes=414_500,
    )

    with reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(database_versions).where(database_versions.c.db_name == "ancestry_pca")
        ).fetchone()

    assert row is not None
    assert row.version == "v1.1"
    assert row.checksum_sha256 is None


def test_record_db_version_independent_rows(reference_engine: sa.Engine) -> None:
    """Different db_names live in independent rows."""
    _record_db_version(
        reference_engine, db_name="clinvar", version="20260301", file_size_bytes=100
    )
    _record_db_version(reference_engine, db_name="gnomad", version="v4.1", file_size_bytes=200)

    with reference_engine.connect() as conn:
        rows = conn.execute(
            sa.select(database_versions).order_by(database_versions.c.db_name)
        ).fetchall()

    assert [r.db_name for r in rows] == ["clinvar", "gnomad"]
    assert [r.version for r in rows] == ["20260301", "v4.1"]


def test_record_db_version_persists_file_path(reference_engine: sa.Engine) -> None:
    """``file_path`` is stored on insert and refreshed on update."""
    _record_db_version(
        reference_engine,
        db_name="gnomad",
        version="r2.1.1",
        file_size_bytes=2_000_000_000,
        sha256="cc" * 32,
        file_path="/tmp/data/gnomad.vcf.gz",
    )

    with reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(database_versions).where(database_versions.c.db_name == "gnomad")
        ).fetchone()
    assert row is not None
    assert row.file_path == "/tmp/data/gnomad.vcf.gz"

    # Update path: omit file_path → column resets to NULL (matches sha256 semantics).
    _record_db_version(
        reference_engine,
        db_name="gnomad",
        version="r2.2.0",
        file_size_bytes=2_100_000_000,
    )

    with reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(database_versions).where(database_versions.c.db_name == "gnomad")
        ).fetchone()
    assert row is not None
    assert row.version == "r2.2.0"
    assert row.file_path is None


# ──────────────────────────────────────────────────────────────────────
# Step 6: _build_encode_ccres_db records a database_versions row
# ──────────────────────────────────────────────────────────────────────

_ENCODE_SAMPLE_BED = """\
#chrom\tstart\tend\taccession\tscore\tstrand\tthickStart\tthickEnd\titemRgb\tccre_class
chr1\t10000\t10500\tEH38E0000001\t0\t.\t10000\t10500\t255,0,0\tPLS
chr1\t20000\t20800\tEH38E0000002\t0\t.\t20000\t20800\t255,205,0\tpELS
chr2\t30000\t30600\tEH38E0000003\t0\t.\t30000\t30600\t0,176,240\tdELS
"""


def _make_data_dir_with_reference(tmp_path: Path) -> Path:
    """Build a tmp data dir containing an empty reference.db with all tables."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    ref_path = data_dir / "reference.db"
    engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(engine)
    engine.dispose()
    return data_dir


def test_build_encode_ccres_records_version(tmp_path: Path) -> None:
    """After build, a row is upserted into reference.db database_versions."""
    data_dir = _make_data_dir_with_reference(tmp_path)
    bed_path = data_dir / "GRCh38-cCREs.bed"
    bed_path.write_text(_ENCODE_SAMPLE_BED)
    db_path = data_dir / "encode_ccres.db"

    _build_encode_ccres_db(bed_path, db_path)

    # SQLite DB created, raw BED removed.
    assert db_path.exists()
    assert not bed_path.exists()

    expected_version = datetime.now(UTC).strftime("%Y%m%d")
    expected_size = db_path.stat().st_size

    engine = sa.create_engine(f"sqlite:///{data_dir / 'reference.db'}")
    try:
        with engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "encode_ccres")
            ).fetchone()
    finally:
        engine.dispose()

    assert row is not None
    assert row.db_name == "encode_ccres"
    assert row.version == expected_version
    assert row.file_size_bytes == expected_size
    assert row.checksum_sha256 is None
    assert row.downloaded_at is not None


def test_build_encode_ccres_succeeds_when_reference_db_missing(tmp_path: Path) -> None:
    """No reference.db on disk: build still succeeds, recording is best-effort."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    bed_path = data_dir / "GRCh38-cCREs.bed"
    bed_path.write_text(_ENCODE_SAMPLE_BED)
    db_path = data_dir / "encode_ccres.db"

    # Should not raise even though reference.db has no schema.
    _build_encode_ccres_db(bed_path, db_path)

    assert db_path.exists()
    assert not bed_path.exists()


def test_build_encode_ccres_upserts_existing_row(tmp_path: Path) -> None:
    """A second build call updates the existing encode_ccres row in place."""
    data_dir = _make_data_dir_with_reference(tmp_path)

    # Seed an older row simulating a prior build.
    engine = sa.create_engine(f"sqlite:///{data_dir / 'reference.db'}")
    try:
        with engine.begin() as conn:
            conn.execute(
                database_versions.insert().values(
                    db_name="encode_ccres",
                    version="20200101",
                    file_size_bytes=1,
                    downloaded_at=datetime(2020, 1, 1, tzinfo=UTC),
                    checksum_sha256=None,
                )
            )
    finally:
        engine.dispose()

    bed_path = data_dir / "GRCh38-cCREs.bed"
    bed_path.write_text(_ENCODE_SAMPLE_BED)
    db_path = data_dir / "encode_ccres.db"

    _build_encode_ccres_db(bed_path, db_path)

    engine = sa.create_engine(f"sqlite:///{data_dir / 'reference.db'}")
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "encode_ccres")
            ).fetchall()
    finally:
        engine.dispose()

    # Single row, version refreshed.
    assert len(rows) == 1
    assert rows[0].version == datetime.now(UTC).strftime("%Y%m%d")
    assert rows[0].file_size_bytes == db_path.stat().st_size


# ──────────────────────────────────────────────────────────────────────
# Phase 0 closure (Step 18): semver `v2.0.0` recording for vep_bundle.
# Distinct from step 4's manifest fixture surface — this anchors the
# registry-level write path against the v2.0.0 string per Plan §5.2.
# ──────────────────────────────────────────────────────────────────────


def test_record_db_version_accepts_v2_0_0_semver(reference_engine: sa.Engine) -> None:
    """`_record_db_version` round-trips the v2.0.0 semver string verbatim."""
    _record_db_version(
        reference_engine,
        db_name="vep_bundle",
        version="v2.0.0",
        file_size_bytes=600_000_000,
        sha256="0" * 64,
        file_path="/tmp/vep_bundle.db",
    )

    with reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(database_versions).where(database_versions.c.db_name == "vep_bundle")
        ).fetchone()

    assert row is not None
    assert row.db_name == "vep_bundle"
    assert row.version == "v2.0.0"
    assert row.file_size_bytes == 600_000_000
    assert row.checksum_sha256 == "0" * 64
    assert row.file_path == "/tmp/vep_bundle.db"
    assert row.downloaded_at is not None


def test_record_db_version_upgrades_v1_to_v2(reference_engine: sa.Engine) -> None:
    """Upgrading the recorded version from v1.0.0 → v2.0.0 leaves a single row."""
    _record_db_version(
        reference_engine,
        db_name="vep_bundle",
        version="v1.0.0",
        file_size_bytes=12_000_000,
    )
    _record_db_version(
        reference_engine,
        db_name="vep_bundle",
        version="v2.0.0",
        file_size_bytes=600_000_000,
        sha256="0" * 64,
    )

    with reference_engine.connect() as conn:
        rows = conn.execute(
            sa.select(database_versions).where(database_versions.c.db_name == "vep_bundle")
        ).fetchall()

    assert len(rows) == 1
    assert rows[0].version == "v2.0.0"
    assert rows[0].file_size_bytes == 600_000_000
    assert rows[0].checksum_sha256 == "0" * 64


def test_vep_bundle_registry_entry_reflects_v2_0_0_sizing() -> None:
    """`DATABASES['vep_bundle'].expected_size_bytes` was bumped to the v2.0.0 footprint.

    Step 15 raised the expected size from ~12 MB to ~600 MB to fit the union
    catalog. This closure-step assertion keeps the registry entry pinned so a
    future edit can't silently roll back to the pre-v2.0.0 sizing.
    """
    from backend.db.database_registry import DATABASES

    entry = DATABASES["vep_bundle"]
    assert entry.expected_size_bytes >= 500_000_000
    assert "AncestryDNA" in entry.description


def test_vep_bundle_registry_url_points_at_v2_0_0_release() -> None:
    """Phase 0i (PR-0z) rewrites the fallback URL from the non-existent
    ``raw.githubusercontent.com/.../bundles/vep_bundle.db`` path to the v2.0.0
    GitHub Release asset, so the manifest-CDN-outage fallback actually resolves.
    """
    from backend.db.database_registry import DATABASES

    entry = DATABASES["vep_bundle"]
    assert entry.url.endswith("/releases/download/bundle-v2.0.0/vep_bundle.db")
    assert "raw.githubusercontent.com" not in entry.url
