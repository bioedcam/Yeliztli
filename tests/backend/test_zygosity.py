"""Tests for the shared zygosity helpers (backend.analysis.zygosity).

Covers ``classify_zygosity`` — the carriage test that resolves a chip genotype
to hom_ref / het / hom_alt against ClinVar ref/alt, including strand-flip
resolution and the unscoreable cases (indels, no-calls, off-strand alleles)
that must return ``None`` so callers never treat them as carried.
"""

from __future__ import annotations

import pytest

from backend.analysis.zygosity import (
    CARRIED_ZYGOSITIES,
    ZYG_HET,
    ZYG_HOM_ALT,
    ZYG_HOM_REF,
    classify_zygosity,
    is_no_call,
)


class TestClassifyZygosityReferenceStrand:
    """Direct (+ strand) genotype vs ClinVar ref/alt comparisons."""

    def test_heterozygous(self) -> None:
        assert classify_zygosity("CT", "C", "T") == ZYG_HET

    def test_heterozygous_allele_order_independent(self) -> None:
        assert classify_zygosity("TC", "C", "T") == ZYG_HET

    def test_homozygous_reference(self) -> None:
        assert classify_zygosity("CC", "C", "T") == ZYG_HOM_REF

    def test_homozygous_alt(self) -> None:
        assert classify_zygosity("TT", "C", "T") == ZYG_HOM_ALT

    def test_haploid_alt(self) -> None:
        # Haploid call (e.g. X/Y for an XY individual) is treated as homozygous.
        assert classify_zygosity("T", "C", "T") == ZYG_HOM_ALT

    def test_haploid_ref(self) -> None:
        assert classify_zygosity("C", "C", "T") == ZYG_HOM_REF


class TestClassifyZygosityStrandFlip:
    """23andMe probes reported on the reverse strand resolve via complement."""

    def test_reverse_strand_heterozygous(self) -> None:
        # ref C / alt T → complement ref G / alt A; "GA" carries both.
        assert classify_zygosity("GA", "C", "T") == ZYG_HET

    def test_reverse_strand_homozygous_alt(self) -> None:
        # "AA" is the complement of the alt (T) → hom_alt.
        assert classify_zygosity("AA", "C", "T") == ZYG_HOM_ALT

    def test_reverse_strand_homozygous_ref(self) -> None:
        assert classify_zygosity("GG", "C", "T") == ZYG_HOM_REF


class TestClassifyZygosityPalindromic:
    """Palindromic SNPs (A/T, C/G) are taken at face value on the + strand."""

    def test_palindromic_het(self) -> None:
        assert classify_zygosity("AT", "A", "T") == ZYG_HET

    def test_palindromic_hom_ref_face_value(self) -> None:
        assert classify_zygosity("AA", "A", "T") == ZYG_HOM_REF

    def test_palindromic_hom_alt_face_value(self) -> None:
        assert classify_zygosity("TT", "A", "T") == ZYG_HOM_ALT


class TestClassifyZygosityUnscoreable:
    """Cases that cannot be confidently scored must return None."""

    @pytest.mark.parametrize("genotype", ["--", "??", "DD", "II", "DI", "ID", "00", "-", "", "  "])
    def test_no_call_genotypes(self, genotype: str) -> None:
        assert classify_zygosity(genotype, "C", "T") is None

    def test_none_genotype(self) -> None:
        assert classify_zygosity(None, "C", "T") is None

    def test_indel_ref_multibase(self) -> None:
        # ClinVar deletion (ref TAAAAG / alt T): no single-base chip mapping.
        assert classify_zygosity("II", "TAAAAG", "T") is None
        assert classify_zygosity("AG", "TAAAAG", "T") is None

    def test_indel_alt_multibase(self) -> None:
        assert classify_zygosity("AG", "A", "AT") is None

    def test_missing_ref_or_alt(self) -> None:
        assert classify_zygosity("CT", "", "T") is None
        assert classify_zygosity("CT", "C", "") is None
        assert classify_zygosity("CT", None, "T") is None

    def test_allele_explained_by_neither_strand(self) -> None:
        # {C,G} matches neither {C,T} nor its complement {G,A}.
        assert classify_zygosity("CG", "C", "T") is None

    def test_non_acgt_allele(self) -> None:
        assert classify_zygosity("A-", "C", "T") is None
        assert classify_zygosity("AN", "C", "T") is None

    def test_overlong_genotype(self) -> None:
        assert classify_zygosity("CTA", "C", "T") is None


class TestModuleConstants:
    def test_carried_zygosities(self) -> None:
        assert CARRIED_ZYGOSITIES == frozenset({ZYG_HET, ZYG_HOM_ALT})
        assert ZYG_HOM_REF not in CARRIED_ZYGOSITIES

    def test_is_no_call_still_works(self) -> None:
        assert is_no_call("--") is True
        assert is_no_call("CT") is False
