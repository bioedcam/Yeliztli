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

## Setup Wizard ↔ Update Manager Wiring

### PR1 — Manifest, bundle wiring, auto-update endpoint, wizard UX

#### Added

- `bundles/manifest.json` — single manifest pinning bundle versions, SHA-256s, and sizes for `lai_bundle`, `vep_bundle`, `ancestry_pca`, plus upstream URLs and last-known versions for every pipeline DB (`clinvar`, `gnomad`, `dbnsfp`, `cpic`, `gwas_catalog`, `dbsnp`, `mondo_hpo`).
- `backend/db/manifest.py` — frozen dataclasses (`Manifest`, `BundleManifestEntry`, `PipelinePinEntry`) plus `fetch_manifest()` with a 1 h in-memory TTL cache, network-failure fallback to last-good, and a `GENOMEINSIGHT_MANIFEST_PATH` env var override for tests.
- `_record_db_version(engine, db_name, version, file_size_bytes, sha256=None, file_path=None)` — single helper in `backend/db/database_registry.py` that upserts into `database_versions` for every successful build/download path (LAI extraction, ENCODE cCREs build, ClinVar/gnomAD/dbNSFP/CPIC/GWAS/MONDO+HPO/dbSNP).
- LAI extraction now records its row using the manifest's version/SHA-256 (falls back to `unknown-pre-manifest` when the manifest is unreachable).
- `auto_update_settings` table (`db_name PK`, `enabled`, `updated_at`) in `backend/db/tables.py`.
- Alembic migration `007_add_auto_update_settings` — idempotent table creation, seeds `AUTO_UPDATE_DEFAULTS`, and backfills `database_versions("unknown-pre-manifest")` for `lai_bundle`/`encode_ccres` installs that pre-date version tracking.
- `get_auto_update` / `set_auto_update` helpers in `backend/db/update_manager.py`, with fallback to `AUTO_UPDATE_DEFAULTS` for missing rows.
- `POST /api/updates/auto-update` endpoint persisting per-DB toggles (404s for unknown DBs); `GET /api/updates/status` now reads `auto_update` from the table.
- Setup wizard per-DB checkboxes with a running "Total: X.X GB selected" total. Required DBs are checked-and-disabled; `lai_bundle` and `encode_ccres` default checked; `bundled` DBs render as "Included" (no checkbox). The button is now "Download Selected".
- Sonner toast on Continue lists any optional DBs the user skipped, with a pointer to Settings > Update Manager.

#### Changed

- `lai_bundle` `DatabaseInfo` now carries `sha256="959ed0fd…"` as a hardcoded integrity fallback; manifest override applies at download time.
- `backend/api/routes/databases.py` injects manifest URL/SHA-256/size when `db_info.sha256 is None` (download mode); falls back to registry defaults when the manifest is unreachable.
- Per-module `record_*_version` helpers in `clinvar/gnomad/dbnsfp/cpic/gwas/mondo_hpo/dbsnp.py` now delegate to `_record_db_version` (behavior preserved, `file_path` plumbed through).
- `update_manager._record_version` is now a thin pass-through to `_record_db_version` to remove the near-duplicate.

### PR2 — Pipeline DB version checks + scheduler integration

#### Added

- `CHECK_FNS` dispatch dict in `backend/db/update_manager.py` covering every DB.
- Manifest-driven `check_lai_bundle_update` and `check_ancestry_pca_update`.
- Per-pipeline checkers: `check_gnomad_update`, `check_dbnsfp_update`, `check_cpic_update`, `check_gwas_update`, `check_dbsnp_update`, `check_mondo_hpo_update`. Each returns `None` on network error (no spurious banners) and compares the manifest's pinned URL/version against `database_versions`.
- `run_lai_bundle_update` and `run_ancestry_pca_bundle_update` — manifest-driven `DownloadManager` flows that record both `database_versions` and `update_history`.

#### Changed

- `run_vep_bundle_update` now also writes `_record_db_version` + `_record_update_history`, so VEP updates show up in the history log.
- `check_all_updates` refactored to loop `CHECK_FNS` rather than hardcoded ClinVar/VEP branches.
- `run_scheduled_update_check` dispatches across all DBs: respects `get_auto_update`, defers when outside `update_download_window` for ≥100 MB, and routes bundles vs. pipelines correctly.

### PR3 — UI polish, app-update banner, Playwright E2E

#### Added

- `AppUpdateBanner` component on the Dashboard, reading `useAppUpdate()`. Dismissible per-version via `localStorage["appUpdateDismissed"]`.
- UpdateManager top-row "GenomeInsight" entry showing app version and a release-notes link via `useAppUpdate`.
- Bundle rows in UpdateManager now display `v1.1 · 2026-04-07`-style build dates from `version_display`.
- "Update now" outside-window tooltip plus a "Force update" mini-button with a confirm dialog (backend `POST /api/updates/trigger` accepts `force=true`).
- Playwright E2E: `tests/e2e/setup-wizard-lai.spec.ts` exercises the wizard with the LAI checkbox; `tests/e2e/update-manager-lai.spec.ts` clicks "Update now" on a preloaded `unknown-pre-manifest` LAI and asserts new `database_versions` + `update_history` rows.

#### Changed

- Removed the obsolete "Run scripts/build_vep_bundle.py" hint from `DatabasesStep.tsx` — VEP updates now flow through the manifest.

## AncestryDNA Integration

### Phase 0 — Foundations

#### Step 6 — `update_manager` writes manifest semver for VEP bundle

##### Changed

- `run_vep_bundle_update` now records the manifest's `version` (semver — e.g. `"v2.0.0"`) in `database_versions['vep_bundle'].version` and `update_history` instead of the bundle's `bundle_metadata.build_date`. The build date is still displayed alongside the version (Plan §5.5).
- When the downloaded SQLite carries a `bundle_metadata.bundle_version` that disagrees with the manifest, a structured warning `vep_bundle_metadata_version_mismatch` is logged (with `manifest_version`, `metadata_bundle_version`, `build_date`); the update never fails on this mismatch because the manifest is the authoritative contract. Pre-v2.0.0 bundles that omit `bundle_version` are tolerated silently.
