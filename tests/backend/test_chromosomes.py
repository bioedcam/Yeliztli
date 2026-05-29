"""Chromosome normalizer contract tests (ADNA-08c; Plan §8.4).

Locks the per-vendor remap tables for the parser layer so step 30's
AncestryDNA parser (and the future 23andMe refactor) can rely on a stable
``normalize_for`` surface:

- **23andMe map** — ``23→X``, ``24→Y``, ``25→MT``, ``26→MT``.
- **AncestryDNA map** — ``23→X``, ``24→Y``, ``25→X`` (PAR collapses to X),
  ``26→MT``.
- Already-canonical values (``"1"``-``"22"``, ``"X"``, ``"Y"``, ``"MT"``) pass
  through unchanged for both vendors.
- Whitespace and case variants are normalised; invalid inputs (``"27"``,
  empty, special chars) raise ``MalformedDataError`` with the offending raw
  value quoted in the message.
"""

from __future__ import annotations

import pytest

from backend.ingestion import chromosomes
from backend.ingestion.base import MalformedDataError, SourceVendor

# --------------------------------------------------------------------------- #
# Per-vendor remap tables                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("23", "X"),
        ("24", "Y"),
        ("25", "MT"),
        ("26", "MT"),
    ],
)
def test_23andme_remaps(raw: str, expected: str) -> None:
    """23andMe: 25 and 26 both collapse onto MT per Plan §8.4."""
    assert chromosomes.normalize_for(SourceVendor.TWENTYTHREEANDME, raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("23", "X"),
        ("24", "Y"),
        ("25", "X"),  # PAR collapses to X — key divergence from 23andMe
        ("26", "MT"),
    ],
)
def test_ancestrydna_remaps(raw: str, expected: str) -> None:
    """AncestryDNA: 25 = PAR collapses to X (not MT); 26 = MT per Plan §8.4."""
    assert chromosomes.normalize_for(SourceVendor.ANCESTRYDNA, raw) == expected


def test_par_divergence_between_vendors() -> None:
    """The ``25`` value is the load-bearing per-vendor divergence (Plan §8.4)."""
    assert chromosomes.normalize_for(SourceVendor.TWENTYTHREEANDME, "25") == "MT"
    assert chromosomes.normalize_for(SourceVendor.ANCESTRYDNA, "25") == "X"


# --------------------------------------------------------------------------- #
# Canonical pass-through (both vendors)                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("vendor", list(SourceVendor))
@pytest.mark.parametrize(
    "canonical",
    [str(n) for n in range(1, 23)] + ["X", "Y", "MT"],
)
def test_canonical_values_pass_through_unchanged(vendor: SourceVendor, canonical: str) -> None:
    assert chromosomes.normalize_for(vendor, canonical) == canonical


# --------------------------------------------------------------------------- #
# Whitespace + case normalisation                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("vendor", list(SourceVendor))
@pytest.mark.parametrize(
    "raw, expected",
    [
        (" 23 ", "X"),  # surrounding whitespace
        ("\t24\t", "Y"),  # tab whitespace
        ("x", "X"),  # lowercase letter chromosome
        ("y", "Y"),  # lowercase letter chromosome
        ("mt", "MT"),  # lowercase MT
        ("Mt", "MT"),  # mixed-case MT
        ("  X", "X"),  # leading whitespace
        ("Y  ", "Y"),  # trailing whitespace
    ],
)
def test_strip_and_uppercase(vendor: SourceVendor, raw: str, expected: str) -> None:
    assert chromosomes.normalize_for(vendor, raw) == expected


# --------------------------------------------------------------------------- #
# Invalid inputs raise MalformedDataError                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("vendor", list(SourceVendor))
@pytest.mark.parametrize(
    "raw",
    [
        "27",  # out of range
        "0",  # below valid range
        "-1",  # negative
        "100",  # multi-digit out of range
        "",  # empty string
        "   ",  # whitespace only
        "Z",  # unknown letter
        "chr1",  # the "chr" prefix is not accepted
        "chrX",
        "1.0",  # decimal
        "1a",  # alphanumeric mix
        "*",  # special char
        "?",  # special char
        "\x00",  # null byte
    ],
)
def test_invalid_inputs_raise(vendor: SourceVendor, raw: str) -> None:
    with pytest.raises(MalformedDataError) as exc_info:
        chromosomes.normalize_for(vendor, raw)
    # The offending raw value is quoted in the message so log forensics can
    # recover the original string (Plan §8.4 follows the 23andMe parser style).
    assert repr(raw) in str(exc_info.value)


# --------------------------------------------------------------------------- #
# Public surface                                                              #
# --------------------------------------------------------------------------- #


def test_module_all_exports() -> None:
    assert set(chromosomes.__all__) == {"normalize_for"}


def test_vendor_coverage_is_total() -> None:
    """Every SourceVendor member must have a remap table — otherwise the
    KeyError path inside ``normalize_for`` would surface as an opaque crash
    instead of a typed parser error.
    """
    for vendor in SourceVendor:
        assert chromosomes.normalize_for(vendor, "1") == "1"
