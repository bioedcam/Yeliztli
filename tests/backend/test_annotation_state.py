"""Tests for the per-sample ``annotation_state`` kv table (Plan §7.1).

Locks two contracts:

1. A fresh sample DB created via ``create_sample_tables`` materialises
   ``annotation_state`` with the columns + primary key from the plan.
2. Reopening / re-initialising an existing sample DB is a no-op for
   ``annotation_state`` — ``checkfirst=True`` preserves any rows already
   written by a prior pipeline pass.
"""

from __future__ import annotations

import sqlalchemy as sa

from backend.db.sample_schema import (
    create_sample_tables,
    ensure_sample_schema_current,
)
from backend.db.tables import annotation_state, sample_metadata_obj


class TestAnnotationStateSchema:
    def test_table_registered_on_sample_metadata(self):
        assert "annotation_state" in sample_metadata_obj.tables
        assert sample_metadata_obj.tables["annotation_state"] is annotation_state

    def test_columns(self):
        col_names = [c.name for c in annotation_state.columns]
        assert col_names == ["key", "value", "updated_at"]

    def test_primary_key(self):
        pk_cols = [c.name for c in annotation_state.primary_key.columns]
        assert pk_cols == ["key"]

    def test_value_not_null(self):
        cols = {c.name: c for c in annotation_state.columns}
        assert cols["value"].nullable is False

    def test_column_types(self):
        cols = {c.name: c for c in annotation_state.columns}
        assert isinstance(cols["key"].type, sa.Text)
        assert isinstance(cols["value"].type, sa.Text)
        assert isinstance(cols["updated_at"].type, sa.DateTime)


class TestAnnotationStateLifecycle:
    def test_fresh_db_creates_annotation_state(self):
        engine = sa.create_engine("sqlite://")
        create_sample_tables(engine)

        inspector = sa.inspect(engine)
        assert "annotation_state" in inspector.get_table_names()

        cols = {c["name"] for c in inspector.get_columns("annotation_state")}
        assert cols == {"key", "value", "updated_at"}

    def test_reopen_is_noop_and_preserves_rows(self):
        engine = sa.create_engine("sqlite://")
        create_sample_tables(engine)

        with engine.begin() as conn:
            conn.execute(
                annotation_state.insert().values(
                    key="vep_bundle_version",
                    value="v1.0.0",
                )
            )

        # Simulate the schema-current path called on every sample-DB open.
        ensure_sample_schema_current(engine)

        with engine.connect() as conn:
            row = conn.execute(
                sa.select(
                    annotation_state.c.key,
                    annotation_state.c.value,
                ).where(annotation_state.c.key == "vep_bundle_version")
            ).one()

        assert row.key == "vep_bundle_version"
        assert row.value == "v1.0.0"

    def test_create_all_checkfirst_is_idempotent(self):
        engine = sa.create_engine("sqlite://")
        create_sample_tables(engine)

        # Calling create_all again with checkfirst=True must not raise
        # (would raise OperationalError "table already exists" without it).
        sample_metadata_obj.create_all(engine, checkfirst=True)

        inspector = sa.inspect(engine)
        assert "annotation_state" in inspector.get_table_names()
