"""Tests for the gnomAD gene-constraint loader (backend.annotation.gnomad_constraint)."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.annotation.gnomad_constraint import (
    load_constraint_from_csv,
    record_constraint_version,
)
from backend.db.tables import database_versions, gnomad_gene_constraint, reference_metadata

_SEED_CSV = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "seed_csvs"
    / "gnomad_constraint_seed.csv"
)


@pytest.fixture()
def engine() -> sa.Engine:
    eng = sa.create_engine("sqlite://")
    reference_metadata.create_all(eng)
    return eng


def _count(engine: sa.Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(
            sa.select(sa.func.count()).select_from(gnomad_gene_constraint)
        ).scalar()


class TestLoadCsv:
    def test_loads_three_rows(self, engine: sa.Engine) -> None:
        stats = load_constraint_from_csv(_SEED_CSV, engine)
        assert stats.genes_loaded == 3
        assert _count(engine) == 3

    def test_constrained_gene_metrics(self, engine: sa.Engine) -> None:
        load_constraint_from_csv(_SEED_CSV, engine)
        with engine.connect() as conn:
            row = conn.execute(
                sa.select(gnomad_gene_constraint).where(
                    gnomad_gene_constraint.c.gene_symbol == "SCN5A"
                )
            ).fetchone()
        assert row.loeuf == 0.18
        assert row.loeuf < 0.35  # LoF-constrained
        assert row.pli == 1.0

    def test_na_loeuf_stored_as_null(self, engine: sa.Engine) -> None:
        load_constraint_from_csv(_SEED_CSV, engine)
        with engine.connect() as conn:
            row = conn.execute(
                sa.select(gnomad_gene_constraint).where(
                    gnomad_gene_constraint.c.gene_symbol == "GENE_NA"
                )
            ).fetchone()
        assert row.loeuf is None
        assert row.pli is None

    def test_idempotent(self, engine: sa.Engine) -> None:
        load_constraint_from_csv(_SEED_CSV, engine)
        load_constraint_from_csv(_SEED_CSV, engine)
        assert _count(engine) == 3  # INSERT OR REPLACE on gene_symbol PK


class TestVersionRecording:
    def test_records_version(self, engine: sa.Engine) -> None:
        record_constraint_version(engine, version="2.1.1", file_size_bytes=123)
        with engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(
                    database_versions.c.db_name == "gnomad_constraint"
                )
            ).fetchone()
        assert row is not None
        assert row.version == "2.1.1"
