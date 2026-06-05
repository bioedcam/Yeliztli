"""dbNSFP SQLite loader and annotation lookup.

Downloads dbNSFP 5.x academic TSV (ZIP archive), extracts missense in-silico prediction
scores, and builds an indexed SQLite database (``dbnsfp.db``) with a
composite ``(chrom, pos, ref, alt)`` key.  Also provides batch lookup
functions used by the annotation engine.

The ``dbnsfp_scores`` table stores one row per variant with columns:
rsid, chrom, pos, ref, alt, cadd_phred, sift_score, sift_pred,
polyphen2_hsvar_score, polyphen2_hsvar_pred, revel, mutpred2, vest4,
metasvm, metalr, gerp_rs, phylop, mpc, primateai.

Usage::

    from backend.annotation.dbnsfp import (
        download_dbnsfp,
        load_dbnsfp_from_tsv,
        load_dbnsfp_from_csv,
        lookup_dbnsfp_by_rsids,
    )

    tsv_path = download_dbnsfp(dest_dir)
    stats = load_dbnsfp_from_tsv(tsv_path, dbnsfp_engine)
    matches = lookup_dbnsfp_by_rsids(["rs429358"], dbnsfp_engine)
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import sqlalchemy as sa
import structlog

from backend.annotation.http_download import stream_download_with_resume
from backend.annotation.sqlite_limits import SQLITE_MAX_VARIABLE_NUMBER as _SQLITE_VAR_LIMIT

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

logger = structlog.get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────

# dbNSFP 5.x academic download URL (TSV archive, ~50 GB)
# The distribution is a ZIP containing per-chromosome TSV files.
# download_and_load_dbnsfp() streams these through csv.DictReader.
DBNSFP_TSV_URL = "https://dist.genos.us/academic/e55b09/dbNSFP5.3.1a.zip"
DBNSFP_PREBUILT_URL = "https://github.com/GenomeInsight/data/releases/download/v1.0/dbnsfp.db.gz"

# Batch sizes
BATCH_SIZE = 10_000
# Default lookup batch sizes; upgraded at module load when SQLite supports
# a higher SQLITE_MAX_VARIABLE_NUMBER (typically 32766 on Linux vs 999 on
# macOS system SQLite).  Larger batches reduce round-trip overhead.
LOOKUP_BATCH_SIZE = max(500, _SQLITE_VAR_LIMIT - 10)
POSITION_LOOKUP_BATCH_SIZE = max(249, (_SQLITE_VAR_LIMIT - 10) // 4)

# Chromosomes we accept (matching 23andMe scope)
VALID_CHROMS = {str(i) for i in range(1, 23)} | {"X", "Y", "MT"}

# dbNSFP annotation bitmask bit (bit 3, value 8)
DBNSFP_BITMASK = 0b001000

# dbNSFP TSV column mappings: dbNSFP column name → our column name
_TSV_COLUMN_MAP = {
    "rs_dbSNP": "rsid",
    "#chr": "chrom",
    "pos(1-based)": "pos",
    "ref": "ref",
    "alt": "alt",
    "CADD_phred": "cadd_phred",
    "SIFT4G_score": "sift_score",
    "SIFT4G_pred": "sift_pred",
    "Polyphen2_HVAR_score": "polyphen2_hsvar_score",
    "Polyphen2_HVAR_pred": "polyphen2_hsvar_pred",
    "REVEL_score": "revel",
    "MutPred_score": "mutpred2",
    "VEST4_score": "vest4",
    "MetaSVM_score": "metasvm",
    "MetaLR_score": "metalr",
    "GERP++_RS": "gerp_rs",
    "phyloP100way_vertebrate": "phylop",
    "MPC_score": "mpc",
    "PrimateAI_score": "primateai",
}

# Score columns (float values)
_SCORE_COLUMNS = [
    "cadd_phred",
    "sift_score",
    "polyphen2_hsvar_score",
    "revel",
    "mutpred2",
    "vest4",
    "metasvm",
    "metalr",
    "gerp_rs",
    "phylop",
    "mpc",
    "primateai",
]

# Prediction columns (text values)
_PRED_COLUMNS = [
    "sift_pred",
    "polyphen2_hsvar_pred",
]

# All dbNSFP score field names (for lookup results)
DBNSFP_FIELDS = (
    "cadd_phred",
    "sift_score",
    "sift_pred",
    "polyphen2_hsvar_score",
    "polyphen2_hsvar_pred",
    "revel",
    "mutpred2",
    "vest4",
    "metasvm",
    "metalr",
    "gerp_rs",
    "phylop",
    "mpc",
    "primateai",
)


# ── SQL for dbnsfp_scores table creation ──────────────────────────────────

CREATE_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS dbnsfp_scores (
    rsid                    TEXT,
    chrom                   TEXT NOT NULL,
    pos                     INTEGER NOT NULL,
    ref                     TEXT NOT NULL,
    alt                     TEXT NOT NULL,
    cadd_phred              REAL,
    sift_score              REAL,
    sift_pred               TEXT,
    polyphen2_hsvar_score   REAL,
    polyphen2_hsvar_pred    TEXT,
    revel                   REAL,
    mutpred2                REAL,
    vest4                   REAL,
    metasvm                 REAL,
    metalr                  REAL,
    gerp_rs                 REAL,
    phylop                  REAL,
    mpc                     REAL,
    primateai               REAL,
    PRIMARY KEY (chrom, pos, ref, alt)
)
"""

CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_dbnsfp_rsid ON dbnsfp_scores (rsid)",
    "CREATE INDEX IF NOT EXISTS idx_dbnsfp_chrom_pos ON dbnsfp_scores (chrom, pos)",
    # Covering index for rsid lookups (P4-22): includes all score columns so
    # the query can be satisfied entirely from the index without hitting the
    # main table.  This eliminates random I/O on the ~1.5 GB main table for
    # the primary (rsid-based) lookup path.
    (
        "CREATE INDEX IF NOT EXISTS idx_dbnsfp_rsid_covering ON dbnsfp_scores "
        "(rsid, chrom, pos, ref, alt, cadd_phred, sift_score, sift_pred, "
        "polyphen2_hsvar_score, polyphen2_hsvar_pred, revel, mutpred2, vest4, "
        "metasvm, metalr, gerp_rs, phylop, mpc, primateai)"
    ),
]


# ── Data classes ─────────────────────────────────────────────────────────


@dataclass
class DbNSFPRecord:
    """A single parsed dbNSFP variant record."""

    rsid: str | None
    chrom: str
    pos: int
    ref: str
    alt: str
    cadd_phred: float | None = None
    sift_score: float | None = None
    sift_pred: str | None = None
    polyphen2_hsvar_score: float | None = None
    polyphen2_hsvar_pred: str | None = None
    revel: float | None = None
    mutpred2: float | None = None
    vest4: float | None = None
    metasvm: float | None = None
    metalr: float | None = None
    gerp_rs: float | None = None
    phylop: float | None = None
    mpc: float | None = None
    primateai: float | None = None


@dataclass
class LoadStats:
    """Statistics from a dbNSFP load operation."""

    total_lines: int = 0
    variants_loaded: int = 0
    skipped_no_rsid: int = 0
    skipped_invalid_chrom: int = 0
    skipped_malformed: int = 0
    skipped_no_scores: int = 0
    sha256: str | None = None


@dataclass
class DbNSFPAnnotation(DbNSFPRecord):
    """dbNSFP annotation data with computed deleterious count."""

    deleterious_count: int = field(init=False)

    def __post_init__(self) -> None:
        self.deleterious_count = count_deleterious(self)


# ── Ensemble pathogenicity helpers ──────────────────────────────────────


def count_deleterious(annot: DbNSFPAnnotation) -> int:
    """Count the number of in-silico tools predicting deleterious effect.

    Thresholds follow standard cutoffs:
        - SIFT4G: score < 0.05 (D)
        - PolyPhen-2 HVAR: score > 0.453 (P or D)
        - CADD: phred ≥ 20
        - REVEL: score ≥ 0.5
        - MetaSVM: score > 0 (D)

    Returns:
        Number of tools predicting deleterious (0-5).
    """
    count = 0
    if annot.sift_score is not None and annot.sift_score < 0.05:
        count += 1
    if annot.polyphen2_hsvar_score is not None and annot.polyphen2_hsvar_score > 0.453:
        count += 1
    if annot.cadd_phred is not None and annot.cadd_phred >= 20:
        count += 1
    if annot.revel is not None and annot.revel >= 0.5:
        count += 1
    if annot.metasvm is not None and annot.metasvm > 0:
        count += 1
    return count


ENSEMBLE_PATHOGENIC_THRESHOLD = 3


def is_ensemble_pathogenic(annot: DbNSFPAnnotation) -> bool:
    """Check if ≥3 tools predict deleterious (ensemble pathogenicity flag).

    Per PRD P2-13: "≥3 tools predict deleterious → flag set".
    """
    return annot.deleterious_count >= ENSEMBLE_PATHOGENIC_THRESHOLD


# ── Helpers ──────────────────────────────────────────────────────────────


def _normalize_chrom(chrom: str) -> str | None:
    """Normalize chromosome name. Returns None for invalid chromosomes."""
    c = chrom.removeprefix("chr").upper()
    if c in VALID_CHROMS:
        return c
    return None


def _parse_float(value: str | None) -> float | None:
    """Parse a float, returning None on failure or missing sentinel."""
    if value is None or value in (".", "", "-"):
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _parse_dbnsfp_float(value: str | None) -> float | None:
    """Parse a dbNSFP float value that may contain multiple semicolon-delimited scores.

    dbNSFP stores multiple transcript-level scores separated by semicolons.
    We take the first non-missing value (first-transcript approach).
    """
    if value is None or value in (".", "", "-"):
        return None
    # dbNSFP uses ';' to separate multiple transcript scores
    if ";" in value:
        for part in value.split(";"):
            result = _parse_float(part.strip())
            if result is not None:
                return result
        return None
    return _parse_float(value)


def _parse_dbnsfp_pred(value: str | None) -> str | None:
    """Parse a dbNSFP prediction value (may be multi-transcript).

    Takes the first non-missing prediction.
    """
    if value is None or value in (".", "", "-"):
        return None
    if ";" in value:
        for part in value.split(";"):
            part = part.strip()
            if part and part != ".":
                return part
        return None
    return value


def _compute_sha256(file_path: Path) -> str:
    """Compute SHA-256 checksum of a file."""
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest()


def _wal_checkpoint(engine: sa.Engine) -> None:
    """Run WAL checkpoint if the engine is file-backed."""
    url = str(engine.url)
    if url == "sqlite://" or ":memory:" in url:
        return
    with engine.connect() as conn:
        conn.execute(sa.text("PRAGMA wal_checkpoint(TRUNCATE)"))
        conn.commit()


# ── TSV parsing ──────────────────────────────────────────────────────────


def parse_dbnsfp_tsv_line(
    fields: dict[str, str],
) -> tuple[DbNSFPRecord | None, str | None]:
    """Parse a single dbNSFP TSV row (as a dict from DictReader).

    Returns:
        Tuple of (record, skip_reason). If record is None, skip_reason
        indicates why the line was skipped.
    """
    # Extract chromosome
    chrom_raw = fields.get("#chr", "")
    chrom = _normalize_chrom(chrom_raw)
    if chrom is None:
        return None, "invalid_chrom"

    # Extract position
    try:
        pos = int(fields.get("pos(1-based)", ""))
    except (ValueError, TypeError):
        return None, "malformed"

    ref = fields.get("ref", "")
    alt = fields.get("alt", "")
    if not ref or not alt:
        return None, "malformed"

    # Extract rsid
    rsid_raw = fields.get("rs_dbSNP", "")
    rsid: str | None = None
    if rsid_raw and rsid_raw != ".":
        # May have multiple rsids; take first
        if ";" in rsid_raw:
            for part in rsid_raw.split(";"):
                if part.startswith("rs"):
                    rsid = part
                    break
        elif rsid_raw.startswith("rs"):
            rsid = rsid_raw

    # Parse scores
    cadd_phred = _parse_dbnsfp_float(fields.get("CADD_phred"))
    sift_score = _parse_dbnsfp_float(fields.get("SIFT4G_score"))
    sift_pred = _parse_dbnsfp_pred(fields.get("SIFT4G_pred"))
    polyphen2_score = _parse_dbnsfp_float(fields.get("Polyphen2_HVAR_score"))
    polyphen2_pred = _parse_dbnsfp_pred(fields.get("Polyphen2_HVAR_pred"))
    revel = _parse_dbnsfp_float(fields.get("REVEL_score"))
    mutpred2 = _parse_dbnsfp_float(fields.get("MutPred_score"))
    vest4 = _parse_dbnsfp_float(fields.get("VEST4_score"))
    metasvm = _parse_dbnsfp_float(fields.get("MetaSVM_score"))
    metalr = _parse_dbnsfp_float(fields.get("MetaLR_score"))
    gerp_rs = _parse_dbnsfp_float(fields.get("GERP++_RS"))
    phylop = _parse_dbnsfp_float(fields.get("phyloP100way_vertebrate"))
    mpc = _parse_dbnsfp_float(fields.get("MPC_score"))
    primateai = _parse_dbnsfp_float(fields.get("PrimateAI_score"))

    # Skip if no scores at all
    all_scores = [
        cadd_phred,
        sift_score,
        polyphen2_score,
        revel,
        mutpred2,
        vest4,
        metasvm,
        metalr,
        gerp_rs,
        phylop,
        mpc,
        primateai,
    ]
    if all(s is None for s in all_scores) and sift_pred is None and polyphen2_pred is None:
        return None, "no_scores"

    record = DbNSFPRecord(
        rsid=rsid,
        chrom=chrom,
        pos=pos,
        ref=ref,
        alt=alt,
        cadd_phred=cadd_phred,
        sift_score=sift_score,
        sift_pred=sift_pred,
        polyphen2_hsvar_score=polyphen2_score,
        polyphen2_hsvar_pred=polyphen2_pred,
        revel=revel,
        mutpred2=mutpred2,
        vest4=vest4,
        metasvm=metasvm,
        metalr=metalr,
        gerp_rs=gerp_rs,
        phylop=phylop,
        mpc=mpc,
        primateai=primateai,
    )

    return record, None


def _iter_dbnsfp_single_file(
    fh,
    stats: LoadStats,
    progress_callback: Callable[[int], None] | None,
) -> Iterator[tuple[dict, LoadStats]]:
    """Parse rows from a single dbNSFP TSV file handle."""
    reader = csv.DictReader(fh, delimiter="\t")
    for fields in reader:
        stats.total_lines += 1

        record, skip_reason = parse_dbnsfp_tsv_line(fields)

        if record is None:
            if skip_reason == "invalid_chrom":
                stats.skipped_invalid_chrom += 1
            elif skip_reason == "no_scores":
                stats.skipped_no_scores += 1
            else:
                stats.skipped_malformed += 1
            continue

        stats.variants_loaded += 1

        row = _record_to_dict(record)

        if progress_callback and stats.total_lines % 100_000 == 0:
            progress_callback(stats.total_lines)

        yield row, stats


def iter_dbnsfp_tsv(
    tsv_path: Path,
    *,
    progress_callback: Callable[[int], None] | None = None,
) -> Iterator[tuple[dict, LoadStats]]:
    """Iterate over dbNSFP TSV rows lazily, yielding (row_dict, stats).

    Memory-efficient: yields one row at a time for streaming inserts.
    Handles plain text, gzip-compressed, and ZIP archives (containing
    per-chromosome gzipped TSV files as distributed by dbNSFP).

    Args:
        tsv_path: Path to the dbNSFP file (.tsv, .tsv.gz, or .zip).
        progress_callback: Optional callback called with parsed line count
            at regular intervals.

    Yields:
        Tuple of (row dict ready for insert, running LoadStats).
    """
    stats = LoadStats()

    if tsv_path.suffix == ".zip":
        # dbNSFP ZIP archive: contains per-chromosome files like
        # dbNSFP5.3.1a_variant.chr1.gz (gzipped TSVs)
        with zipfile.ZipFile(tsv_path, "r") as zf:
            members = sorted(
                n for n in zf.namelist() if "_variant.chr" in n and not n.startswith("__MACOSX")
            )
            logger.info("dbnsfp_zip_members", count=len(members), files=members[:3])
            for member in members:
                logger.info("dbnsfp_processing_member", member=member)
                with zf.open(member) as raw_fh:
                    if member.endswith(".gz"):
                        fh = io.TextIOWrapper(gzip.open(raw_fh, "rb"), encoding="utf-8")
                    else:
                        fh = io.TextIOWrapper(raw_fh, encoding="utf-8")
                    yield from _iter_dbnsfp_single_file(fh, stats, progress_callback)
    else:
        open_fn = gzip.open if tsv_path.suffix == ".gz" else open
        with open_fn(tsv_path, "rt", encoding="utf-8") as fh:  # type: ignore[call-overload]
            yield from _iter_dbnsfp_single_file(fh, stats, progress_callback)


def _record_to_dict(record: DbNSFPRecord) -> dict:
    """Convert a DbNSFPRecord to a dict suitable for DB insertion."""
    return {
        "rsid": record.rsid,
        "chrom": record.chrom,
        "pos": record.pos,
        "ref": record.ref,
        "alt": record.alt,
        "cadd_phred": record.cadd_phred,
        "sift_score": record.sift_score,
        "sift_pred": record.sift_pred,
        "polyphen2_hsvar_score": record.polyphen2_hsvar_score,
        "polyphen2_hsvar_pred": record.polyphen2_hsvar_pred,
        "revel": record.revel,
        "mutpred2": record.mutpred2,
        "vest4": record.vest4,
        "metasvm": record.metasvm,
        "metalr": record.metalr,
        "gerp_rs": record.gerp_rs,
        "phylop": record.phylop,
        "mpc": record.mpc,
        "primateai": record.primateai,
    }


# ── Database creation & loading ──────────────────────────────────────────


def create_dbnsfp_tables(engine: sa.Engine) -> None:
    """Create the dbnsfp_scores table and indexes in the target database.

    Safe to call multiple times (uses IF NOT EXISTS).

    Args:
        engine: SQLAlchemy engine for the dbnsfp.db file.
    """
    with engine.begin() as conn:
        conn.execute(sa.text(CREATE_TABLE_SQL))
        for idx_sql in CREATE_INDEXES_SQL:
            conn.execute(sa.text(idx_sql))


def load_dbnsfp_from_tsv(
    tsv_path: Path,
    engine: sa.Engine,
    *,
    clear_existing: bool = True,
    progress_callback: Callable[[int], None] | None = None,
) -> LoadStats:
    """Parse a dbNSFP TSV and load scores into the dbnsfp_scores table.

    Uses streaming parse + batch insert to keep memory usage low.

    Args:
        tsv_path: Path to the dbNSFP TSV (.tsv or .tsv.gz).
        engine: SQLAlchemy engine for dbnsfp.db.
        clear_existing: Whether to DELETE all existing rows first.
        progress_callback: Called with parsed line count at intervals.

    Returns:
        LoadStats with counts and metadata.
    """
    create_dbnsfp_tables(engine)

    if clear_existing:
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM dbnsfp_scores"))

    batch: list[dict] = []
    final_stats = LoadStats()

    for row, final_stats in iter_dbnsfp_tsv(tsv_path, progress_callback=progress_callback):
        batch.append(row)

        if len(batch) >= BATCH_SIZE:
            _insert_batch(engine, batch)
            batch = []

    # Flush remaining
    if batch:
        _insert_batch(engine, batch)

    # WAL checkpoint
    _wal_checkpoint(engine)

    logger.info(
        "dbnsfp_tsv_loaded",
        variants=final_stats.variants_loaded,
        skipped_no_rsid=final_stats.skipped_no_rsid,
        skipped_invalid_chrom=final_stats.skipped_invalid_chrom,
        skipped_no_scores=final_stats.skipped_no_scores,
        skipped_malformed=final_stats.skipped_malformed,
    )

    return final_stats


def load_dbnsfp_from_csv(
    csv_path: Path,
    engine: sa.Engine,
    *,
    clear_existing: bool = True,
) -> LoadStats:
    """Load dbNSFP data from a CSV seed file into the dbnsfp_scores table.

    Useful for testing and for loading pre-processed data.  The CSV is expected
    to have columns matching the dbnsfp_scores table exactly:
    rsid, chrom, pos, ref, alt, cadd_phred, ..., primateai.

    Args:
        csv_path: Path to the CSV file with dbNSFP data.
        engine: SQLAlchemy engine for dbnsfp.db.
        clear_existing: Whether to DELETE all existing rows first.

    Returns:
        LoadStats with counts.
    """
    create_dbnsfp_tables(engine)

    if clear_existing:
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM dbnsfp_scores"))

    stats = LoadStats()
    batch: list[dict] = []

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            stats.total_lines += 1
            for required in ("chrom", "pos", "ref", "alt"):
                if required not in row:
                    msg = f"Missing required column '{required}' in CSV"
                    raise ValueError(msg)
            batch.append(
                {
                    "rsid": row.get("rsid") or None,
                    "chrom": row["chrom"],
                    "pos": int(row["pos"]),
                    "ref": row["ref"],
                    "alt": row["alt"],
                    "cadd_phred": _parse_float(row.get("cadd_phred")),
                    "sift_score": _parse_float(row.get("sift_score")),
                    "sift_pred": row.get("sift_pred") or None,
                    "polyphen2_hsvar_score": _parse_float(row.get("polyphen2_hsvar_score")),
                    "polyphen2_hsvar_pred": row.get("polyphen2_hsvar_pred") or None,
                    "revel": _parse_float(row.get("revel")),
                    "mutpred2": _parse_float(row.get("mutpred2")),
                    "vest4": _parse_float(row.get("vest4")),
                    "metasvm": _parse_float(row.get("metasvm")),
                    "metalr": _parse_float(row.get("metalr")),
                    "gerp_rs": _parse_float(row.get("gerp_rs")),
                    "phylop": _parse_float(row.get("phylop")),
                    "mpc": _parse_float(row.get("mpc")),
                    "primateai": _parse_float(row.get("primateai")),
                }
            )
            stats.variants_loaded += 1

            if len(batch) >= BATCH_SIZE:
                _insert_batch(engine, batch)
                batch = []

    if batch:
        _insert_batch(engine, batch)

    _wal_checkpoint(engine)

    logger.info("dbnsfp_csv_loaded", variants=stats.variants_loaded)
    return stats


def _insert_batch(engine: sa.Engine, batch: list[dict]) -> None:
    """Insert a batch of rows into dbnsfp_scores using INSERT OR REPLACE."""
    if not batch:
        return
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT OR REPLACE INTO dbnsfp_scores "
                "(rsid, chrom, pos, ref, alt, cadd_phred, "
                "sift_score, sift_pred, polyphen2_hsvar_score, "
                "polyphen2_hsvar_pred, revel, mutpred2, vest4, "
                "metasvm, metalr, gerp_rs, phylop, mpc, primateai) "
                "VALUES (:rsid, :chrom, :pos, :ref, :alt, :cadd_phred, "
                ":sift_score, :sift_pred, :polyphen2_hsvar_score, "
                ":polyphen2_hsvar_pred, :revel, :mutpred2, :vest4, "
                ":metasvm, :metalr, :gerp_rs, :phylop, :mpc, :primateai)"
            ),
            batch,
        )


# ── Download ─────────────────────────────────────────────────────────────


def download_dbnsfp(
    dest_dir: Path,
    *,
    url: str = DBNSFP_TSV_URL,
    progress_callback: Callable[[int, int | None], None] | None = None,
    timeout: float = 3600.0,
) -> Path:
    """Download the dbNSFP database file.

    Writes to a temporary file and renames on success to avoid
    leaving partial files on failure.

    Args:
        dest_dir: Directory to save the downloaded file.
        url: Override URL (useful for testing).
        progress_callback: Called with (bytes_downloaded, total_bytes).
        timeout: HTTP request timeout in seconds.

    Returns:
        Path to the downloaded file.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / "dbnsfp_archive.zip"

    logger.info("dbnsfp_download_start", url=url)

    # Resilient streaming download: retries and resumes via HTTP Range so a
    # dropped connection mid-transfer (common on this ~48 GB file) does not
    # discard gigabytes of progress. See backend/annotation/http_download.py.
    stream_download_with_resume(
        url,
        dest_path,
        progress_callback=progress_callback,
        timeout=timeout,
    )

    logger.info("dbnsfp_download_complete", path=str(dest_path))
    return dest_path


def download_and_load_dbnsfp(
    dbnsfp_engine: sa.Engine,
    dest_dir: Path,
    *,
    url: str = DBNSFP_TSV_URL,
    download_progress: Callable[[int, int | None], None] | None = None,
    parse_progress: Callable[[int], None] | None = None,
    timeout: float = 3600.0,
    reference_engine: sa.Engine | None = None,
) -> LoadStats:
    """Full pipeline: download dbNSFP, parse, and load into dbnsfp.db.

    Args:
        dbnsfp_engine: SQLAlchemy engine for dbnsfp.db.
        dest_dir: Directory for downloaded files.
        url: dbNSFP download URL (override for testing).
        download_progress: Callback for download progress.
        parse_progress: Callback for parse progress.
        timeout: HTTP timeout in seconds.
        reference_engine: Optional engine for reference.db to record version.

    Returns:
        LoadStats with counts and metadata.
    """
    # Download
    downloaded_path = download_dbnsfp(
        dest_dir,
        url=url,
        progress_callback=download_progress,
        timeout=timeout,
    )

    # Compute checksum
    sha256 = _compute_sha256(downloaded_path)

    # Parse and load
    stats = load_dbnsfp_from_tsv(
        downloaded_path,
        dbnsfp_engine,
        progress_callback=parse_progress,
    )
    stats.sha256 = sha256

    # Record version in reference.db
    if reference_engine is not None:
        record_dbnsfp_version(
            reference_engine,
            version="5.3.1a",
            file_path=str(downloaded_path),
            file_size_bytes=downloaded_path.stat().st_size,
            checksum=sha256,
        )

    return stats


# ── Version tracking ─────────────────────────────────────────────────────


def _parse_version_tuple(version: str) -> tuple[int, ...]:
    """Parse a dbNSFP release tag into a tuple of integer components.

    dbNSFP tags follow ``MAJOR.MINOR.PATCH[suffix]`` (e.g. ``5.3.1a``).  We
    strip any leading non-numeric prefix, split on ``.``, and reduce each
    component to its leading run of digits (so ``1a`` → ``1``), discarding
    components with no digits.  This yields a tuple suitable for numeric
    ordering, fixing the string-compare misorder of e.g. ``5.10.0`` vs
    ``5.9.0``.
    """
    stripped = version.lstrip("vVrR")
    components: list[int] = []
    for part in stripped.split("."):
        digits = ""
        for ch in part:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits:
            components.append(int(digits))
    return tuple(components)


def _version_at_least(current: str, target: str) -> bool:
    """Return True if ``current`` is the same as or newer than ``target``.

    Compares numeric version components (zero-padding the shorter tuple) so
    that e.g. ``5.10.0`` correctly sorts after ``5.9.0``.
    """
    cur = _parse_version_tuple(current)
    tgt = _parse_version_tuple(target)
    length = max(len(cur), len(tgt))
    cur += (0,) * (length - len(cur))
    tgt += (0,) * (length - len(tgt))
    return cur >= tgt


def record_dbnsfp_version(
    engine: sa.Engine,
    *,
    version: str,
    file_path: str | None = None,
    file_size_bytes: int | None = None,
    checksum: str | None = None,
) -> None:
    """Insert or update the dbNSFP version in the database_versions table."""
    from backend.db.database_registry import _record_db_version

    _record_db_version(
        engine,
        db_name="dbnsfp",
        version=version,
        file_size_bytes=file_size_bytes,
        sha256=checksum,
        file_path=file_path,
    )


def check_dbnsfp_update(
    reference_engine: sa.Engine,
    settings: object | None = None,
    *,
    timeout: float = 30.0,
):
    """Check whether the dbNSFP release pinned in the manifest is newer than installed.

    Uses ``pipeline_pins["dbnsfp"]`` from ``bundles/manifest.json`` as the
    authoritative source for the latest URL + release tag, then performs an
    HTTP HEAD on the pinned URL to confirm reachability and obtain a
    download-size estimate for the bandwidth-window check. Returns ``None``
    when the manifest pin is missing/unreachable, the HEAD call fails, or
    the recorded version is the same as or newer than the manifest pin
    (numeric component compare on the release tag — dbNSFP tags follow
    ``MAJOR.MINOR.PATCH[suffix]``).

    Args:
        reference_engine: Reference DB engine for ``database_versions`` lookup.
        settings: Accepted for dispatch-signature parity with other
            ``check_*_update`` functions; unused.
        timeout: HTTP timeout in seconds for both the manifest fetch and HEAD.

    Returns:
        ``VersionInfo`` when the manifest pin is newer than the installed
        version, otherwise ``None``.
    """
    del settings  # unused; kept for dispatch-signature parity
    from backend.db.manifest import get_pipeline_pin
    from backend.db.update_manager import VersionInfo, get_current_version

    pin = get_pipeline_pin("dbnsfp", timeout=timeout)
    if pin is None or not pin.last_known_version:
        return None

    current = get_current_version(reference_engine, "dbnsfp")
    if current is not None and _version_at_least(current, pin.last_known_version):
        return None

    download_size = 0
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(timeout, connect=10.0),
        ) as client:
            resp = client.head(pin.url)
            resp.raise_for_status()
            content_length = resp.headers.get("Content-Length")
            if content_length:
                download_size = int(content_length)
    except Exception as exc:
        logger.warning("dbnsfp_update_check_failed", error=str(exc))
        return None

    return VersionInfo(
        db_name="dbnsfp",
        latest_version=pin.last_known_version,
        download_url=pin.url,
        download_size_bytes=download_size,
    )


# ── Annotation lookup ────────────────────────────────────────────────────


def lookup_dbnsfp_by_rsids(
    rsids: list[str],
    dbnsfp_engine: sa.Engine,
) -> dict[str, DbNSFPAnnotation]:
    """Look up dbNSFP scores for a batch of rsids.

    Processes in batches of 500 to stay under SQLite's 999-variable limit.

    Args:
        rsids: List of rsid strings (e.g. ["rs429358", "rs7412"]).
        dbnsfp_engine: SQLAlchemy engine for dbnsfp.db.

    Returns:
        Dict mapping rsid → DbNSFPAnnotation for matched variants.
    """
    if not rsids:
        return {}

    results: dict[str, DbNSFPAnnotation] = {}

    with dbnsfp_engine.connect() as conn:
        for i in range(0, len(rsids), LOOKUP_BATCH_SIZE):
            batch = rsids[i : i + LOOKUP_BATCH_SIZE]
            placeholders = ", ".join(f":r{j}" for j in range(len(batch)))
            params = {f"r{j}": rsid for j, rsid in enumerate(batch)}

            stmt = sa.text(
                "SELECT rsid, chrom, pos, ref, alt, cadd_phred, sift_score, "  # noqa: S608
                "sift_pred, polyphen2_hsvar_score, polyphen2_hsvar_pred, "
                "revel, mutpred2, vest4, metasvm, metalr, gerp_rs, phylop, "
                f"mpc, primateai FROM dbnsfp_scores WHERE rsid IN ({placeholders})"
            )
            rows = conn.execute(stmt, params).fetchall()

            for row in rows:
                results[row.rsid] = DbNSFPAnnotation(
                    rsid=row.rsid,
                    chrom=row.chrom,
                    pos=row.pos,
                    ref=row.ref,
                    alt=row.alt,
                    cadd_phred=row.cadd_phred,
                    sift_score=row.sift_score,
                    sift_pred=row.sift_pred,
                    polyphen2_hsvar_score=row.polyphen2_hsvar_score,
                    polyphen2_hsvar_pred=row.polyphen2_hsvar_pred,
                    revel=row.revel,
                    mutpred2=row.mutpred2,
                    vest4=row.vest4,
                    metasvm=row.metasvm,
                    metalr=row.metalr,
                    gerp_rs=row.gerp_rs,
                    phylop=row.phylop,
                    mpc=row.mpc,
                    primateai=row.primateai,
                )

    return results


def lookup_dbnsfp_by_positions(
    positions: list[tuple[str, int, str, str]],
    dbnsfp_engine: sa.Engine,
) -> dict[tuple[str, int, str, str], DbNSFPAnnotation]:
    """Look up dbNSFP annotations by (chrom, pos, ref, alt).

    Fallback strategy when rsid matching fails. Uses the composite
    primary key for efficient lookups.

    Args:
        positions: List of (chrom, pos, ref, alt) tuples.
        dbnsfp_engine: SQLAlchemy engine for dbnsfp.db.

    Returns:
        Dict mapping (chrom, pos, ref, alt) → DbNSFPAnnotation.
    """
    if not positions:
        return {}

    results: dict[tuple[str, int, str, str], DbNSFPAnnotation] = {}

    with dbnsfp_engine.connect() as conn:
        for i in range(0, len(positions), POSITION_LOOKUP_BATCH_SIZE):
            batch = positions[i : i + POSITION_LOOKUP_BATCH_SIZE]

            conditions = []
            params: dict[str, str | int] = {}
            for j, (chrom, pos, ref, alt) in enumerate(batch):
                conditions.append(
                    f"(chrom = :c{j} AND pos = :p{j} AND ref = :r{j} AND alt = :a{j})"
                )
                params[f"c{j}"] = chrom
                params[f"p{j}"] = pos
                params[f"r{j}"] = ref
                params[f"a{j}"] = alt

            where_clause = " OR ".join(conditions)
            stmt = sa.text(
                "SELECT rsid, chrom, pos, ref, alt, cadd_phred, sift_score, "  # noqa: S608
                "sift_pred, polyphen2_hsvar_score, polyphen2_hsvar_pred, "
                "revel, mutpred2, vest4, metasvm, metalr, gerp_rs, phylop, "
                f"mpc, primateai FROM dbnsfp_scores WHERE {where_clause}"
            )
            rows = conn.execute(stmt, params).fetchall()

            for row in rows:
                key = (row.chrom, row.pos, row.ref, row.alt)
                results[key] = DbNSFPAnnotation(
                    rsid=row.rsid,
                    chrom=row.chrom,
                    pos=row.pos,
                    ref=row.ref,
                    alt=row.alt,
                    cadd_phred=row.cadd_phred,
                    sift_score=row.sift_score,
                    sift_pred=row.sift_pred,
                    polyphen2_hsvar_score=row.polyphen2_hsvar_score,
                    polyphen2_hsvar_pred=row.polyphen2_hsvar_pred,
                    revel=row.revel,
                    mutpred2=row.mutpred2,
                    vest4=row.vest4,
                    metasvm=row.metasvm,
                    metalr=row.metalr,
                    gerp_rs=row.gerp_rs,
                    phylop=row.phylop,
                    mpc=row.mpc,
                    primateai=row.primateai,
                )

    return results
