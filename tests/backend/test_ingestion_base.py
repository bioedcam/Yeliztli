"""Shared parser types contract — Plan §8.2.

Locks the public surface that the dispatcher (step 27), the AncestryDNA parser
(step 30), and the 23andMe refactor (step 29) all import from. Failures here
break every downstream parser-layer step, so the assertions are intentionally
mechanical.
"""

from __future__ import annotations

import dataclasses

import pytest

from backend.ingestion.base import (
    MalformedDataError,
    ParsedVariant,
    ParserError,
    ParseResult,
    SourceVendor,
    UnrecognizedVersionError,
    UnsupportedFormatError,
)


class TestSourceVendor:
    def test_enum_values(self) -> None:
        assert SourceVendor.TWENTYTHREEANDME.value == "23andme"
        assert SourceVendor.ANCESTRYDNA.value == "ancestrydna"

    def test_enum_membership(self) -> None:
        assert set(SourceVendor) == {
            SourceVendor.TWENTYTHREEANDME,
            SourceVendor.ANCESTRYDNA,
        }

    def test_value_round_trip(self) -> None:
        assert SourceVendor("23andme") is SourceVendor.TWENTYTHREEANDME
        assert SourceVendor("ancestrydna") is SourceVendor.ANCESTRYDNA


class TestParsedVariant:
    def test_fields_and_types(self) -> None:
        variant = ParsedVariant(rsid="rs1234", chrom="1", pos=12345, genotype="AG")
        assert variant.rsid == "rs1234"
        assert variant.chrom == "1"
        assert variant.pos == 12345
        assert variant.genotype == "AG"

    def test_is_frozen(self) -> None:
        variant = ParsedVariant(rsid="rs1", chrom="1", pos=1, genotype="AA")
        with pytest.raises(dataclasses.FrozenInstanceError):
            variant.rsid = "rs2"  # type: ignore[misc]

    def test_uses_slots(self) -> None:
        variant = ParsedVariant(rsid="rs1", chrom="1", pos=1, genotype="AA")
        assert not hasattr(variant, "__dict__")

    def test_is_hashable(self) -> None:
        a = ParsedVariant(rsid="rs1", chrom="1", pos=1, genotype="AA")
        b = ParsedVariant(rsid="rs1", chrom="1", pos=1, genotype="AA")
        c = ParsedVariant(rsid="rs2", chrom="1", pos=1, genotype="AA")
        assert hash(a) == hash(b)
        assert {a, b, c} == {a, c}


class TestParseResult:
    def test_minimal_construction(self) -> None:
        result = ParseResult(vendor=SourceVendor.TWENTYTHREEANDME, version="v5", build="GRCh37")
        assert result.vendor is SourceVendor.TWENTYTHREEANDME
        assert result.version == "v5"
        assert result.build == "GRCh37"
        assert result.variants == []
        assert result.nocall_count == 0
        assert result.total_lines == 0
        assert result.skipped_lines == 0

    def test_full_construction(self) -> None:
        variants = [
            ParsedVariant(rsid="rs1", chrom="1", pos=1, genotype="AA"),
            ParsedVariant(rsid="rs2", chrom="2", pos=2, genotype="--"),
        ]
        result = ParseResult(
            vendor=SourceVendor.ANCESTRYDNA,
            version="v2.0",
            build="GRCh37",
            variants=variants,
            nocall_count=1,
            total_lines=4,
            skipped_lines=2,
        )
        assert result.vendor is SourceVendor.ANCESTRYDNA
        assert result.version == "v2.0"
        assert result.variants == variants
        assert result.nocall_count == 1
        assert result.total_lines == 4
        assert result.skipped_lines == 2

    def test_version_is_string(self) -> None:
        # Plan §8.2 / §8.7: `version` is freeform string, not an enum, so the
        # dispatcher can compose `f"{vendor.value}_{version}"` without coercion.
        result = ParseResult(vendor=SourceVendor.ANCESTRYDNA, version="v2.0", build="GRCh37")
        assert isinstance(result.version, str)
        assert f"{result.vendor.value}_{result.version}" == "ancestrydna_v2.0"

    def test_default_variants_list_not_shared(self) -> None:
        a = ParseResult(vendor=SourceVendor.TWENTYTHREEANDME, version="v5", build="GRCh37")
        b = ParseResult(vendor=SourceVendor.TWENTYTHREEANDME, version="v5", build="GRCh37")
        a.variants.append(ParsedVariant(rsid="rs1", chrom="1", pos=1, genotype="AA"))
        assert b.variants == []


class TestExceptionHierarchy:
    def test_parser_error_is_exception(self) -> None:
        assert issubclass(ParserError, Exception)

    @pytest.mark.parametrize(
        "subclass",
        [UnsupportedFormatError, MalformedDataError, UnrecognizedVersionError],
    )
    def test_subclasses_of_parser_error(self, subclass: type[Exception]) -> None:
        assert issubclass(subclass, ParserError)

    def test_subclasses_are_distinct(self) -> None:
        assert not issubclass(UnsupportedFormatError, MalformedDataError)
        assert not issubclass(MalformedDataError, UnrecognizedVersionError)
        assert not issubclass(UnrecognizedVersionError, UnsupportedFormatError)

    def test_message_round_trip(self) -> None:
        err = MalformedDataError("Line 7: empty rsid")
        assert str(err) == "Line 7: empty rsid"
        assert isinstance(err, ParserError)


class TestPublicSurface:
    def test_module_all_exports(self) -> None:
        from backend.ingestion import base

        assert set(base.__all__) == {
            "SourceVendor",
            "ParsedVariant",
            "ParseResult",
            "ParserError",
            "UnsupportedFormatError",
            "MalformedDataError",
            "UnrecognizedVersionError",
        }
