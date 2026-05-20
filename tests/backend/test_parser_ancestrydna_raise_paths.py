"""Table-driven raise-path + canonicalization tests for ``parser_ancestrydna``.

Locks the AncestryDNA parser's contract for malformed inputs (ADNA-08e;
step 37; Plan §13.1) plus the two parse-time canonicalization rules from
Plan §8.6:

- Raise paths (a)–(g) — every ``MalformedDataError`` cause the parser
  promises to catch: empty rsid, non-numeric position, negative position,
  invalid chromosome ``"27"``, wrong column counts (4 and 6), and
  whitespace-only rsid. The dispatcher is bypassed here — each case is
  fed straight into :func:`parse_ancestrydna` via an in-memory stream so
  the assertion lands on the parser, not the format-detection layer.
- (h) Element-wise no-call rule — any allele equal to ``"0"`` collapses
  the *whole* call to ``"--"`` (Plan §8.6 #2). Mirrors the rule
  ``_canonical_genotype`` enforces in isolation in
  ``test_parser_ancestrydna.py``, but asserts at the
  :class:`~backend.ingestion.base.ParseResult` level so the end-to-end
  parse path is locked.
- (i) Sorted-pair canonicalization — uppercase + sorted-pair form is the
  storage shape for every diploid call (Plan §8.6 #6, #7). Same
  rationale as (h): parallel surface to the helper-level test, asserted
  through the full parse.

Companion file to ``test_parser_ancestrydna.py``: the smoke tests there
already exercise ``_canonical_genotype`` directly; this file pins the
contract through the public ``parse_ancestrydna`` entry point so a
regression that bypasses the helper still trips.
"""

from __future__ import annotations

import io

import pytest

from backend.ingestion.base import MalformedDataError, ParseResult
from backend.ingestion.parser_ancestrydna import parse_ancestrydna

# --------------------------------------------------------------------------- #
# Shared fixture material
# --------------------------------------------------------------------------- #

# Minimum head block that satisfies dispatcher format detection: an
# ``#AncestryDNA`` signature line plus the 5-column uncommented header.
# Re-stating it inline (rather than importing the helper from
# ``test_parser_ancestrydna``) keeps this file independently grep-able and
# avoids cross-test-module coupling.
_MIN_HEAD = (
    "#AncestryDNA raw data download\n"
    "#Fields are TAB-separated.\n"
    "rsid\tchromosome\tposition\tallele1\tallele2\n"
)


def _stream(*data_lines: str) -> io.StringIO:
    """Build an in-memory AncestryDNA file from one or more data lines."""
    body = "\n".join(data_lines)
    return io.StringIO(_MIN_HEAD + body + "\n")


# --------------------------------------------------------------------------- #
# Raise paths — (a) … (g)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "data_line,match",
    [
        # (a) Empty rsid — first tab-delimited field is the empty string.
        pytest.param("\t1\t100\tA\tG", "empty rsid", id="a-empty-rsid"),
        # (b) Non-numeric position — ``int(pos_raw)`` raises.
        pytest.param(
            "rs1\t1\tnotanumber\tA\tG",
            "non-numeric position",
            id="b-non-numeric-position",
        ),
        # (c) Negative position — parses to int(), then ``< 0`` guard fires.
        pytest.param(
            "rs1\t1\t-100\tA\tG",
            "negative position",
            id="c-negative-position",
        ),
        # (d) Invalid chromosome 27 — AncestryDNA's encoding tops out at 26
        # (MT). Anything past that is outside the vendor map and the canonical
        # ``1`` … ``22`` / ``X`` / ``Y`` / ``MT`` set, so ``normalize_for``
        # raises with the offending raw value quoted.
        pytest.param(
            "rs1\t27\t100\tA\tG",
            "Invalid chromosome value",
            id="d-invalid-chromosome-27",
        ),
        # (e) Wrong column count — 4 columns. ``_validate_line`` raises
        # before any field-level checks because the structure is unparseable.
        pytest.param(
            "rs1\t1\t100\tA",
            "expected 5 columns",
            id="e-wrong-column-count-4",
        ),
        # (f) Wrong column count — 6 columns. Same guard, opposite direction.
        pytest.param(
            "rs1\t1\t100\tA\tG\tX",
            "expected 5 columns",
            id="f-wrong-column-count-6",
        ),
        # (g) Whitespace-only rsid — strip()-then-empty resolves to the same
        # ``empty rsid`` error as (a). Locked separately so a future parser
        # that skipped the per-field strip (and therefore accepted ``"   "``
        # as a real rsid) would trip this assertion.
        pytest.param(
            "   \t1\t100\tA\tG",
            "empty rsid",
            id="g-whitespace-only-rsid",
        ),
    ],
)
def test_raises_malformed_data_error(data_line: str, match: str) -> None:
    """Plan §8.6 raise paths (a)–(g) — every malformed input raises
    :class:`MalformedDataError` with the expected message fragment.

    The error message fragment is part of the contract: downstream
    logging and the wizard's banner rely on it to surface a useful hint
    instead of a generic "parse failed" line.
    """
    with pytest.raises(MalformedDataError, match=match):
        parse_ancestrydna(_stream(data_line))


# --------------------------------------------------------------------------- #
# (h) Element-wise no-call rule
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "a1,a2",
    [
        # Plan §8.6 #2 explicitly calls out ``I<TAB>0`` → ``--``: the rule is
        # per-allele, not per-pair, so any ``0`` (or empty) on either side
        # collapses the whole call.
        pytest.param("I", "0", id="indel-then-zero"),
        pytest.param("0", "I", id="zero-then-indel"),
        pytest.param("D", "0", id="del-then-zero"),
        pytest.param("0", "D", id="zero-then-del"),
        pytest.param("A", "0", id="snv-then-zero"),
        pytest.param("0", "G", id="zero-then-snv"),
        # Element-wise empty-string case — exercises the second branch of
        # ``_NO_CALL_SENTINELS = frozenset({"0", ""})`` through the public
        # parse path. Whitespace-only allele fields strip to the empty
        # string before the no-call check, so this row also triggers the
        # element-wise rule rather than landing as a column-count error.
        pytest.param(" ", "T", id="whitespace-then-snv"),
    ],
)
def test_element_wise_no_call_rule(a1: str, a2: str) -> None:
    """Plan §8.6 #2 — any allele equal to ``"0"`` (or whitespace-only)
    canonicalizes the whole call to ``"--"`` *and* increments
    :attr:`ParseResult.nocall_count`.

    Asserted through the full parse path rather than through the
    ``_canonical_genotype`` helper so a regression that bypasses the
    helper inside the parser body still trips.
    """
    result = parse_ancestrydna(_stream(f"rs1\t1\t100\t{a1}\t{a2}"))
    assert isinstance(result, ParseResult)
    assert len(result.variants) == 1
    assert result.variants[0].genotype == "--"
    assert result.nocall_count == 1


# --------------------------------------------------------------------------- #
# (i) Sorted-pair storage normalization
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "a1,a2,expected",
    [
        # SNVs — alphabetic sort regardless of input order.
        pytest.param("G", "A", "AG", id="snv-g-a"),
        pytest.param("A", "G", "AG", id="snv-a-g"),
        # Indels — ``I<TAB>D`` and ``D<TAB>I`` both land at canonical ``"DI"``
        # (lexicographic sort: ``"D" < "I"``).
        pytest.param("I", "D", "DI", id="indel-i-d"),
        pytest.param("D", "I", "DI", id="indel-d-i"),
        # Mixed-case — Plan §8.6 #7 mandates uppercase-then-sort, so
        # ``g<TAB>A`` and ``A<TAB>g`` both collapse to ``"AG"``.
        pytest.param("g", "A", "AG", id="mixed-case-g-A"),
        pytest.param("A", "g", "AG", id="mixed-case-A-g"),
    ],
)
def test_sorted_pair_canonicalization(a1: str, a2: str, expected: str) -> None:
    """Plan §8.6 #6, #7 — the parser stores diploid calls in canonical
    uppercase + sorted-pair form, regardless of column ordering or input
    case.

    Asserted through the full parse path; ``_canonical_genotype``-level
    coverage lives in ``test_parser_ancestrydna.py``.
    """
    result = parse_ancestrydna(_stream(f"rs1\t1\t100\t{a1}\t{a2}"))
    assert len(result.variants) == 1
    variant = result.variants[0]
    assert variant.genotype == expected
    # Sorted-pair canonicalization rows are NOT no-calls — the nocall
    # bookkeeping must stay at zero so a regression that misroutes a
    # mixed-case input through the no-call path still trips.
    assert result.nocall_count == 0
