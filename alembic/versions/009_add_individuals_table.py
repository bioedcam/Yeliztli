"""Add ``individuals`` table + nullable ``samples.individual_id`` FK.

Phase 2 of the AncestryDNA integration (Plan §9.2). Forward-only schema
addition: a new ``individuals`` table groups one or more ``samples`` rows
under a single biological subject (e.g., a 23andMe export plus an
AncestryDNA export from the same person). Existing samples remain
unlinked (``individual_id IS NULL``) and surface in the UI under an
"Unassigned" group.

Schema changes (additive; backward compatible):

1. Create ``individuals`` table.
2. Add nullable ``samples.individual_id`` column with
   ``FOREIGN KEY(individual_id) REFERENCES individuals(id) ON DELETE SET NULL``.
3. Create ``ix_samples_individual_id`` for fast per-individual sample
   lookups in the two-level sample selector.

Downgrade reverses the additions in inverse order.

Revision ID: 009
Revises: 008
Create Date: 2026-05-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "009"
down_revision: str = "008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "individuals",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("notes", sa.Text, server_default=""),
        sa.Column(
            "biological_sex",
            sa.Text,
            comment="'XX' | 'XY' | NULL — inferred or user-set",
        ),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime),
        sa.CheckConstraint(
            "biological_sex IN ('XX', 'XY') OR biological_sex IS NULL",
            name="ck_individuals_biological_sex",
        ),
    )

    # SQLite cannot ADD CONSTRAINT separately, so the FK has to be declared
    # inline with the column via batch_alter_table's copy-and-move strategy.
    # The constraint is named so Alembic batch mode can round-trip it.
    with op.batch_alter_table("samples") as batch_op:
        batch_op.add_column(
            sa.Column(
                "individual_id",
                sa.Integer,
                sa.ForeignKey(
                    "individuals.id",
                    name="fk_samples_individual_id",
                    ondelete="SET NULL",
                ),
                nullable=True,
            ),
        )

    op.create_index(
        "ix_samples_individual_id",
        "samples",
        ["individual_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_samples_individual_id", table_name="samples")
    with op.batch_alter_table("samples") as batch_op:
        batch_op.drop_column("individual_id")
    op.drop_table("individuals")
