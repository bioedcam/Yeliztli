"""Tests for the within-account KING-robust kinship module.

The KING-robust estimator is φ = (N_hethet − 2·N_ibs0) / (Het_i + Het_j). These
tests build two rsID→genotype maps with controlled genotype-pair compositions so
the resulting φ, IBS0 proportion, and relationship band are exact and
deterministic: a duplicate scores ~0.5; parent-offspring and full-sibling both
sit at ~0.25 but split on IBS0; unrelated scores ~0; and a pair with too few
shared SNPs is reported as indeterminate.
"""

from __future__ import annotations

from backend.analysis.kinship import (
    MIN_SHARED_SNPS,
    _hom_allele,
    _is_het,
    king_kinship,
)


def _build(spec: list[tuple[int, str, str]]) -> tuple[dict[str, str], dict[str, str]]:
    """Build (genos_i, genos_j) from (count, genotype_i, genotype_j) tuples."""
    gi: dict[str, str] = {}
    gj: dict[str, str] = {}
    idx = 0
    for count, a, b in spec:
        for _ in range(count):
            rsid = f"r{idx}"
            idx += 1
            gi[rsid] = a
            gj[rsid] = b
    return gi, gj


class TestHelpers:
    def test_is_het(self) -> None:
        assert _is_het("AG") is True
        assert _is_het("AA") is False
        assert _is_het("A") is False
        assert _is_het("--") is False

    def test_hom_allele(self) -> None:
        assert _hom_allele("AA") == "A"
        assert _hom_allele("AG") is None
        assert _hom_allele("--") is None


class TestKingRobust:
    def test_duplicate_scores_half(self) -> None:
        gi, gj = _build([(1500, "AG", "AG"), (1500, "AA", "AA")])
        s = king_kinship(gi, gj)
        assert s.phi == 0.5
        assert s.relationship == "duplicate_or_mz_twin"
        assert s.n_shared == 3000

    def test_parent_offspring(self) -> None:
        # φ = 0.25 with zero opposite homozygotes → parent-offspring.
        gi, gj = _build(
            [(1000, "AG", "AG"), (1000, "AG", "AA"), (1000, "AA", "AG"), (1000, "AA", "AA")]
        )
        s = king_kinship(gi, gj)
        assert s.phi == 0.25
        assert s.ibs0 == 0
        assert s.relationship == "parent_offspring"

    def test_full_sibling(self) -> None:
        # Same 1st-degree φ band but a meaningful IBS0 fraction → full sibling.
        gi, gj = _build(
            [
                (1000, "AG", "AG"),
                (1000, "AG", "AA"),
                (1000, "AA", "AG"),
                (900, "AA", "AA"),
                (100, "AA", "GG"),  # opposite homozygotes → IBS0
            ]
        )
        s = king_kinship(gi, gj)
        assert s.ibs0 == 100
        assert 0.177 <= s.phi <= 0.354
        assert s.relationship == "full_sibling"

    def test_unrelated_scores_zero(self) -> None:
        gi, gj = _build([(2000, "AG", "AA"), (2000, "AA", "AG")])
        s = king_kinship(gi, gj)
        assert s.phi == 0.0
        assert s.relationship == "unrelated"

    def test_indeterminate_when_few_shared_snps(self) -> None:
        gi, gj = _build([(MIN_SHARED_SNPS - 1, "AG", "AG")])
        s = king_kinship(gi, gj)
        assert s.n_shared < MIN_SHARED_SNPS
        assert s.relationship == "indeterminate"

    def test_only_intersecting_rsids_count(self) -> None:
        gi = {"r1": "AG", "r2": "AA", "only_i": "GG"}
        gj = {"r1": "AG", "r2": "AA", "only_j": "CC"}
        s = king_kinship(gi, gj)
        assert s.n_shared == 2  # only r1, r2 are shared

    def test_malformed_genotype_not_counted_as_ibs0(self) -> None:
        # Malformed (non-biallelic) calls must not inflate the opposite-homozygote
        # count; only the genuine AA/GG opposite homozygote (r3) is an IBS0.
        gi = {"r1": "A", "r2": "AAA", "r3": "AA"}
        gj = {"r1": "GG", "r2": "GG", "r3": "GG"}
        s = king_kinship(gi, gj)
        assert s.ibs0 == 1
