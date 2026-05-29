"""Per-vendor chromosome normalizers for the ingestion parser layer.

Implements the shared ``normalize_for(vendor, chrom)`` entry point used by every
vendor parser (Plan §8.4). Each vendor has its own raw-encoding map:

- **23andMe** — ``23→X``, ``24→Y``, ``25→MT``, ``26→MT``. Both 25 and 26 collapse
  onto MT (the legacy 23andMe convention).
- **AncestryDNA** — ``23→X``, ``24→Y``, ``25→X`` (PAR collapses onto X), ``26→MT``.
  PAR-vs-XTR distinction is not surfaced here; if a future analysis needs it, an
  optional ``is_par`` flag would be threaded through ``ParsedVariant`` rather
  than introduced as a separate chromosome value.

Inputs are uppercased and stripped before lookup; already-canonical values
(``"1"`` … ``"22"``, ``"X"``, ``"Y"``, ``"MT"``) pass through unchanged.
Everything else raises ``MalformedDataError`` with the offending raw value
quoted for log-line forensics.
"""

from __future__ import annotations

from backend.ingestion.base import MalformedDataError, SourceVendor

_23ANDME_MAP: dict[str, str] = {
    "23": "X",
    "24": "Y",
    "25": "MT",
    "26": "MT",
}

_ANCESTRYDNA_MAP: dict[str, str] = {
    "23": "X",
    "24": "Y",
    "25": "X",  # PAR (pseudoautosomal region) collapses to X
    "26": "MT",
}

_VENDOR_MAPS: dict[SourceVendor, dict[str, str]] = {
    SourceVendor.TWENTYTHREEANDME: _23ANDME_MAP,
    SourceVendor.ANCESTRYDNA: _ANCESTRYDNA_MAP,
}

_VALID: frozenset[str] = frozenset([str(n) for n in range(1, 23)] + ["X", "Y", "MT"])


def normalize_for(vendor: SourceVendor, chrom: str) -> str:
    """Normalize ``chrom`` for ``vendor`` to one of ``1``-``22`` / ``X`` / ``Y`` / ``MT``.

    Parameters
    ----------
    vendor:
        Source vendor enum value. Selects the per-vendor remap table.
    chrom:
        Raw chromosome string from the vendor file (may be whitespace-padded
        or mixed-case).

    Returns
    -------
    str
        Canonical chromosome string.

    Raises
    ------
    MalformedDataError
        If ``chrom`` is empty, has unrecognised content, or sits outside the
        ``1``-``26`` / ``X`` / ``Y`` / ``MT`` set after normalisation.
    """
    upper = chrom.strip().upper()
    table = _VENDOR_MAPS[vendor]
    if upper in table:
        return table[upper]
    if upper in _VALID:
        return upper
    raise MalformedDataError(f"Invalid chromosome value: {chrom!r}")


__all__ = ["normalize_for"]
