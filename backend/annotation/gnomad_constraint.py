"""gnomAD gene-constraint (LOEUF / pLI / missense-z) loader.

EXPANSION_STRATEGY.md §7 / roadmap #12. Downloads/loads the gnomAD v2.1.1
(GRCh37, CC0 — redistributable) ``lof_metrics.by_gene`` table into the
``gnomad_gene_constraint`` table in ``reference.db``. Mirrors
:mod:`backend.annotation.gnomad`: a thin, idempotent (``INSERT OR REPLACE``)
loader with a CSV path for fixtures, a streaming downloader, and a
``database_versions`` row.

v2.1.1 is GRCh37, matching the consumer-array build and the existing gnomAD AF
pin (Karczewski 2020, *Nature*; PMID 32461654). The constraint table is
gene-keyed and read by :mod:`backend.analysis.gene_constraint`.
"""

from __future__ import annotations

import csv
import gzip
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import sqlalchemy as sa
import structlog

from backend.annotation.bulk_load import bulk_write_connection, execute_write, insert_batch
from backend.annotation.http_download import stream_download

if TYPE_CHECKING:
    from collections.abc import Callable

logger = structlog.get_logger(__name__)

# gnomAD v2.1.1 LoF-metrics-by-gene table (GRCh37, CC0).
GNOMAD_CONSTRAINT_URL = (
    "https://storage.googleapis.com/gcp-public-data--gnomad/"
    "release/2.1.1/constraint/"
    "gnomad.v2.1.1.lof_metrics.by_gene.txt.bgz"
)

GNOMAD_CONSTRAINT_VERSION = "2.1.1"

BATCH_SIZE = 5_000

CREATE_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS gnomad_gene_constraint (
    gene_symbol TEXT PRIMARY KEY,
    transcript  TEXT,
    oe_lof      REAL,
    loeuf       REAL,
    pli         REAL,
    mis_z       REAL,
    syn_z       REAL
)
"""

CREATE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_gnomad_constraint_loeuf ON gnomad_gene_constraint (loeuf)"
)

_INSERT_SQL = sa.text(
    "INSERT OR REPLACE INTO gnomad_gene_constraint "
    "(gene_symbol, transcript, oe_lof, loeuf, pli, mis_z, syn_z) "
    "VALUES (:gene_symbol, :transcript, :oe_lof, :loeuf, :pli, :mis_z, :syn_z)"
)


@dataclass
class ConstraintLoadStats:
    total_lines: int = 0
    genes_loaded: int = 0
    skipped: int = 0
    duplicate_genes: int = 0


def _parse_float(value: str | None) -> float | None:
    """Parse a gnomAD numeric cell; ``NA``/empty → ``None``."""
    if value is None:
        return None
    v = value.strip()
    if not v or v.upper() == "NA":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def create_constraint_table(engine: sa.Engine) -> None:
    """Create the constraint table + LOEUF index if absent (idempotent)."""
    with bulk_write_connection(engine) as conn:
        execute_write(conn, sa.text(CREATE_TABLE_SQL))
        execute_write(conn, sa.text(CREATE_INDEX_SQL))


def _row_to_record(row: dict[str, str]) -> dict | None:
    """Map a gnomAD by_gene row to a constraint record, or None to skip.

    Keyed on ``gene`` (HGNC symbol). LOEUF = ``oe_lof_upper``.
    """
    gene = (row.get("gene") or "").strip()
    if not gene:
        return None
    return {
        "gene_symbol": gene,
        "transcript": (row.get("transcript") or "").strip() or None,
        "oe_lof": _parse_float(row.get("oe_lof")),
        "loeuf": _parse_float(row.get("oe_lof_upper")),
        "pli": _parse_float(row.get("pLI")),
        "mis_z": _parse_float(row.get("mis_z")),
        "syn_z": _parse_float(row.get("syn_z")),
    }


def _load_rows(reader: csv.DictReader, engine: sa.Engine) -> ConstraintLoadStats:
    stats = ConstraintLoadStats()
    batch: list[dict] = []
    seen: set[str] = set()
    with bulk_write_connection(engine) as conn:
        for row in reader:
            stats.total_lines += 1
            record = _row_to_record(row)
            if record is None:
                stats.skipped += 1
                continue
            # The by_gene file is already one canonical row per gene; this dedup
            # keeps the first occurrence as a safety net. If the upstream file ever
            # ships multiple rows per gene, the "first wins" choice is order-
            # dependent — so we count and warn rather than silently picking one.
            if record["gene_symbol"] in seen:
                stats.skipped += 1
                stats.duplicate_genes += 1
                continue
            seen.add(record["gene_symbol"])
            batch.append(record)
            stats.genes_loaded += 1
            if len(batch) >= BATCH_SIZE:
                insert_batch(conn, _INSERT_SQL, batch)
                batch = []
        if batch:
            insert_batch(conn, _INSERT_SQL, batch)
    if stats.duplicate_genes:
        logger.warning(
            "gnomad_constraint_duplicate_genes",
            duplicates=stats.duplicate_genes,
            note="upstream file had >1 row per gene; kept first occurrence (order-dependent)",
        )
    return stats


def load_constraint_from_tsv(tsv_path: Path, engine: sa.Engine) -> ConstraintLoadStats:
    """Load the gnomAD by_gene constraint TSV (optionally gzip/bgzip) into the table."""
    create_constraint_table(engine)
    opener: Callable = gzip.open if str(tsv_path).endswith((".gz", ".bgz")) else open
    with opener(tsv_path, "rt", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        stats = _load_rows(reader, engine)
    logger.info("gnomad_constraint_tsv_loaded", genes=stats.genes_loaded, skipped=stats.skipped)
    return stats


def load_constraint_from_csv(csv_path: Path, engine: sa.Engine) -> ConstraintLoadStats:
    """Load a CSV seed file (gene,transcript,oe_lof,loeuf,pli,mis_z,syn_z) — for tests/fixtures."""
    create_constraint_table(engine)
    batch: list[dict] = []
    stats = ConstraintLoadStats()
    with (
        open(csv_path, encoding="utf-8") as f,
        bulk_write_connection(engine) as conn,
    ):
        reader = csv.DictReader(f)
        for row in reader:
            stats.total_lines += 1
            gene = (row.get("gene_symbol") or row.get("gene") or "").strip()
            if not gene:
                stats.skipped += 1
                continue
            batch.append(
                {
                    "gene_symbol": gene,
                    "transcript": (row.get("transcript") or "").strip() or None,
                    "oe_lof": _parse_float(row.get("oe_lof")),
                    "loeuf": _parse_float(row.get("loeuf")),
                    "pli": _parse_float(row.get("pli")),
                    "mis_z": _parse_float(row.get("mis_z")),
                    "syn_z": _parse_float(row.get("syn_z")),
                }
            )
            stats.genes_loaded += 1
        if batch:
            insert_batch(conn, _INSERT_SQL, batch)
    logger.info("gnomad_constraint_csv_loaded", genes=stats.genes_loaded)
    return stats


def download_constraint(
    dest_dir: Path,
    *,
    url: str = GNOMAD_CONSTRAINT_URL,
    progress_callback: Callable[[int, int | None], None] | None = None,
    timeout: float = 600.0,
) -> Path:
    """Download the gnomAD constraint table to ``dest_dir`` (atomic rename on success)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / "gnomad.v2.1.1.lof_metrics.by_gene.txt.bgz"
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")
    logger.info("gnomad_constraint_download_start", url=url)
    stream_download(
        url,
        tmp_path,
        progress_callback=progress_callback,
        timeout=timeout,
        resumable=False,
    )
    tmp_path.rename(dest_path)
    return dest_path


def record_constraint_version(
    engine: sa.Engine,
    *,
    version: str = GNOMAD_CONSTRAINT_VERSION,
    file_path: str | None = None,
    file_size_bytes: int | None = None,
    checksum: str | None = None,
) -> None:
    """Record the gnomAD constraint version in ``database_versions``."""
    from backend.db.database_registry import _record_db_version

    _record_db_version(
        engine,
        db_name="gnomad_constraint",
        version=version,
        file_size_bytes=file_size_bytes,
        sha256=checksum,
        file_path=file_path,
    )
