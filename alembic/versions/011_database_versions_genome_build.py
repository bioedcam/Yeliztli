"""Add ``database_versions.genome_build`` (F30 cross-source provenance).

Phase-F annotation-validation follow-up. Records the genome build of each
reference source's coordinates so a stored finding can be tied to the assembly
that produced it. Forward-only additive column on the reference DB: NULL for
build-agnostic / gene-keyed sources (dbsnp merge history, mondo_hpo, omim,
lai_bundle, ancestry_pca), ``GRCh37`` for the live GRCh37 pipeline sources
(clinvar, gnomad, gwas_catalog, cpic, vep_bundle, gnomad_constraint) and
``GRCh38`` for dbNSFP — making F35's legitimate cross-build coordinate explicit
in the manifest.

The column is also declared in ``backend/db/tables.py`` and is created at
runtime for fresh DBs via ``reference_metadata.create_all(checkfirst=True)`` /
backfilled onto pre-existing DBs by ``ensure_reference_schema_current``; this
migration keeps the Alembic history complete.

Downgrade drops the column.

Revision ID: 011
Revises: 010
Create Date: 2026-06-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "011"
down_revision: str = "010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "database_versions",
        sa.Column("genome_build", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("database_versions", "genome_build")
