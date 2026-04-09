# Changelog

All notable changes to GenomeInsight will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **Ancestry Module v2 (AMv2):** Two-tier ancestry analysis system replacing the 128-AIM IDW approach.
  - **Tier 1 (Instant):** 5,000-AIM PCA projection with NNLS + kNN admixture estimation. Runs in < 1 second.
  - **Tier 2 (Deep Analysis):** Local ancestry inference via re-exported Gnomix models with Beagle phasing. Chromosome-level ancestry painting. Runs in ~15-30 minutes. Optional.
- 7 superpopulations (AFR, AMR, CSA, EAS, EUR, MID, OCE) — up from 6 (SAS renamed to CSA, MID added).
- NPZ-based PCA bundle (414 KB) with 5,000 AIMs, 8 significant PCs, and rsID matching for 23andMe compatibility.
- NNLS admixture with bootstrap 95% confidence intervals (100 iterations).
- kNN secondary admixture estimate with cosine-similarity confidence scoring.
- LAI bundle (~500 MB) hosted on GitHub Releases with resumable download and SHA-256 verification.
- Gnomix inference engine (`gnomix_inference.py`) — pure numpy + XGBoost, no sklearn/pandas dependency.
- LAI runner with pysam-based VCF handling (replaces bcftools/bgzip/tabix subprocess calls).
- Chromosome painting visualization using react-konva (Canvas) with hover tooltips and population legend.
- LAI-derived global ancestry pie chart alongside Tier 1 bar chart.
- Tier 1 vs Tier 2 concordance comparison section.
- Per-population MID accuracy warning when proportion < 15%.
- PCA scatter plot with PC selector dropdown (PC1 vs PC2, PC1 vs PC3, etc.).
- Analysis Details collapsible section with AIM count, PCs used, and method description.
- PRS ancestry mismatch integration: admixture-aware threshold warns when top ancestry < 70%.
- LAI API endpoints: status, trigger, results, and SSE progress.
- LAI results table in sample DB with findings integration.
- Huey task for background LAI processing with job progress tracking.
- Java runtime detection for LAI bundle requirements.
- Setup wizard LAI bundle checkbox (optional, default unchecked).
- "Download LAI Bundle" button on Ancestry page for post-setup download.
- LAI progress UI with per-chromosome phasing and inference status.
- `docs/ANCESTRY_MODULE.md` — methods, validation, limitations, and citations.

### Changed

- Ancestry engine rewritten from IDW-based to NNLS + kNN admixture estimation.
- PCA bundle migrated from JSON to NPZ format (128 AIMs to 5,000 AIMs).
- `get_inferred_ancestry()` consolidated into `ancestry.py` (removed duplicate from `prs.py`).
- `get_inferred_ancestry()` preference order: `local_ancestry` > `nnls_admixture` > `pca_projection`.
- Admixture bar chart updated for 7 populations with percentage labels and confidence badges.
- `POPULATIONS` constant updated from 6 to 7 populations across backend.

### Dependencies

- Added `scipy` (NNLS admixture via `scipy.optimize.nnls`).
- Added `pysam` (VCF read/write, replaces bcftools/bgzip/tabix subprocess calls).
- Added `xgboost` (Gnomix smoother step, loads native-format boosters).
- Added `react-konva` and `konva` (chromosome painting Canvas visualization).
