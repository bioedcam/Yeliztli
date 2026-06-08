"""Add ``gnomad_gene_constraint`` table (LOEUF / pLI / missense-z).

EXPANSION_STRATEGY.md §7 / roadmap #12. Forward-only additive schema change:
a new gene-keyed table in ``reference.db`` holding gnomAD v2.1.1 (GRCh37, CC0)
loss-of-function constraint metrics, used to attach a "this gene doesn't
tolerate loss-of-function" *context* badge to monogenic findings. The table is
also declared in ``backend/db/tables.py`` and created at runtime by
``reference_metadata.create_all(checkfirst=True)``; this migration keeps the
Alembic history complete.

``lof_constrained`` is intentionally NOT stored — it is derived at lookup
(``loeuf < 0.35 or pli > 0.9``) so the table holds only raw metrics.

Downgrade drops the table.

Revision ID: 010
Revises: 009
Create Date: 2026-06-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "010"
down_revision: str = "009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gnomad_gene_constraint",
        sa.Column("gene_symbol", sa.Text, primary_key=True),
        sa.Column("transcript", sa.Text),
        sa.Column("oe_lof", sa.Float),
        sa.Column("loeuf", sa.Float),
        sa.Column("pli", sa.Float),
        sa.Column("mis_z", sa.Float),
        sa.Column("syn_z", sa.Float),
    )
    op.create_index(
        "idx_gnomad_constraint_loeuf",
        "gnomad_gene_constraint",
        ["loeuf"],
    )


def downgrade() -> None:
    op.drop_index("idx_gnomad_constraint_loeuf", table_name="gnomad_gene_constraint")
    op.drop_table("gnomad_gene_constraint")
