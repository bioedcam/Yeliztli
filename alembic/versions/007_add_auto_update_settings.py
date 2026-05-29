"""Add auto_update_settings table; seed defaults; backfill bundle versions.

Adds the per-database auto-update toggle table introduced in plan §3.5,
seeds one row per ``AUTO_UPDATE_DEFAULTS`` key, and backfills
``database_versions`` rows for the ``lai_bundle`` and ``encode_ccres``
artifacts that may already be on disk from earlier installs.

Backfill rationale: existing installs may have an extracted LAI bundle or
an ENCODE cCREs SQLite DB on disk but no ``database_versions`` row because
the recording paths were added later in this stage. Without this backfill
the Update Manager would treat them as "Not installed" and force a
re-download. Inserting ``version="unknown-pre-manifest"`` keeps them
visible without re-downloading.

Revision ID: 007
Revises: 006
Create Date: 2026-05-08
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "007"
down_revision: str = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Snapshot of AUTO_UPDATE_DEFAULTS at migration authoring time. Inlined so
# the migration stays stable even if the runtime dict shifts later.
AUTO_UPDATE_DEFAULTS_SNAPSHOT: dict[str, bool] = {
    "clinvar": True,
    "gwas_catalog": True,
    "gnomad": True,
    "dbnsfp": True,
    "dbsnp": True,
    "mondo_hpo": True,
    "vep_bundle": False,
    "cpic": True,
    "encode_ccres": True,
    "ancestry_pca": True,
}


def _data_dir_from_bind(bind: sa.Connection) -> Path | None:
    """Derive the data directory (parent of reference.db) from the bind URL."""
    db_path = bind.engine.url.database
    if not db_path or db_path == ":memory:":
        return None
    return Path(db_path).resolve().parent


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ── Create auto_update_settings table (idempotent) ────────────────
    if "auto_update_settings" not in inspector.get_table_names():
        op.create_table(
            "auto_update_settings",
            sa.Column("db_name", sa.Text, primary_key=True),
            sa.Column("enabled", sa.Boolean, nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )

    auto_update_settings = sa.table(
        "auto_update_settings",
        sa.column("db_name", sa.Text),
        sa.column("enabled", sa.Boolean),
        sa.column("updated_at", sa.DateTime),
    )

    # ── Seed default rows (idempotent) ────────────────────────────────
    existing_names = {
        row[0] for row in bind.execute(sa.select(auto_update_settings.c.db_name)).fetchall()
    }
    now = datetime.now(UTC)
    new_rows = [
        {"db_name": name, "enabled": enabled, "updated_at": now}
        for name, enabled in AUTO_UPDATE_DEFAULTS_SNAPSHOT.items()
        if name not in existing_names
    ]
    if new_rows:
        bind.execute(sa.insert(auto_update_settings), new_rows)

    # ── Backfill database_versions for pre-manifest installs ──────────
    data_dir = _data_dir_from_bind(bind)
    if data_dir is None:
        return

    database_versions = sa.table(
        "database_versions",
        sa.column("db_name", sa.Text),
        sa.column("version", sa.Text),
        sa.column("file_path", sa.Text),
        sa.column("file_size_bytes", sa.Integer),
        sa.column("downloaded_at", sa.DateTime),
        sa.column("checksum_sha256", sa.Text),
    )

    recorded = {row[0] for row in bind.execute(sa.select(database_versions.c.db_name)).fetchall()}

    backfill_rows: list[dict[str, object]] = []

    lai_dir = data_dir / "lai_bundle"
    if "lai_bundle" not in recorded and lai_dir.is_dir():
        size = sum(p.stat().st_size for p in lai_dir.rglob("*") if p.is_file())
        mtime = datetime.fromtimestamp(lai_dir.stat().st_mtime, tz=UTC)
        backfill_rows.append(
            {
                "db_name": "lai_bundle",
                "version": "unknown-pre-manifest",
                "file_path": str(lai_dir),
                "file_size_bytes": size,
                "downloaded_at": mtime,
                "checksum_sha256": None,
            }
        )

    encode_path = data_dir / "encode_ccres.db"
    if "encode_ccres" not in recorded and encode_path.is_file():
        stat = encode_path.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        backfill_rows.append(
            {
                "db_name": "encode_ccres",
                "version": "unknown-pre-manifest",
                "file_path": str(encode_path),
                "file_size_bytes": stat.st_size,
                "downloaded_at": mtime,
                "checksum_sha256": None,
            }
        )

    if backfill_rows:
        bind.execute(sa.insert(database_versions), backfill_rows)


def downgrade() -> None:
    # Drop the table; intentionally leave any backfilled
    # database_versions rows in place — they represent real on-disk state.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "auto_update_settings" in inspector.get_table_names():
        op.drop_table("auto_update_settings")
