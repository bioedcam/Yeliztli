"""Vendor dispatcher for raw-data files.

Single public entry point (`parse`) for the ingest layer. Detects which vendor
the file belongs to (23andMe or AncestryDNA) via head-line inspection and
routes to the matching vendor parser, then returns the unified
``base.ParseResult``.

Detection precedence per Plan §8.3:

- **23andMe first.** Either the canonical column header
  ``# rsid\\tchromosome\\tposition\\tgenotype`` OR the substring ``23andme``
  in the first 50 lines wins.
- **AncestryDNA second.** Either the ``#ancestrydna`` substring in the first
  50 lines OR an uncommented 5-column header
  ``rsid\\tchromosome\\tposition\\tallele1\\tallele2`` wins.
- A file matching both signatures (e.g. a 23andMe comment that mentions
  AncestryDNA) routes to 23andMe by precedence.
- Otherwise raises ``UnsupportedFormatError`` with a VCF / CSV / binary /
  generic guidance message.

The unified ``ParseResult`` returned by :func:`parse` always carries the new
``base.ParseResult`` shape with a string ``version`` (e.g. ``"v5"``). Step 27
adapts the legacy 23andMe parser's enum-typed result onto the unified shape;
step 29 retires the adapter when ``parser_23andme`` natively returns
``base.ParseResult``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TextIO

from backend.ingestion import parser_23andme
from backend.ingestion.base import (
    MalformedDataError,
    ParsedVariant,
    ParserError,
    ParseResult,
    SourceVendor,
    UnrecognizedVersionError,
    UnsupportedFormatError,
)

_DETECT_LINE_LIMIT = 50

_23ANDME_HEADER = "# rsid\tchromosome\tposition\tgenotype"
_23ANDME_SUBSTRING = "23andme"

_ANCESTRYDNA_SIGNATURE = "#ancestrydna"
_ANCESTRYDNA_HEADER_COLUMNS = ("rsid", "chromosome", "position", "allele1", "allele2")

_VERSION_TO_BUILD = {"v3": "GRCh36", "v4": "GRCh37", "v5": "GRCh37"}

_ERR_VCF = (
    "This looks like a VCF file. GenomeInsight v1 expects 23andMe or "
    "AncestryDNA raw data. VCF support is planned for a future release."
)
_ERR_CSV = (
    "This file appears to be comma-separated. 23andMe and AncestryDNA raw "
    "data files use tab-separated format."
)
_ERR_BINARY = "This file contains binary data and is not a valid text file."
_ERR_UNKNOWN = (
    "Unrecognized file format. GenomeInsight expects 23andMe or AncestryDNA "
    "raw data (tab-separated, .txt). Please file a GitHub issue at "
    "https://github.com/bioedcam/GenomeInsight/issues if you believe this is "
    "a bug."
)


def _check_binary(head: bytes) -> bool:
    return b"\x00" in head


def _read_head_lines(
    file_or_path: str | Path | TextIO,
    limit: int = _DETECT_LINE_LIMIT,
) -> list[str]:
    if isinstance(file_or_path, (str, Path)):
        path = Path(file_or_path)
        raw = path.read_bytes()[:512]
        if _check_binary(raw):
            raise UnsupportedFormatError(_ERR_BINARY)
        with open(path, encoding="utf-8", errors="replace") as fh:
            return [fh.readline() for _ in range(limit)]

    stream: TextIO = file_or_path
    seekable = hasattr(stream, "seekable") and stream.seekable()
    if not seekable:
        raise ValueError(
            "TextIO streams must be seekable. Wrap non-seekable streams in "
            "io.StringIO(stream.read()) before calling detect_vendor / parse."
        )
    pos = stream.tell()
    lines = [stream.readline() for _ in range(limit)]
    stream.seek(pos)
    return lines


def _looks_like_23andme(lines: list[str]) -> bool:
    joined_lower = "".join(lines).lower()
    if _23ANDME_SUBSTRING in joined_lower:
        return True
    target = _23ANDME_HEADER.lower()
    for raw in lines:
        if raw.rstrip("\n\r").strip().lower() == target:
            return True
    return False


def _looks_like_ancestrydna(lines: list[str]) -> bool:
    joined_lower = "".join(lines).lower()
    if _ANCESTRYDNA_SIGNATURE in joined_lower:
        return True
    for raw in lines:
        line = raw.rstrip("\n\r")
        if not line or line.startswith("#"):
            continue
        columns = tuple(c.strip().lower() for c in line.split("\t"))
        return columns == _ANCESTRYDNA_HEADER_COLUMNS
    return False


def _reject_with_guidance(lines: list[str]) -> None:
    joined_lower = "".join(lines).lower()

    if "##fileformat=vcf" in joined_lower or "#chrom\tpos\tid" in joined_lower:
        raise UnsupportedFormatError(_ERR_VCF)

    data_lines = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    if data_lines:
        comma_count = sum(1 for ln in data_lines if "," in ln)
        if comma_count > len(data_lines) * 0.5:
            raise UnsupportedFormatError(_ERR_CSV)

    raise UnsupportedFormatError(_ERR_UNKNOWN)


def detect_vendor(file_or_path: str | Path | TextIO) -> SourceVendor:
    """Inspect the file head and return the matching vendor.

    Raises
    ------
    UnsupportedFormatError
        If the file matches neither vendor signature, with a guidance message
        tailored to detected non-vendor formats (VCF / CSV / binary).
    """
    lines = _read_head_lines(file_or_path)
    if _looks_like_23andme(lines):
        return SourceVendor.TWENTYTHREEANDME
    if _looks_like_ancestrydna(lines):
        return SourceVendor.ANCESTRYDNA
    _reject_with_guidance(lines)
    raise UnsupportedFormatError(_ERR_UNKNOWN)  # pragma: no cover — type guard


def _translate_legacy_23andme_error(exc: parser_23andme.ParserError) -> ParserError:
    if isinstance(exc, parser_23andme.UnsupportedFormatError):
        return UnsupportedFormatError(str(exc))
    if isinstance(exc, parser_23andme.MalformedDataError):
        return MalformedDataError(str(exc))
    if isinstance(exc, parser_23andme.UnrecognizedVersionError):
        return UnrecognizedVersionError(str(exc))
    return ParserError(str(exc))


def _adapt_23andme(legacy_result: parser_23andme.ParseResult) -> ParseResult:
    version_str = legacy_result.version.value
    build = _VERSION_TO_BUILD.get(version_str, "GRCh37")
    return ParseResult(
        vendor=SourceVendor.TWENTYTHREEANDME,
        version=version_str,
        build=build,
        variants=[
            ParsedVariant(rsid=v.rsid, chrom=v.chrom, pos=v.pos, genotype=v.genotype)
            for v in legacy_result.variants
        ],
        nocall_count=legacy_result.nocall_count,
        total_lines=legacy_result.total_lines,
        skipped_lines=legacy_result.skipped_lines,
    )


def parse(file_or_path: str | Path | TextIO) -> ParseResult:
    """Detect vendor, route to the matching parser, and normalize the result.

    Returns
    -------
    ParseResult
        The unified ``base.ParseResult`` shape. For 23andMe inputs, the
        legacy ``parser_23andme.ParseResult`` is adapted onto this shape
        (step 29 retires the adapter).

    Raises
    ------
    UnsupportedFormatError, MalformedDataError, UnrecognizedVersionError
        Re-raised from the underlying vendor parser as ``base`` module
        subclasses so callers can catch a single hierarchy.
    """
    vendor = detect_vendor(file_or_path)
    if vendor is SourceVendor.TWENTYTHREEANDME:
        try:
            legacy = parser_23andme.parse_23andme(file_or_path)
        except parser_23andme.ParserError as exc:
            raise _translate_legacy_23andme_error(exc) from exc
        return _adapt_23andme(legacy)
    if vendor is SourceVendor.ANCESTRYDNA:
        try:
            from backend.ingestion import (
                parser_ancestrydna,  # noqa: PLC0415 — lazy import; module lands in step 30
            )
        except ImportError as exc:  # pragma: no cover — exercised once step 30 lands
            raise UnsupportedFormatError(
                "AncestryDNA parser is not yet available (lands in step 30)."
            ) from exc
        return parser_ancestrydna.parse_ancestrydna(file_or_path)
    raise UnsupportedFormatError("unreachable")  # pragma: no cover


__all__ = ["detect_vendor", "parse"]
