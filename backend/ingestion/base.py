"""Shared types for the ingestion parser layer.

Defines the vendor enum, parsed-variant + parse-result dataclasses, and the
exception hierarchy consumed by every vendor parser plus the dispatcher.

Per Plan §8.2, vendor-specific parsers (`parser_23andme`, `parser_ancestrydna`)
and the dispatcher (`dispatcher`) import the public symbols defined here. The
23andMe parser refactor onto these shared types lands in step 29 — step 26
ships only the shared module itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SourceVendor(Enum):
    """Vendor of the source raw-data file."""

    TWENTYTHREEANDME = "23andme"
    ANCESTRYDNA = "ancestrydna"


@dataclass(frozen=True, slots=True)
class ParsedVariant:
    """A single variant row extracted from a vendor raw-data file.

    ``genotype`` is the canonical 2-character form: uppercased + sorted-pair
    for diploid calls (``"AG"``, ``"GT"``), ``"--"`` for no-calls, and the
    canonical sorted-pair form for indels (``"DD"``, ``"II"``, ``"DI"``).
    """

    rsid: str
    chrom: str
    pos: int
    genotype: str


@dataclass
class ParseResult:
    """Aggregate result of parsing a complete vendor raw-data file.

    ``version`` is freeform per vendor (e.g. ``"v5"`` for 23andMe v5, ``"v2.0"``
    for AncestryDNA v2.0); callers compose ``f"{vendor.value}_{version}"`` into
    ``samples.file_format`` (Plan §8.7).
    """

    vendor: SourceVendor
    version: str
    build: str
    variants: list[ParsedVariant] = field(default_factory=list)
    nocall_count: int = 0
    total_lines: int = 0
    skipped_lines: int = 0


class ParserError(Exception):
    """Base class for every parser-layer error."""


class UnsupportedFormatError(ParserError):
    """File does not match any supported vendor format."""


class MalformedDataError(ParserError):
    """File matches a supported vendor but contains invalid data lines."""


class UnrecognizedVersionError(ParserError):
    """File matches a supported vendor but the version cannot be determined."""


__all__ = [
    "SourceVendor",
    "ParsedVariant",
    "ParseResult",
    "ParserError",
    "UnsupportedFormatError",
    "MalformedDataError",
    "UnrecognizedVersionError",
]
