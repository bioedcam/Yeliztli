#!/usr/bin/env python3
"""Regenerate the mini ancestry test fixture NPZ.

Creates a small PCA bundle (~100 AIMs, 50 ref samples, 3 populations)
for fast unit testing without loading the full 5,000-AIM production bundle.

Usage:
    python scripts/regenerate_ancestry_fixtures.py

Output:
    tests/fixtures/ancestry_test_fixture.npz
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

OUTPUT_PATH = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "ancestry_test_fixture.npz"
)

# Parameters
N_AIMS = 100
N_PCS = 4
POPULATIONS = np.array(["AFR", "EUR", "EAS"])
N_POPS = len(POPULATIONS)

# Reference samples per population
N_REF_PER_POP = 17  # 51 total (close to 50)
N_REF = N_REF_PER_POP * N_POPS

rng = np.random.default_rng(42)

# Generate synthetic PCA loadings
loadings = rng.standard_normal((N_AIMS, N_PCS)).astype(np.float64) * 0.1

# Generate means and stds (plausible allele freq stats)
means = rng.uniform(0.1, 1.8, N_AIMS).astype(np.float64)
stds = rng.uniform(0.3, 0.9, N_AIMS).astype(np.float64)

# Generate well-separated population centroids
population_centroids = np.array(
    [
        [5.0, 0.0, 0.0, 0.0],  # AFR
        [0.0, 5.0, 0.0, 0.0],  # EUR
        [-5.0, -5.0, 0.0, 0.0],  # EAS
    ],
    dtype=np.float64,
)

# Generate reference samples clustered around centroids
ref_pca_coords = np.zeros((N_REF, N_PCS), dtype=np.float64)
ref_labels = np.empty(N_REF, dtype="U10")
ref_sample_ids = np.empty(N_REF, dtype="U20")

for i, pop in enumerate(POPULATIONS):
    start = i * N_REF_PER_POP
    end = start + N_REF_PER_POP
    noise = rng.standard_normal((N_REF_PER_POP, N_PCS)) * 0.5
    ref_pca_coords[start:end] = population_centroids[i] + noise
    ref_labels[start:end] = pop
    for j in range(N_REF_PER_POP):
        ref_sample_ids[start + j] = f"{pop}_{j:03d}"

# AIM metadata
aim_rsids = np.array([f"1:{1000 + i}:A:G" for i in range(N_AIMS)])
aim_rsids_23andme = np.array([f"rs{10000 + i}" for i in range(N_AIMS)])
aim_chroms = np.array(["1"] * N_AIMS)
aim_positions_grch38 = np.arange(1000, 1000 + N_AIMS, dtype=np.int64)
aim_a1 = np.array(["G"] * N_AIMS)  # alt
aim_a2 = np.array(["A"] * N_AIMS)  # ref

# Eigenvalues (decreasing)
eigenvalues = np.array([100.0, 50.0, 20.0, 10.0], dtype=np.float64)

# Tracy-Widom p-values (first 4 significant, rest not)
tw_pvalues = np.concatenate(
    [
        np.array([1e-20, 1e-15, 1e-8, 1e-4]),
        np.ones(16) * 0.5,
    ]
).astype(np.float64)

np.savez_compressed(
    OUTPUT_PATH,
    loadings=loadings,
    means=means,
    stds=stds,
    ref_pca_coords=ref_pca_coords,
    ref_labels=ref_labels,
    ref_sample_ids=ref_sample_ids,
    population_centroids=population_centroids,
    populations=POPULATIONS,
    aim_rsids=aim_rsids,
    aim_rsids_23andme=aim_rsids_23andme,
    aim_chroms=aim_chroms,
    aim_positions_grch38=aim_positions_grch38,
    aim_a1=aim_a1,
    aim_a2=aim_a2,
    eigenvalues=eigenvalues,
    n_significant_pcs=np.int64(N_PCS),
    tw_pvalues=tw_pvalues,
    n_total_snps=np.int64(10000),
    n_selected_aims=np.int64(N_AIMS),
)

print(f"Written {OUTPUT_PATH} ({OUTPUT_PATH.stat().st_size:,} bytes)")
print(f"  AIMs: {N_AIMS}, PCs: {N_PCS}, Pops: {list(POPULATIONS)}, Ref samples: {N_REF}")
