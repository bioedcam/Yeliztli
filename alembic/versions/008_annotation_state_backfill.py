"""Backfill per-sample ``annotation_state`` with ``vep_bundle_version='v1.0.0'``.

Phase 0 of the AncestryDNA integration (Plan §7.4 step 2, §17.1).

This is a **per-sample** backfill — there is no reference-DB schema change.
For every row in ``samples``, the migration opens the per-sample SQLite at
``samples.db_path`` and:

1. Creates ``annotation_state(key TEXT PK, value TEXT NOT NULL, updated_at TIMESTAMP)``
   if it does not already exist (``CREATE TABLE IF NOT EXISTS``).
2. Runs ``INSERT OR IGNORE`` to seed
   ``key='vep_bundle_version', value='v1.0.0'`` — preserving any row that a
   freshly re-annotated sample may have written already.

Unreachable / non-SQLite / corrupt per-sample DBs are logged with a
``alembic_008_sample_db_skipped`` structured warning and skipped; the
migration does not raise.

Idempotent: re-running upgrades is a no-op (``IF NOT EXISTS`` + ``INSERT OR
IGNORE``). Downgrade intentionally leaves the per-sample ``annotation_state``
rows in place — they reflect real provenance and the staleness service
needs them.

Revision ID: 008
Revises: 007
Create Date: 2026-05-19
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
import structlog

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "008"
down_revision: str = "007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


logger = structlog.get_logger(__name__)

_FALLBACK_VEP_VERSION = "v1.0.0"

_CREATE_ANNOTATION_STATE = (
    "CREATE TABLE IF NOT EXISTS annotation_state ("
    "key TEXT PRIMARY KEY, "
    "value TEXT NOT NULL, "
    "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
    ")"
)

_BACKFILL_VEP_VERSION = (
    "INSERT OR IGNORE INTO annotation_state (key, value) "
    "VALUES ('vep_bundle_version', :v)"
)


def _data_dir_from_bind(bind: sa.Connection) -> Path | None:
    """Derive the data directory (parent of reference.db) from the bind URL."""
    db_path = bind.engine.url.database
    if not db_path or db_path == ":memory:":
        return None
    return Path(db_path).resolve().parent


def _resolve_sample_db_path(stored: str, data_dir: Path | None) -> Path:
    """Resolve a ``samples.db_path`` value to a concrete path.

    Absolute paths are honoured verbatim. Relative paths are resolved against
    the data directory (the parent of ``reference.db``).
    """
    path = Path(stored)
    if path.is_absolute() or data_dir is None:
        return path
    return (data_dir / path).resolve()


def _backfill_one(sample_db_path: Path) -> None:
    """Create ``annotation_state`` and seed ``vep_bundle_version`` for one DB.

    Skipped (with a structured warning) for any DB that fails to open or
    fails the DDL/DML — corruption, non-SQLite blobs, permission errors —
    so a single bad sample DB cannot fail the migration.
    """
    if not sample_db_path.exists():
        logger.warning(
            "alembic_008_sample_db_skipped",
            sample_db=str(sample_db_path),
            reason="missing",
        )
        return

    engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(sa.text(_CREATE_ANNOTATION_STATE))
            conn.execute(
                sa.text(_BACKFILL_VEP_VERSION),
                {"v": _FALLBACK_VEP_VERSION},
            )
    except sa.exc.SQLAlchemyError as exc:
        logger.warning(
            "alembic_008_sample_db_skipped",
            sample_db=str(sample_db_path),
            reason="sqlalchemy_error",
            error=str(exc),
        )
    finally:
        engine.dispose()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "samples" not in inspector.get_table_names():
        # Fresh DB with no samples table yet — nothing to backfill.
        return

    data_dir = _data_dir_from_bind(bind)

    samples = sa.table(
        "samples",
        sa.column("id", sa.Integer),
        sa.column("db_path", sa.Text),
    )
    rows = bind.execute(sa.select(samples.c.id, samples.c.db_path)).fetchall()

    for sample_id, stored_path in rows:
        if not stored_path:
            logger.warning(
                "alembic_008_sample_db_skipped",
                sample_id=sample_id,
                reason="empty_db_path",
            )
            continue
        sample_db_path = _resolve_sample_db_path(stored_path, data_dir)
        _backfill_one(sample_db_path)


def downgrade() -> None:
    # No reference-DB schema change to revert. Per-sample ``annotation_state``
    # rows are intentionally left in place — the staleness service relies on
    # them and they reflect real provenance.
    pass
