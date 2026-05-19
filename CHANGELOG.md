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

#### Step 7 — Bundle-version 409 gate on AncestryDNA uploads + version bump

##### Added

- `POST /api/ingest` now sniffs uploads for the `#ancestrydna` header and rejects them with **HTTP 409 `bundle_version_too_old`** when the installed `database_versions['vep_bundle'].version` is below `v2.0.0` (semver compare via `packaging.version.Version`). The structured payload carries `installed_version`, `required_version`, `vendor`, `update_url`, `size_bytes`, and `checksum_sha256` — sourced from the bundle manifest with a `database_registry` fallback (Plan §5.4). 23andMe uploads are unaffected.
- New `tests/backend/test_bundle_gating.py` locks the three contract cases: AncestryDNA + v1 → 409 (payload-shape assertions), AncestryDNA + v2 → 202, 23andMe + v1 → 202.

##### Changed

- App version bumped from `0.1.0` → `0.2.0` in `pyproject.toml`, `backend/main.py::VERSION`, and `frontend/package.json` to align with the manifest `min_app_version: "0.2.0"` floor for the v2.0.0 bundle.

#### Step 8 — `annotation_state` per-sample kv table

##### Added

- New per-sample `annotation_state(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at DATETIME DEFAULT now)` table declared on `sample_metadata_obj` in `backend/db/tables.py`. Wired into the existing `create_sample_tables(engine) → sample_metadata_obj.create_all(engine, checkfirst=True)` path so every fresh sample DB materialises the table without an Alembic migration; reopening an existing DB is a no-op that preserves any rows already written (Plan §7.1).
- New `tests/backend/test_annotation_state.py` locks the schema (columns, primary key, nullability) and the lifecycle contracts: fresh-DB creation, idempotent reopen with row preservation, and `create_all(checkfirst=True)` repeat-call safety.
- `tests/backend/test_tables.py` extended to assert the table is registered on `sample_metadata_obj` (table count 13 → 14, new name in the expected set).

#### Step 9 — `AnnotationEngineResult.coverage_stats`

##### Added

- `AnnotationEngineResult` (in `backend/annotation/engine.py`) gains a `coverage_stats: dict[str, Any]` field. `run_annotation` populates it at the end of each pass with the Plan §5.6 payload: `bundle_version` (read from `database_versions['vep_bundle']`), top-level rollup (`total_variants`, `vep_bundle_rsid_hits`, `vep_bundle_coord_fallback_hits`, `vep_misses`), and a single-key `by_source` map for unmerged samples. Vendor is derived from `sample_metadata.file_format.split("_", 1)[0].lower()` (`"23andme_v5" → "23andme"`, `"ancestrydna_v2.0" → "ancestrydna"`); when no metadata row exists the key falls back to `"unknown"`. Merged-sample dispatch (three-key `S1`/`S2`/`both`) is deferred until `raw_variants.source` lands in step 63. Telemetry collection never aborts the engine: missing reference engine, missing `database_versions` row, or missing `sample_metadata` row each fall back to a safe default (Plan §5.6, §7.2).
- `vep_bundle_coord_fallback_hits` is currently always `0` — reserved for the forthcoming VEP coord-fallback lookup so downstream consumers see a stable payload shape today.
- The `annotation_engine_complete` structured log line now includes the `coverage_stats` payload so bio-validator regressions can grep hit-rate deltas directly from logs.
- `tests/backend/test_annotation_engine.py` gains `TestCoverageStatsPayload` (six cases): default empty dict, 23andMe single-key shape with full key audit, AncestryDNA single-key shape, top-level/per-source rollup consistency (`rsid + coord + misses == total_variants`), missing-bundle-version fallback to `None`, missing-file-format fallback to `"unknown"`, and the empty-sample short-circuit that leaves `coverage_stats == {}`.

#### Step 10 — Defer `annotation_state` upsert in Huey task

##### Added

- `run_annotation_task` now upserts both reserved `annotation_state` keys — `vep_bundle_version` (from `AnnotationEngineResult.coverage_stats['bundle_version']`, with a defensive `"v1.0.0"` fallback) and `annotation_bundle_coverage_json` (JSON-serialised coverage payload) — inside a single `sample_engine.begin()` transaction on the **success path** of the existing `try/except` around `run_all_analyses`. A raise from analysis bypasses the upsert via control flow, leaving `annotation_state` at its pre-run value so the staleness gate stays up and the user sees the re-annotate banner (Plan §7.3).
- New `_upsert_annotation_state(conn, key, value)` helper in `backend/tasks/huey_tasks.py` (SQLite `ON CONFLICT DO UPDATE` via `sqlalchemy.dialects.sqlite.insert`) so multiple kv writes share one transaction.
- `tests/backend/test_huey_annotation.py::TestAnnotationStateGate` (four cases): success path upserts both keys (`vep_bundle_version == "v2.0.0"`, JSON payload matches Plan §5.6 shape with single-key `by_source` and counts summing to `total_variants`); missing-`database_versions`-row falls back to `"v1.0.0"`; a `RuntimeError` raised from `run_all_analyses` leaves a pre-seeded `annotation_state` row untouched and `annotation_bundle_coverage_json` absent (gate stays up) while the job itself still marks `complete` (analysis is best-effort); and the SSE message stream emits `"Annotating…"` before `"Analyzing…"`.

##### Changed

- Two-phase SSE progress messages refreshed to match the Plan §7.3 vocabulary: the initial running message is now `"Annotating…"` (was `"Starting annotation"`) and the bridge into analysis modules is `"Analyzing…"` (was `"Running analysis modules..."`). Per-batch (`"Annotated X/Y variants"`) and per-module (`"Analyzing: <module> (i/n)"`) detail messages are unchanged.

#### Step 11 — Staleness service

##### Added

- New `backend/services/` package with `staleness.py::is_sample_stale(sample_id) -> bool` (Plan §7.4 step 3). Reads the per-sample `annotation_state.value WHERE key='vep_bundle_version'` and compares its `packaging.version.Version` major against the installed `database_versions['vep_bundle'].version` major. Minor/patch differences are not stale.
- Missing-state fallback (defensive contract): a per-sample DB without an `annotation_state` table, without a `vep_bundle_version` row, or with a value that cannot be parsed as a semver is treated as `v1.0.0`. The helper emits a structured `annotation_state_missing` warning with a `reason` field and never raises on a malformed per-sample DB. When the installed `vep_bundle` row is missing or malformed, the helper logs `vep_bundle_version_unreadable` and declines to gate.
- New `tests/backend/test_staleness.py` (10 cases): fresh sample, minor/patch-difference fresh, stale (lower sample major), missing `annotation_state` table → stale + warning against installed v2 / fresh against installed v1, missing `vep_bundle_version` row, malformed recorded version, no-raise contract on malformed per-sample DB, missing installed version → not stale + `vep_bundle_version_unreadable` warning, missing sample row → fallback with `reason="sample_row_missing"`.

#### Step 12 — `require_fresh_sample` dependency + drift guard

##### Added

- New `backend/api/dependencies.py::require_fresh_sample(sample_id)` (Plan §7.5). FastAPI dependency that calls `is_sample_stale(sample_id)` and raises `HTTPException(status_code=423, detail={...})` when stale; returns `sample_id` unchanged on fresh samples so routes can declare `Depends(require_fresh_sample)` without losing path-parameter access. The 423 `detail` payload carries the four keys mandated by Plan §7.5: `installed_version` (the sample's recorded `annotation_state.vep_bundle_version` — Plan §7.4 missing-state fallback `"v1.0.0"` applies), `required_version` (manifest's `vep_bundle.version` with `database_versions` fallback), `update_url` (manifest URL with `database_registry` fallback), and `reannotate_url` (the existing `POST /api/annotation/{sample_id}` escape hatch).
- New `tests/backend/test_stale_sample_dependency.py` with two locked contracts. **Unit behaviour** (6 cases): fresh sample returns `sample_id`, minor/patch difference passes, stale sample raises 423, payload carries the four required keys with the expected values, missing `annotation_state` table → fallback `installed_version="v1.0.0"` + 423, `required_version` falls back to `database_versions` when the manifest is unreachable. **Drift guard** (Plan §7.5): a `pytest.mark.parametrize` over every route under `backend/api/routes/*.py` that takes a `sample_id` (or alias `merged_id`) path/query parameter asserts each is classified by the gated/opt-out lists — currently 92 routes across 29 modules. Adding a new sample-scoped route later trips the test until the author declares which list it belongs to. The `samples.py` partial gating is asserted at the (method, path) subroute granularity. Two supporting invariants: module lists are disjoint, and every routes-dir module is declared in one of the two module lists or is special-cased `samples`.
- The mechanical `Depends(require_fresh_sample)` annotation across the gated route surface lands in Step 13. Step 12 ships only the dependency function, its unit tests, and the drift-guard contract.

#### Step 13 — Apply `Depends(require_fresh_sample)` to gated routes

##### Changed

- Mechanical wire-up of `Depends(require_fresh_sample)` across every sample-scoped analysis route enumerated in Plan §7.5. Fully-gated modules with only path/query `sample_id` routes (`allergy`, `annotations_api`, `findings`, `fitness`, `gene_health`, `methylation`, `nutrigenomics`, `rare_variants`, `skin`, `sleep`, `variant_detail`, `variants`) declare the dependency at the `APIRouter(...)` level so every future route in the module inherits the gate automatically. Mixed modules with a non-sample-scoped sibling route (`ancestry`'s `/lai/status`, the `*/disclaimer` routes in `apoe`/`cancer`/`cardiovascular`/`carrier`/`traits`, plus `custom_panels`, `genes`, `igv_tracks`, `liftover`, `overlays`, `pharma`, `tags`, `watches`) declare it per-route in the decorator. Body-only `sample_id` routes (`export`, `query_builder`, `reports` plus the `POST`/`PUT`/`PATCH` body handlers in `tags` and `watches`) invoke `require_fresh_sample(body.sample_id)` at the top of the handler since FastAPI cannot resolve the dependency's `sample_id` parameter from a Pydantic body without forcing a duplicate query-param requirement.
- `samples.py` stays partial-gated per Plan §7.5: the bare-metadata routes (`GET / PATCH / DELETE /api/samples/{id}`) remain ungated so users can rename / delete / inspect a stale sample. The analysis-scoped subroutes (`/merge-provenance`, `/concordance-report`, `/watched-variants/migrate-from-sources`) are not implemented in this step — they land alongside their introducing steps (68, 72) and are pre-declared in the drift guard's `_SAMPLES_GATED_PATHS` set so wiring them later requires no edit to the test.

##### Tests

- The step's gate verification is the existing drift-guard parametrization in `tests/backend/test_stale_sample_dependency.py` (Plan §7.5 enumeration check). All 101 parametrized cases pass post wire-up. Route-level HTTP 423 assertions on every gated route are scoped to step 18 (Phase 0 backend test sweep — closure) per Plan §16.6 / ADNA-00e.

#### Step 14 — Frontend `<StaleSampleGate>` + Dashboard wrap

##### Added

- New `frontend/src/components/layout/StaleSampleGate.tsx` (Plan §7.5). Probes the active sample (URL param `sample_id`) by issuing a single `GET /api/variants/count?sample_id=<id>` request — a representative sample-scoped gated route from step 13. A `423` response is parsed into the `{installed_version, required_version, update_url, reannotate_url}` payload (Plan §7.5) and rendered as a full-page banner with the canonical copy ("This sample was annotated against bundle vX; re-annotate against vY to view results."). The single CTA fires `POST` against `reannotate_url` — the existing `POST /api/annotation/{sample_id}` escape hatch carried in the 423 payload by `require_fresh_sample`. On success the staleness probe is invalidated so the gate lifts automatically once `run_annotation_task` upserts a fresh `vep_bundle_version` row (Plan §7.3). Any other probe outcome — 2xx, 4xx other than 423, network error — passes `children` through unchanged; the gate is concerned only with the staleness contract.
- `frontend/src/pages/Dashboard.tsx` wraps its active-sample layout with `<StaleSampleGate>`, blocking the status bar / annotation panel / module cards / findings preview / QC sections behind the gate when the active sample is stale.
- New `frontend/src/test/stale-sample-gate.test.tsx` (5 cases): banner renders payload-driven `installed_version` / `required_version` and the bundle-update link on 423; children render on 200; no probe fires when `sample_id` is absent from the URL; CTA POSTs to the payload's `reannotate_url` and surfaces success state; a 500 from re-annotation populates the in-banner error message without removing the gate.
