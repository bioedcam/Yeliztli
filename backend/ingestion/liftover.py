"""GRCh38 liftover integration (P4-19).

Converts GRCh37 (hg19) genomic coordinates to GRCh38 (hg38) using pyliftover.

The hg19→hg38 chain file is vendored in-repo at ``backend/data/chains/`` and
loaded directly, so liftover never touches the network. pyliftover's default
behaviour (``LiftOver("hg19", "hg38")``) would download the chain from UCSC on
first use, which made CI flaky when that fetch failed; loading the bundled file
keeps tests offline/deterministic and avoids a first-run download in production.
A network fetch remains only as a fallback if the vendored file is ever missing.

Lifted coordinates are stored as parallel columns (chrom_grch38, pos_grch38) in
the annotated_variants table — the primary (chrom, pos) columns remain GRCh37.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from pyliftover import LiftOver

logger = logging.getLogger(__name__)

# Vendored UCSC hg19→hg38 chain (~222 KB). See backend/data/chains/README.md
# for provenance and refresh instructions.
_CHAIN_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "chains" / "hg19ToHg38.over.chain.gz"
)

# Thread-safe singleton for the LiftOver instance (chain file is ~222 KB,
# loaded once and reused across all liftover calls).
_lock = threading.Lock()
_liftover: LiftOver | None = None


def _get_liftover() -> LiftOver:
    """Return (or lazily initialise) the hg19→hg38 LiftOver instance.

    Loads the vendored chain file directly (no network). Falls back to
    pyliftover's UCSC download only if the bundled file is missing, logging a
    warning since that reintroduces the network dependency the vendored file
    exists to remove.
    """
    global _liftover
    with _lock:
        if _liftover is None:
            if _CHAIN_PATH.exists():
                logger.info(
                    "liftover_init",
                    extra={"from": "hg19", "to": "hg38", "source": "vendored"},
                )
                _liftover = LiftOver(str(_CHAIN_PATH))
            else:
                logger.warning(
                    "liftover_chain_missing_fallback_to_web",
                    extra={"expected_path": str(_CHAIN_PATH)},
                )
                _liftover = LiftOver("hg19", "hg38")
    return _liftover


def convert_coordinate(
    chrom: str,
    pos: int,
) -> tuple[str, int] | None:
    """Convert a single GRCh37 coordinate to GRCh38.

    Args:
        chrom: Chromosome name (e.g. "1", "X", "MT"). The ``chr`` prefix is
            added automatically if missing (pyliftover requires UCSC-style names).
        pos: 0-based or 1-based GRCh37 position. pyliftover uses 0-based
            coordinates internally; 23andMe positions are 1-based, so we
            convert to 0-based before the call and back to 1-based on return.

    Returns:
        Tuple of ``(chrom_grch38, pos_grch38)`` with 1-based position and
        chromosome name without ``chr`` prefix (matching our internal convention),
        or ``None`` if the coordinate could not be lifted over (e.g. the region
        was deleted/rearranged in GRCh38, or a mitochondrial input — see below).

    Mitochondrial inputs (``MT`` / ``chrM``) always return ``None`` (F34): UCSC
    hg19 ``chrM`` is the old Yoruba reference sequence, **not** rCRS — the build
    the chip data uses — so the hg19→hg38 chain lifts MT positions to wrong
    GRCh38 coordinates (e.g. 263→deleted, 750→748). Refusing to lift is correct
    here; emitting a bogus coordinate would silently corrupt downstream joins.
    """
    clean = chrom.removeprefix("chr")

    # MT short-circuit (F34): the vendored hg19→hg38 chain's chrM is Yoruba, not
    # rCRS, so any lifted mitochondrial coordinate is wrong. Decline to lift.
    if clean in ("MT", "M"):
        return None

    lo = _get_liftover()

    # pyliftover requires UCSC-style "chr"-prefixed names.
    ucsc_chrom = f"chr{clean}"

    # pyliftover uses 0-based coordinates; our positions are 1-based
    results = lo.convert_coordinate(ucsc_chrom, pos - 1)

    if not results:
        return None

    # Take the best (first) result
    new_chrom, new_pos_0based, _strand, _score = results[0]

    # Strip "chr" prefix for internal consistency. MT is short-circuited above,
    # and no autosomal/sex input lifts to chrM, so no chrM→MT remap is needed.
    out_chrom = new_chrom.removeprefix("chr")

    # Convert back to 1-based
    return (out_chrom, new_pos_0based + 1)


def batch_convert(
    variants: list[tuple[str, str, int]],
) -> dict[str, tuple[str, int] | None]:
    """Batch convert GRCh37 coordinates to GRCh38.

    Args:
        variants: List of ``(rsid, chrom, pos)`` tuples.

    Returns:
        Dict mapping rsid → ``(chrom_grch38, pos_grch38)`` or ``None`` if
        the coordinate could not be lifted.
    """
    results: dict[str, tuple[str, int] | None] = {}
    converted = 0
    failed = 0

    for rsid, chrom, pos in variants:
        result = convert_coordinate(chrom, pos)
        results[rsid] = result
        if result is not None:
            converted += 1
        else:
            failed += 1

    logger.info(
        "liftover_batch_complete",
        extra={
            "total": len(variants),
            "converted": converted,
            "failed": failed,
        },
    )
    return results


def reset_liftover() -> None:
    """Reset the cached LiftOver instance (for testing)."""
    global _liftover
    with _lock:
        _liftover = None
