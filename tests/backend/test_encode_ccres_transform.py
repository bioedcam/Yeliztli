"""Tests for the ENCODE cCRE post-download BED→SQLite transform pipeline.

Verifies that the post_download hook in DatabaseInfo correctly converts
a raw BED file into a valid SQLite database with the expected schema.
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from backend.db.database_registry import DATABASES, _build_encode_ccres_db

# Sample BED content (10-column ENCODE format)
SAMPLE_BED = """\
#chrom\tstart\tend\taccession\tscore\tstrand\tthickStart\tthickEnd\titemRgb\tccre_class
chr1\t10000\t10500\tEH38E0000001\t0\t.\t10000\t10500\t255,0,0\tPLS
chr1\t20000\t20800\tEH38E0000002\t0\t.\t20000\t20800\t255,205,0\tpELS
chr2\t30000\t30600\tEH38E0000003\t0\t.\t30000\t30600\t0,176,240\tdELS
chr5\t40000\t40400\tEH38E0000004\t0\t.\t40000\t40400\t0,176,80\tCTCF-only
chrX\t50000\t50300\tEH38E0000005\t0\t.\t50000\t50300\t255,0,157\tDNase-H3K4me3
"""


def test_build_encode_ccres_db(tmp_path: Path) -> None:
    """post_download hook produces a valid SQLite database from BED input."""
    bed_path = tmp_path / "GRCh37-cCREs.bed"
    bed_path.write_text(SAMPLE_BED)
    db_path = tmp_path / "encode_ccres.db"

    _build_encode_ccres_db(bed_path, db_path)

    # DB should be a valid SQLite file
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        # Table exists with correct schema
        rows = conn.execute(
            sa.text("SELECT accession, chrom, start_pos, end_pos, ccre_class FROM encode_ccres")
        ).fetchall()
        assert len(rows) == 5

        # Check a specific record
        row = conn.execute(
            sa.text("SELECT * FROM encode_ccres WHERE accession = 'EH38E0000001'")
        ).fetchone()
        assert row is not None
        assert row[1] == "1"  # chrom normalized (no 'chr' prefix)
        assert row[2] == 10000  # start_pos
        assert row[3] == 10500  # end_pos
        assert row[4] == "PLS"  # ccre_class

        # Indexes should exist
        indexes = conn.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='index'")
        ).fetchall()
        index_names = {r[0] for r in indexes}
        assert "idx_ccres_region" in index_names
        assert "idx_ccres_class" in index_names

        # Version table should be populated
        version = conn.execute(sa.text("SELECT * FROM encode_ccres_version")).fetchone()
        assert version is not None
        assert version[2] == 5  # record_count

    engine.dispose()

    # Raw BED file should be removed
    assert not bed_path.exists()


def test_build_encode_ccres_db_empty_bed(tmp_path: Path) -> None:
    """Hook handles an empty BED file gracefully."""
    bed_path = tmp_path / "empty.bed"
    bed_path.write_text("# header only\n")
    db_path = tmp_path / "encode_ccres.db"

    _build_encode_ccres_db(bed_path, db_path)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        count = conn.execute(sa.text("SELECT COUNT(*) FROM encode_ccres")).scalar()
        assert count == 0
    engine.dispose()

    assert not bed_path.exists()


def test_encode_ccres_database_info_has_post_download() -> None:
    """The encode_ccres entry in DATABASES has a post_download hook."""
    db_info = DATABASES["encode_ccres"]
    assert db_info.post_download is not None
    assert db_info.post_download is _build_encode_ccres_db


def test_other_databases_have_no_post_download() -> None:
    """Other databases should not have a post_download hook."""
    for name, db_info in DATABASES.items():
        if name in ("encode_ccres", "lai_bundle"):
            continue
        assert db_info.post_download is None, f"{name} should not have post_download"
