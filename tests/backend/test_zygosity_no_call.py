"""Shared ``is_no_call`` helper contract tests (MRG-01a part 1; Plan §10.3).

Locks the recognition set for ``backend.analysis.zygosity.is_no_call`` so the
thirteen-module adoption in Step 61 and the ``_classify_genotype`` QC
reclassification in Step 62 ride on a stable surface. Future changes to the
recognition rule must update this matrix in lockstep (Plan §10.3).

Coverage:

- Every sentinel from Plan §10.3 returns True, with and without surrounding
  whitespace (locks the ``strip()`` contract).
- A spread of valid SNP / hemizygous genotypes returns False, including
  lowercase variants (uppercase-only is the canonical post-Phase-1 contract).
- Malformed / partial-no-call inputs that are NOT in the sentinel set return
  False (e.g. ``"0A"`` — Phase 1 parsers canonicalize element-wise no-calls to
  ``"--"`` before this helper is reached).
- Return type is strictly ``bool`` (asserted via ``is True`` / ``is False``).
"""

from __future__ import annotations

import pytest

from backend.analysis.zygosity import is_no_call

# --------------------------------------------------------------------------- #
# True cases — every Plan §10.3 sentinel, plus the strip() contract           #
# --------------------------------------------------------------------------- #

# Bare sentinels from Plan §10.3.
_NO_CALL_TRUE_BARE = [
    None,
    "",
    "--",
    "??",
    "-",
    "0",
    "00",
    "II",
    "DD",
    "DI",
    "ID",
]

# Whitespace-only inputs reduce to "" after strip().
_NO_CALL_TRUE_WHITESPACE = [
    " ",
    "  ",
    "\t",
    "\n",
    "\r",
    " \t\n",
    "\r\n",
]

# Padded sentinels — strip() must preserve no-call classification.
_NO_CALL_TRUE_PADDED = [
    " -- ",
    "  -- ",
    "\t--\n",
    " ?? ",
    "\n??\t",
    " - ",
    " 0 ",
    "  00  ",
    "\tII\t",
    " DD ",
    " DI ",
    " ID ",
]


@pytest.mark.parametrize(
    "genotype",
    _NO_CALL_TRUE_BARE + _NO_CALL_TRUE_WHITESPACE + _NO_CALL_TRUE_PADDED,
)
def test_is_no_call_returns_true_for_recognized_sentinels(genotype: str | None) -> None:
    """Every Plan §10.3 sentinel (bare, whitespace, padded) → True."""
    assert is_no_call(genotype) is True


# --------------------------------------------------------------------------- #
# False cases — valid calls, partial sentinels, lowercase, oddities           #
# --------------------------------------------------------------------------- #

# Standard diploid SNP genotypes (post-canonicalization: uppercase, sorted).
_NO_CALL_FALSE_DIPLOID = [
    "AA",
    "AC",
    "AG",
    "AT",
    "CC",
    "CG",
    "CT",
    "GG",
    "GT",
    "TT",
]

# Hemizygous calls on X/Y for XY individuals (single allele, NOT "-").
_NO_CALL_FALSE_HEMIZYGOUS = ["A", "C", "G", "T"]

# Lowercase — Phase 1 parser canonicalizes to uppercase; lowercase is NOT
# treated as no-call (would silently absorb malformed inputs otherwise).
_NO_CALL_FALSE_LOWERCASE = [
    "aa",
    "ag",
    "tt",
    "ii",
    "dd",
    "di",
    "id",
    "a",
    "g",
    "--".lower(),  # "--" is symmetric; included for completeness even though == "--"
]

# Partial / malformed: not in the sentinel set, must NOT be absorbed as no-call.
_NO_CALL_FALSE_PARTIAL_OR_MALFORMED = [
    "0A",  # AncestryDNA element-wise no-call; parser canonicalizes to "--" upstream
    "A0",
    "AB",
    "AAA",  # triploid-shaped — invalid for human autosomes
    "1",
    "2",
    "AD",  # not a recognized indel code
    "DA",
    "IA",
    "IG",
    " A G ",  # strip leaves "A G" — not in sentinel set
    "?-",  # partial sentinel mix; not in set
    "-?",
]


@pytest.mark.parametrize(
    "genotype",
    _NO_CALL_FALSE_DIPLOID
    + _NO_CALL_FALSE_HEMIZYGOUS
    + [g for g in _NO_CALL_FALSE_LOWERCASE if g != "--"]
    + _NO_CALL_FALSE_PARTIAL_OR_MALFORMED,
)
def test_is_no_call_returns_false_for_valid_or_unrecognized(genotype: str) -> None:
    """Valid calls, lowercase, and unrecognized strings → False."""
    assert is_no_call(genotype) is False


# --------------------------------------------------------------------------- #
# Return-type lock                                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "genotype,expected",
    [
        (None, True),
        ("--", True),
        ("??", True),
        ("AA", False),
        ("aa", False),
    ],
)
def test_is_no_call_returns_strict_bool(genotype: str | None, expected: bool) -> None:
    """Return value is strictly ``bool`` (not truthy / falsy)."""
    result = is_no_call(genotype)
    assert type(result) is bool
    assert result is expected
