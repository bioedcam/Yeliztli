"""Tests for NPZ PCA bundle loading and validation (Step 1.3).

Validates the committed ancestry_pca_bundle.npz file loads correctly
with all expected arrays, shapes, and contents for the 5,000-AIM,
8-PC, 7-population ancestry inference system.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from backend.analysis.ancestry import POPULATIONS, load_ancestry_bundle

BUNDLE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "backend"
    / "data"
    / "panels"
    / "ancestry_pca_bundle.npz"
)


@pytest.fixture()
def npz():
    """Raw NPZ data for low-level array validation."""
    return np.load(BUNDLE_PATH, allow_pickle=False)


@pytest.fixture()
def bundle():
    """Loaded AncestryBundle from the NPZ file."""
    return load_ancestry_bundle(BUNDLE_PATH)


# ── Array presence and shape validation ──────────────────────────────


class TestNPZArrays:
    """Validate all expected arrays are present with correct shapes."""

    EXPECTED_KEYS = {
        "loadings",
        "means",
        "stds",
        "ref_pca_coords",
        "ref_labels",
        "ref_sample_ids",
        "population_centroids",
        "populations",
        "aim_rsids",
        "aim_chroms",
        "aim_positions_grch38",
        "aim_a1",
        "aim_a2",
        "eigenvalues",
        "n_significant_pcs",
        "tw_pvalues",
        "n_total_snps",
        "n_selected_aims",
        "aim_rsids_23andme",
    }

    def test_all_expected_keys_present(self, npz) -> None:
        assert set(npz.keys()) == self.EXPECTED_KEYS

    def test_loadings_shape(self, npz) -> None:
        assert npz["loadings"].shape == (5000, 8)

    def test_means_shape(self, npz) -> None:
        assert npz["means"].shape == (5000,)

    def test_stds_shape(self, npz) -> None:
        assert npz["stds"].shape == (5000,)

    def test_ref_pca_coords_shape(self, npz) -> None:
        assert npz["ref_pca_coords"].shape == (3419, 8)

    def test_ref_labels_shape(self, npz) -> None:
        assert npz["ref_labels"].shape == (3419,)

    def test_population_centroids_shape(self, npz) -> None:
        assert npz["population_centroids"].shape == (7, 8)

    def test_populations_shape(self, npz) -> None:
        assert npz["populations"].shape == (7,)

    def test_eigenvalues_shape(self, npz) -> None:
        assert npz["eigenvalues"].shape == (8,)

    def test_tw_pvalues_shape(self, npz) -> None:
        assert npz["tw_pvalues"].shape == (20,)

    def test_aim_rsids_23andme_shape(self, npz) -> None:
        assert npz["aim_rsids_23andme"].shape == (5000,)


# ── Population validation ────────────────────────────────────────────


class TestPopulations:
    """Validate 7-population setup."""

    def test_seven_populations(self, bundle) -> None:
        assert len(bundle.populations) == 7

    def test_canonical_population_order(self, bundle) -> None:
        assert bundle.populations == ["AFR", "AMR", "CSA", "EAS", "EUR", "MID", "OCE"]

    def test_matches_module_constant(self, bundle) -> None:
        assert tuple(bundle.populations) == POPULATIONS

    def test_all_populations_have_centroids(self, bundle) -> None:
        for pop in bundle.populations:
            assert pop in bundle.reference_centroids

    def test_all_populations_have_reference_samples(self, bundle) -> None:
        for pop in bundle.populations:
            assert pop in bundle.reference_samples
            assert len(bundle.reference_samples[pop]) > 0

    def test_all_populations_have_labels(self, bundle) -> None:
        for pop in bundle.populations:
            assert pop in bundle.population_labels
            assert len(bundle.population_labels[pop]) > 0


# ── AIM rsID validation ──────────────────────────────────────────────


class TestAIMRsIDs:
    """Validate aim_rsids_23andme array for genotype matching."""

    def test_5000_entries(self, bundle) -> None:
        assert bundle.snp_count == 5000

    def test_all_valid_rsids(self, bundle) -> None:
        """All rsIDs should be standard rs-prefixed or 23andMe i-prefixed."""
        for snp in bundle.snps:
            assert snp.rsid.startswith(("rs", "i")), f"Invalid rsID: {snp.rsid}"

    def test_no_empty_rsids(self, bundle) -> None:
        for snp in bundle.snps:
            assert snp.rsid != "", "Empty rsID found"
            assert len(snp.rsid) > 2, f"Too-short rsID: {snp.rsid}"

    def test_majority_are_standard_rsids(self, bundle) -> None:
        """Most rsIDs should be standard rs-prefixed (not 23andMe internal)."""
        rs_count = sum(1 for snp in bundle.snps if snp.rsid.startswith("rs"))
        assert rs_count > 4900, f"Only {rs_count}/5000 standard rsIDs"

    def test_rsids_are_unique(self, bundle) -> None:
        rsids = [snp.rsid for snp in bundle.snps]
        assert len(rsids) == len(set(rsids)), "Duplicate rsIDs found"


# ── Tracy-Widom and eigenvalue validation ────────────────────────────


class TestStatisticalArrays:
    """Validate pre-computed statistical arrays."""

    def test_n_significant_pcs_is_8(self, bundle) -> None:
        assert bundle.n_significant_pcs == 8

    def test_tw_pvalues_has_20_entries(self, bundle) -> None:
        assert len(bundle.tw_pvalues) == 20

    def test_eigenvalues_positive(self, bundle) -> None:
        assert all(e > 0 for e in bundle.eigenvalues)

    def test_eigenvalues_descending(self, bundle) -> None:
        for i in range(len(bundle.eigenvalues) - 1):
            assert bundle.eigenvalues[i] >= bundle.eigenvalues[i + 1]

    def test_n_total_snps(self, bundle) -> None:
        # 548,818 SNPs in the gnomAD HGDP+1KG reference panel before AIM selection
        assert bundle.n_total_snps == 548818

    def test_n_selected_aims(self, bundle) -> None:
        # Top 5,000 AIMs selected by Rosenberg's In + Fst ranking
        assert bundle.n_selected_aims == 5000


# ── Reference panel validation ───────────────────────────────────────


class TestReferencePanel:
    """Validate reference panel data."""

    def test_ref_sample_count(self, npz) -> None:
        assert npz["ref_pca_coords"].shape[0] == 3419

    def test_ref_labels_all_valid(self, npz) -> None:
        valid_pops = {"AFR", "AMR", "CSA", "EAS", "EUR", "MID", "OCE"}
        labels = set(npz["ref_labels"])
        assert labels == valid_pops

    def test_stds_no_zeros(self, bundle) -> None:
        """All stds should be positive (no zero-variance AIMs)."""
        assert np.all(bundle.stds > 0), "Zero-variance AIMs found"

    def test_means_in_range(self, bundle) -> None:
        """Mean dosages should be in [0, 2]."""
        assert np.all(bundle.means >= 0)
        assert np.all(bundle.means <= 2)
