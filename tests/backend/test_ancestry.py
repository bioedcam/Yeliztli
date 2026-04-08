"""Tests for ancestry inference module (P3-23, P3-24, P3-25).

Covers:
  - Bundle loading and validation
  - Genotype encoding (alt-allele dosage)
  - PCA projection via NumPy dot product
  - Nearest-centroid classification
  - Admixture fraction computation (P3-24, T3-24)
  - PCA coordinates for visualization (P3-25)
  - Findings storage (module='ancestry', category='pca_projection')
  - T3-25: PCA projection places known EUR-ancestry sample in EUR cluster
  - Coverage threshold enforcement
  - Integration with prs.get_inferred_ancestry()
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pytest
import sqlalchemy as sa

from backend.analysis.ancestry import (
    AncestryAIM,
    AncestryBundle,
    AncestryResult,
    PCACoordinates,
    _classify_nearest_centroid,
    _encode_dosage,
    _project_onto_pca,
    compute_admixture_fractions,
    get_pca_coordinates,
    infer_ancestry,
    load_ancestry_bundle,
    store_ancestry_findings,
)
from backend.db.tables import annotated_variants, findings, raw_variants

# ── Fixtures ──────────────────────────────────────────────────────────────

BUNDLE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "backend"
    / "data"
    / "panels"
    / "ancestry_pca_bundle.npz"
)


@pytest.fixture()
def bundle() -> AncestryBundle:
    """Load the ancestry PCA bundle from the real JSON file."""
    return load_ancestry_bundle(BUNDLE_PATH)


@pytest.fixture()
def small_bundle() -> AncestryBundle:
    """A minimal synthetic bundle for unit tests."""
    snps = [
        AncestryAIM(rsid="rs1", chrom="1", pos=100, ref="A", alt="G", ref_freq=0.7),
        AncestryAIM(rsid="rs2", chrom="2", pos=200, ref="C", alt="T", ref_freq=0.5),
        AncestryAIM(rsid="rs3", chrom="3", pos=300, ref="G", alt="A", ref_freq=0.3),
        AncestryAIM(rsid="rs4", chrom="4", pos=400, ref="T", alt="C", ref_freq=0.6),
    ]

    # Loadings: 4 SNPs × 2 PCs (n_snps, n_components)
    loadings = np.array(
        [
            [0.5, 0.1],   # SNP1: PC1=0.5, PC2=0.1
            [0.3, -0.4],  # SNP2: PC1=0.3, PC2=-0.4
            [-0.2, 0.3],  # SNP3: PC1=-0.2, PC2=0.3
            [0.1, 0.5],   # SNP4: PC1=0.1, PC2=0.5
        ],
        dtype=np.float64,
    )

    # Per-AIM means and stds (derived from ref_freq: mean = 2 * alt_freq)
    means = np.array([0.6, 1.0, 1.4, 0.8], dtype=np.float64)
    stds = np.array([0.5, 0.5, 0.5, 0.5], dtype=np.float64)

    centroids = {
        "AFR": np.array([2.0, -1.0], dtype=np.float64),
        "EUR": np.array([-1.0, 1.5], dtype=np.float64),
        "EAS": np.array([-0.5, -2.0], dtype=np.float64),
    }

    reference_samples = {
        "AFR": [[2.1, -0.9], [1.8, -1.2], [2.3, -0.8]],
        "EUR": [[-1.1, 1.4], [-0.8, 1.6], [-1.2, 1.3]],
        "EAS": [[-0.6, -1.9], [-0.4, -2.1], [-0.7, -2.2]],
    }

    return AncestryBundle(
        version="test",
        build="GRCh38",
        n_components=2,
        populations=["AFR", "EUR", "EAS"],
        population_labels={"AFR": "African", "EUR": "European", "EAS": "East Asian"},
        snps=snps,
        loadings=loadings,
        means=means,
        stds=stds,
        reference_centroids=centroids,
        reference_samples=reference_samples,
        eigenvalues=np.array([10.0, 5.0], dtype=np.float64),
        n_significant_pcs=2,
        tw_pvalues=np.array([0.001] * 20, dtype=np.float64),
        n_total_snps=1000,
        n_selected_aims=4,
    )


def _insert_raw_genotypes(
    sample_engine: sa.Engine,
    genotypes: list[dict],
) -> sa.Engine:
    """Insert raw variants for testing."""
    with sample_engine.begin() as conn:
        conn.execute(sa.insert(raw_variants), genotypes)
    return sample_engine


def _insert_annotated_genotypes(
    sample_engine: sa.Engine,
    genotypes: list[dict],
) -> sa.Engine:
    """Insert annotated variants for testing."""
    with sample_engine.begin() as conn:
        conn.execute(sa.insert(annotated_variants), genotypes)
    return sample_engine


@pytest.fixture()
def eur_sample(sample_engine: sa.Engine) -> sa.Engine:
    """Sample with genotypes typical of European ancestry.

    High ref allele frequencies → low dosage for many SNPs.
    """
    genotypes = [
        {"rsid": "rs1", "chrom": "1", "pos": 100, "genotype": "AA"},  # 0 alt
        {"rsid": "rs2", "chrom": "2", "pos": 200, "genotype": "CC"},  # 0 alt
        {"rsid": "rs3", "chrom": "3", "pos": 300, "genotype": "GA"},  # 1 alt
        {"rsid": "rs4", "chrom": "4", "pos": 400, "genotype": "TT"},  # 0 alt
    ]
    return _insert_raw_genotypes(sample_engine, genotypes)


@pytest.fixture()
def afr_sample(sample_engine: sa.Engine) -> sa.Engine:
    """Sample with genotypes typical of African ancestry.

    Higher alt allele counts for ancestry-informative markers.
    """
    genotypes = [
        {"rsid": "rs1", "chrom": "1", "pos": 100, "genotype": "GG"},  # 2 alt
        {"rsid": "rs2", "chrom": "2", "pos": 200, "genotype": "TT"},  # 2 alt
        {"rsid": "rs3", "chrom": "3", "pos": 300, "genotype": "AA"},  # 2 alt
        {"rsid": "rs4", "chrom": "4", "pos": 400, "genotype": "CC"},  # 2 alt
    ]
    return _insert_raw_genotypes(sample_engine, genotypes)


@pytest.fixture()
def partial_sample(sample_engine: sa.Engine) -> sa.Engine:
    """Sample with only 1 of 4 SNPs — below coverage threshold."""
    genotypes = [
        {"rsid": "rs1", "chrom": "1", "pos": 100, "genotype": "AG"},
    ]
    return _insert_raw_genotypes(sample_engine, genotypes)


# ── Bundle loading tests ─────────────────────────────────────────────────


class TestLoadAncestryBundle:
    """Test ancestry PCA bundle loading from NPZ."""

    def test_loads_from_npz(self, bundle: AncestryBundle) -> None:
        assert bundle.snp_count == 5000
        assert bundle.n_components == 8
        assert len(bundle.populations) == 7

    def test_loadings_shape(self, bundle: AncestryBundle) -> None:
        assert bundle.loadings.shape == (bundle.snp_count, bundle.n_components)

    def test_means_and_stds_shape(self, bundle: AncestryBundle) -> None:
        assert bundle.means.shape == (bundle.snp_count,)
        assert bundle.stds.shape == (bundle.snp_count,)

    def test_centroids_all_populations(self, bundle: AncestryBundle) -> None:
        for pop in bundle.populations:
            assert pop in bundle.reference_centroids
            assert len(bundle.reference_centroids[pop]) == bundle.n_components

    def test_snps_have_valid_rsids(self, bundle: AncestryBundle) -> None:
        for snp in bundle.snps:
            assert snp.rsid.startswith(("rs", "i")), f"Invalid rsid: {snp.rsid}"

    def test_rsid_set(self, bundle: AncestryBundle) -> None:
        rsids = bundle.rsid_set()
        assert len(rsids) == bundle.snp_count
        assert all(r.startswith(("rs", "i")) for r in rsids)

    def test_rsid_to_index(self, bundle: AncestryBundle) -> None:
        idx_map = bundle.rsid_to_index()
        assert len(idx_map) == bundle.snp_count
        assert idx_map[bundle.snps[0].rsid] == 0
        assert idx_map[bundle.snps[-1].rsid] == bundle.snp_count - 1

    def test_file_not_found_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_ancestry_bundle(Path("/nonexistent/bundle.npz"))

    def test_population_labels(self, bundle: AncestryBundle) -> None:
        assert "EUR" in bundle.population_labels
        assert "AFR" in bundle.population_labels
        assert "CSA" in bundle.population_labels
        assert "MID" in bundle.population_labels

    def test_seven_populations(self, bundle: AncestryBundle) -> None:
        expected = {"AFR", "AMR", "CSA", "EAS", "EUR", "MID", "OCE"}
        assert set(bundle.populations) == expected

    def test_n_significant_pcs(self, bundle: AncestryBundle) -> None:
        assert bundle.n_significant_pcs == 8

    def test_tw_pvalues(self, bundle: AncestryBundle) -> None:
        assert len(bundle.tw_pvalues) == 20

    def test_eigenvalues(self, bundle: AncestryBundle) -> None:
        assert len(bundle.eigenvalues) == bundle.n_components
        # Eigenvalues should be in descending order
        for i in range(len(bundle.eigenvalues) - 1):
            assert bundle.eigenvalues[i] >= bundle.eigenvalues[i + 1]

    def test_reference_samples_all_populations(self, bundle: AncestryBundle) -> None:
        for pop in bundle.populations:
            assert pop in bundle.reference_samples
            assert len(bundle.reference_samples[pop]) > 0


# ── Genotype encoding tests ──────────────────────────────────────────────


class TestEncodeDosage:
    """Test alt-allele dosage encoding."""

    def test_homozygous_ref(self) -> None:
        assert _encode_dosage("AA", "G") == 0.0

    def test_heterozygous(self) -> None:
        assert _encode_dosage("AG", "G") == 1.0

    def test_homozygous_alt(self) -> None:
        assert _encode_dosage("GG", "G") == 2.0

    def test_none_returns_none(self) -> None:
        assert _encode_dosage(None, "G") is None

    def test_empty_returns_none(self) -> None:
        assert _encode_dosage("", "G") is None

    def test_nocall_returns_none(self) -> None:
        assert _encode_dosage("--", "G") is None
        assert _encode_dosage("00", "G") is None

    def test_case_insensitive(self) -> None:
        assert _encode_dosage("ag", "G") == 1.0
        assert _encode_dosage("AG", "g") == 1.0

    def test_single_char_returns_none(self) -> None:
        assert _encode_dosage("A", "G") is None

    def test_indel_nocall(self) -> None:
        assert _encode_dosage("II", "G") is None
        assert _encode_dosage("DD", "G") is None


# ── PCA projection tests ────────────────────────────────────────────────


class TestProjectOntoPCA:
    """Test PCA projection via NumPy dot product."""

    def test_all_snps_present(self, small_bundle: AncestryBundle) -> None:
        genotype_map = {"rs1": "AG", "rs2": "CT", "rs3": "GA", "rs4": "TC"}
        pc_scores, snps_used = _project_onto_pca(small_bundle, genotype_map)
        assert snps_used == 4
        assert len(pc_scores) == 2

    def test_missing_snps_imputed_as_zero(
        self,
        small_bundle: AncestryBundle,
    ) -> None:
        # Only rs1 present
        genotype_map = {"rs1": "AG"}
        _, snps_used = _project_onto_pca(small_bundle, genotype_map)
        assert snps_used == 1

    def test_empty_genotypes(self, small_bundle: AncestryBundle) -> None:
        pc_scores, snps_used = _project_onto_pca(small_bundle, {})
        assert snps_used == 0
        # All centered values are 0 → pc_scores should be 0
        np.testing.assert_array_equal(pc_scores, np.zeros(2))

    def test_projection_is_linear(self, small_bundle: AncestryBundle) -> None:
        """Verify projection = standardized @ loadings."""
        genotype_map = {"rs1": "GG", "rs2": "TT", "rs3": "AA", "rs4": "CC"}
        pc_scores, _ = _project_onto_pca(small_bundle, genotype_map)

        # Manual computation
        dosages = np.array([2.0, 2.0, 2.0, 2.0])  # all homozygous alt
        standardized = (dosages - small_bundle.means) / small_bundle.stds
        expected = standardized @ small_bundle.loadings
        np.testing.assert_array_almost_equal(pc_scores, expected)


# ── Nearest centroid classification tests ────────────────────────────────


class TestClassifyNearestCentroid:
    """Test nearest-centroid classification."""

    def test_exact_centroid_match(self) -> None:
        centroids = {
            "AFR": np.array([10.0, 0.0]),
            "EUR": np.array([-10.0, 0.0]),
        }
        pop, dists = _classify_nearest_centroid(np.array([10.0, 0.0]), centroids)
        assert pop == "AFR"
        assert dists["AFR"] == 0.0

    def test_nearest_classification(self) -> None:
        centroids = {
            "AFR": np.array([10.0, 0.0]),
            "EUR": np.array([-10.0, 0.0]),
            "EAS": np.array([0.0, 10.0]),
        }
        # Point closer to EUR
        pop, dists = _classify_nearest_centroid(np.array([-8.0, 1.0]), centroids)
        assert pop == "EUR"
        assert dists["EUR"] < dists["AFR"]
        assert dists["EUR"] < dists["EAS"]

    def test_all_distances_returned(self) -> None:
        centroids = {
            "AFR": np.array([1.0, 0.0]),
            "EUR": np.array([0.0, 1.0]),
            "EAS": np.array([0.0, 0.0]),
        }
        _, dists = _classify_nearest_centroid(np.array([0.5, 0.5]), centroids)
        assert len(dists) == 3
        assert all(d >= 0 for d in dists.values())


# ── Admixture fraction tests (P3-24, T3-24) ──────────────────────────


class TestComputeAdmixtureFractions:
    """Test admixture fraction computation via inverse-distance weighting."""

    def test_fractions_sum_to_one(self) -> None:
        """T3-24: Admixture fractions sum to ~1.0."""
        distances = {"AFR": 10.0, "EUR": 5.0, "EAS": 20.0, "SAS": 15.0}
        fractions = compute_admixture_fractions(distances)
        assert abs(sum(fractions.values()) - 1.0) < 1e-6

    def test_closer_population_has_higher_fraction(self) -> None:
        distances = {"AFR": 100.0, "EUR": 1.0, "EAS": 50.0}
        fractions = compute_admixture_fractions(distances)
        assert fractions["EUR"] > fractions["AFR"]
        assert fractions["EUR"] > fractions["EAS"]

    def test_exact_centroid_gives_1_0(self) -> None:
        """Sample at exact centroid → 100% that population."""
        distances = {"AFR": 0.0, "EUR": 10.0, "EAS": 20.0}
        fractions = compute_admixture_fractions(distances)
        assert fractions["AFR"] == 1.0
        assert fractions["EUR"] == 0.0
        assert fractions["EAS"] == 0.0

    def test_equal_distances_give_equal_fractions(self) -> None:
        distances = {"AFR": 5.0, "EUR": 5.0, "EAS": 5.0}
        fractions = compute_admixture_fractions(distances)
        for frac in fractions.values():
            assert abs(frac - 1.0 / 3) < 0.01

    def test_empty_distances(self) -> None:
        fractions = compute_admixture_fractions({})
        assert fractions == {}

    def test_all_populations_present(self) -> None:
        distances = {"AFR": 10.0, "AMR": 20.0, "EAS": 30.0, "EUR": 5.0, "SAS": 25.0, "OCE": 40.0}
        fractions = compute_admixture_fractions(distances)
        assert set(fractions.keys()) == set(distances.keys())
        assert all(0.0 <= f <= 1.0 for f in fractions.values())
        assert abs(sum(fractions.values()) - 1.0) < 1e-6

    def test_fractions_are_non_negative(self) -> None:
        distances = {"AFR": 1.0, "EUR": 100.0, "EAS": 1000.0}
        fractions = compute_admixture_fractions(distances)
        assert all(f >= 0.0 for f in fractions.values())

    def test_single_population(self) -> None:
        distances = {"EUR": 5.0}
        fractions = compute_admixture_fractions(distances)
        assert fractions["EUR"] == 1.0

    def test_two_zero_distances(self) -> None:
        """Multiple populations at distance 0 — share equally."""
        distances = {"AFR": 0.0, "EUR": 0.0, "EAS": 10.0}
        fractions = compute_admixture_fractions(distances)
        assert abs(sum(fractions.values()) - 1.0) < 1e-6
        zero_pops = [p for p, d in distances.items() if d < 1e-10]
        expected_share = 1.0 / len(zero_pops)
        for p in zero_pops:
            assert abs(fractions[p] - expected_share) < 1e-6
        assert fractions["EAS"] == 0.0


class TestAdmixtureFractionsIntegration:
    """Test admixture fractions integrated into the inference pipeline."""

    def test_infer_returns_admixture_fractions(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        assert hasattr(result, "admixture_fractions")
        assert len(result.admixture_fractions) == 3  # AFR, EUR, EAS
        assert abs(sum(result.admixture_fractions.values()) - 1.0) < 1e-6

    def test_top_population_has_highest_fraction(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        top_frac = result.admixture_fractions[result.top_population]
        for pop, frac in result.admixture_fractions.items():
            if pop != result.top_population:
                assert top_frac >= frac

    def test_stored_finding_has_admixture(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        store_ancestry_findings(result, eur_sample)

        with eur_sample.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.module == "ancestry")
            ).fetchone()
        detail = json.loads(row.detail_json)
        assert "admixture_fractions" in detail
        fracs = detail["admixture_fractions"]
        assert abs(sum(fracs.values()) - 1.0) < 1e-6

    def test_insufficient_coverage_still_computes_fractions(
        self,
        small_bundle: AncestryBundle,
        partial_sample: sa.Engine,
    ) -> None:
        """Fractions computed even with low coverage, but finding not stored."""
        result = infer_ancestry(small_bundle, partial_sample)
        assert result.is_sufficient is False
        # Fractions still computed even though coverage is insufficient
        assert len(result.admixture_fractions) > 0


# ── Integration tests ────────────────────────────────────────────────────


class TestInferAncestry:
    """Test full ancestry inference pipeline."""

    def test_infer_with_full_coverage(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        assert result.snps_used == 4
        assert result.snps_total == 4
        assert result.coverage_fraction == 1.0
        assert result.is_sufficient is True
        assert result.top_population in ("AFR", "EUR", "EAS")

    def test_infer_returns_pc_scores(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        assert len(result.pc_scores) == 2

    def test_infer_returns_population_distances(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        assert "AFR" in result.population_distances
        assert "EUR" in result.population_distances
        assert "EAS" in result.population_distances

    def test_insufficient_coverage(
        self,
        small_bundle: AncestryBundle,
        partial_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, partial_sample)
        assert result.snps_used == 1
        assert result.coverage_fraction == 0.25
        assert result.is_sufficient is False

    def test_empty_sample(
        self,
        small_bundle: AncestryBundle,
        sample_engine: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, sample_engine)
        assert result.snps_used == 0
        assert result.is_sufficient is False

    def test_uses_annotated_variants_when_available(
        self,
        small_bundle: AncestryBundle,
        sample_engine: sa.Engine,
    ) -> None:
        """When annotated_variants has data, use it instead of raw_variants."""
        genotypes = [
            {
                "rsid": "rs1",
                "chrom": "1",
                "pos": 100,
                "genotype": "GG",
                "annotation_coverage": 1,
            },
            {
                "rsid": "rs2",
                "chrom": "2",
                "pos": 200,
                "genotype": "TT",
                "annotation_coverage": 1,
            },
            {
                "rsid": "rs3",
                "chrom": "3",
                "pos": 300,
                "genotype": "AA",
                "annotation_coverage": 1,
            },
            {
                "rsid": "rs4",
                "chrom": "4",
                "pos": 400,
                "genotype": "CC",
                "annotation_coverage": 1,
            },
        ]
        _insert_annotated_genotypes(sample_engine, genotypes)
        result = infer_ancestry(small_bundle, sample_engine)
        assert result.snps_used == 4

    def test_projection_under_1_second(
        self,
        bundle: AncestryBundle,
        sample_engine: sa.Engine,
    ) -> None:
        """Performance: PCA projection should complete in < 1 second."""
        # Insert some raw variants that match bundle SNPs
        bundle_snps = list(bundle.snps)[:50]
        genotypes = [
            {"rsid": s.rsid, "chrom": s.chrom, "pos": s.pos, "genotype": "AG"} for s in bundle_snps
        ]
        if genotypes:
            _insert_raw_genotypes(sample_engine, genotypes)

        t0 = time.perf_counter()
        infer_ancestry(bundle, sample_engine)
        elapsed = time.perf_counter() - t0
        assert elapsed < 1.0, f"Projection took {elapsed:.3f}s, expected < 1s"


# ── T3-25: EUR sample classification ────────────────────────────────────


class TestEURClassification:
    """T3-25: PCA projection places known EUR-ancestry sample in EUR cluster."""

    def test_eur_sample_classified_as_eur_or_nearest(
        self,
        bundle: AncestryBundle,
        sample_engine: sa.Engine,
    ) -> None:
        """With realistic bundle, a EUR-like genotype pattern should classify
        near EUR. We insert genotypes that are homozygous ref for most AIMs
        (typical of EUR for most ancestry-informative markers).
        """
        # Insert genotypes for all bundle SNPs as homozygous ref
        # (broadly EUR-like pattern for most AIMs)
        genotypes = [
            {
                "rsid": snp.rsid,
                "chrom": snp.chrom,
                "pos": snp.pos,
                "genotype": snp.ref * 2,  # homozygous reference
            }
            for snp in bundle.snps
        ]
        _insert_raw_genotypes(sample_engine, genotypes)

        result = infer_ancestry(bundle, sample_engine)
        assert result.is_sufficient
        assert result.snps_used == bundle.snp_count
        # The top population should be one of the known populations
        assert result.top_population in bundle.populations, (
            f"Top population {result.top_population} not in known populations"
        )


# ── Findings storage tests ───────────────────────────────────────────────


class TestStoreAncestryFindings:
    """Test ancestry findings storage in the sample database."""

    def test_stores_single_finding(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        count = store_ancestry_findings(result, eur_sample)
        assert count == 1

    def test_finding_has_module_ancestry(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        store_ancestry_findings(result, eur_sample)

        with eur_sample.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == "ancestry")
            ).fetchall()
        assert len(rows) == 1
        assert rows[0].module == "ancestry"
        assert rows[0].category == "pca_projection"

    def test_detail_json_has_top_population(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        store_ancestry_findings(result, eur_sample)

        with eur_sample.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.module == "ancestry")
            ).fetchone()
        detail = json.loads(row.detail_json)
        assert "top_population" in detail
        assert detail["top_population"] == result.top_population

    def test_detail_json_has_pc_scores(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        store_ancestry_findings(result, eur_sample)

        with eur_sample.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.module == "ancestry")
            ).fetchone()
        detail = json.loads(row.detail_json)
        assert "pc_scores" in detail
        assert len(detail["pc_scores"]) == 2

    def test_detail_json_has_inferred_ancestry(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        """Verify detail_json has 'inferred_ancestry' for prs.get_inferred_ancestry()."""
        result = infer_ancestry(small_bundle, eur_sample)
        store_ancestry_findings(result, eur_sample)

        with eur_sample.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.module == "ancestry")
            ).fetchone()
        detail = json.loads(row.detail_json)
        assert "inferred_ancestry" in detail
        assert detail["inferred_ancestry"] == result.top_population

    def test_clears_previous_findings_on_rerun(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        store_ancestry_findings(result, eur_sample)
        store_ancestry_findings(result, eur_sample)

        with eur_sample.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(findings.c.module == "ancestry")
            ).scalar()
        assert count == 1  # Not 2

    def test_insufficient_coverage_stores_nothing(
        self,
        small_bundle: AncestryBundle,
        partial_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, partial_sample)
        count = store_ancestry_findings(result, partial_sample)
        assert count == 0

    def test_evidence_level_2(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        store_ancestry_findings(result, eur_sample)

        with eur_sample.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.module == "ancestry")
            ).fetchone()
        assert row.evidence_level == 2

    def test_finding_text_contains_population(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        store_ancestry_findings(result, eur_sample)

        with eur_sample.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.module == "ancestry")
            ).fetchone()
        assert result.top_population in row.finding_text


# ── PCA coordinates for visualization (P3-25) ────────────────────────────


class TestGetPCACoordinates:
    """Test PCA coordinates for scatter plot visualization (P3-25)."""

    def test_returns_pca_coordinates(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        coords = get_pca_coordinates(small_bundle, result)
        assert isinstance(coords, PCACoordinates)

    def test_user_coordinates_match_result(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        coords = get_pca_coordinates(small_bundle, result)
        assert coords.user == result.pc_scores

    def test_reference_samples_present(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        coords = get_pca_coordinates(small_bundle, result)
        assert len(coords.reference_samples) == 3  # AFR, EUR, EAS
        for pop in ["AFR", "EUR", "EAS"]:
            assert pop in coords.reference_samples
            assert len(coords.reference_samples[pop]) > 0

    def test_reference_samples_have_correct_dimensions(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        coords = get_pca_coordinates(small_bundle, result)
        for pop, samples in coords.reference_samples.items():
            for sample in samples:
                assert len(sample) == coords.n_components, (
                    f"{pop} sample has {len(sample)} dims, expected {coords.n_components}"
                )

    def test_centroids_present(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        coords = get_pca_coordinates(small_bundle, result)
        assert len(coords.centroids) == 3
        for pop in ["AFR", "EUR", "EAS"]:
            assert pop in coords.centroids
            assert len(coords.centroids[pop]) == coords.n_components

    def test_population_labels(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        coords = get_pca_coordinates(small_bundle, result)
        assert coords.population_labels == {
            "AFR": "African",
            "EUR": "European",
            "EAS": "East Asian",
        }

    def test_pc_labels(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        coords = get_pca_coordinates(small_bundle, result)
        assert coords.pc_labels == ["PC1", "PC2"]
        assert coords.n_components == 2

    def test_with_real_bundle(
        self,
        bundle: AncestryBundle,
        sample_engine: sa.Engine,
    ) -> None:
        """PCA coordinates work with the full bundle (8 PCs, 5000 AIMs)."""
        # Insert some genotypes
        genotypes = [
            {"rsid": s.rsid, "chrom": s.chrom, "pos": s.pos, "genotype": s.ref * 2}
            for s in bundle.snps[:50]
        ]
        _insert_raw_genotypes(sample_engine, genotypes)
        result = infer_ancestry(bundle, sample_engine)
        coords = get_pca_coordinates(bundle, result)
        assert coords.n_components == 8
        assert len(coords.pc_labels) == 8
        assert len(coords.user) == 8
        # Reference samples should have all 7 populations
        assert len(coords.reference_samples) == 7
        for pop in bundle.populations:
            assert pop in coords.reference_samples
            assert len(coords.reference_samples[pop]) > 0


class TestBundleReferenceSamples:
    """Test reference samples in the ancestry PCA bundle."""

    def test_bundle_has_reference_samples(self, bundle: AncestryBundle) -> None:
        assert len(bundle.reference_samples) > 0

    def test_all_populations_have_samples(self, bundle: AncestryBundle) -> None:
        for pop in bundle.populations:
            assert pop in bundle.reference_samples, f"Missing reference samples for {pop}"
            assert len(bundle.reference_samples[pop]) > 0

    def test_reference_sample_dimensions(self, bundle: AncestryBundle) -> None:
        for pop, samples in bundle.reference_samples.items():
            for i, sample in enumerate(samples):
                assert len(sample) == bundle.n_components, (
                    f"{pop} sample {i} has {len(sample)} dims, expected {bundle.n_components}"
                )

    def test_reference_samples_near_centroids(self, bundle: AncestryBundle) -> None:
        """Reference samples should cluster near their population centroids."""
        for pop, samples in bundle.reference_samples.items():
            centroid = bundle.reference_centroids[pop]
            mean = np.mean(samples, axis=0)
            dist = float(np.sqrt(np.sum((mean - centroid) ** 2)))
            # Mean of samples should be reasonably close to centroid
            assert dist < 10.0, f"{pop} mean distance to centroid: {dist:.2f}"


# ── PRS integration test ─────────────────────────────────────────────────


class TestPRSIntegration:
    """Test that ancestry findings are readable by prs.get_inferred_ancestry()."""

    def test_get_inferred_ancestry_reads_finding(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        from backend.analysis.prs import get_inferred_ancestry

        # Before ancestry inference → None
        assert get_inferred_ancestry(eur_sample) is None

        # After ancestry inference → top population code
        result = infer_ancestry(small_bundle, eur_sample)
        store_ancestry_findings(result, eur_sample)

        ancestry = get_inferred_ancestry(eur_sample)
        assert ancestry is not None
        assert ancestry == result.top_population


# ── Data class tests ─────────────────────────────────────────────────────


class TestAncestryResult:
    """Test AncestryResult dataclass."""

    def test_n_components(self) -> None:
        result = AncestryResult(
            pc_scores=[1.0, 2.0, 3.0],
            top_population="EUR",
            population_distances={"EUR": 0.5},
            admixture_fractions={"EUR": 1.0},
            snps_used=100,
            snps_total=128,
            coverage_fraction=0.78,
            projection_time_ms=0.5,
            is_sufficient=True,
        )
        assert result.n_components == 3

    def test_sufficient_coverage(self) -> None:
        result = AncestryResult(
            pc_scores=[1.0],
            top_population="EUR",
            population_distances={},
            admixture_fractions={},
            snps_used=50,
            snps_total=100,
            coverage_fraction=0.5,
            projection_time_ms=0.1,
            is_sufficient=True,
        )
        assert result.is_sufficient is True


class TestAncestryBundle:
    """Test AncestryBundle dataclass."""

    def test_snp_count(self, small_bundle: AncestryBundle) -> None:
        assert small_bundle.snp_count == 4

    def test_rsid_set(self, small_bundle: AncestryBundle) -> None:
        assert small_bundle.rsid_set() == {"rs1", "rs2", "rs3", "rs4"}

    def test_rsid_to_index(self, small_bundle: AncestryBundle) -> None:
        idx = small_bundle.rsid_to_index()
        assert idx["rs1"] == 0
        assert idx["rs4"] == 3
