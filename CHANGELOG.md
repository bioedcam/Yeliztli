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

#### Step 15 — Setup-wizard disk-space pre-check + bundle-update affordance

##### Added

- `frontend/src/components/setup/StorageStep.tsx` now renders a per-DB size breakdown panel (Plan §12.1, ADNA-00d) under the existing "approximately 4 GB" hint, calling out gnomAD (~2 GB), dbNSFP (~1.5 GB), the union-catalog **VEP bundle (~600 MB)** for 23andMe v5 ∪ AncestryDNA v2.0 on 0.2.0+, LAI bundle (~500 MB), and the smaller reference DBs (~420 MB combined).
- `frontend/src/components/setup/UploadStep.tsx` renders the §5.4 HTTP 409 bundle-gate payload as an in-wizard amber banner with a one-click "Update VEP bundle to vX.Y.Z" CTA. The CTA fires the existing `useTriggerUpdate({ dbName: 'vep_bundle' })` hook, which polls the bundle-update job to completion via `/api/updates/job/{job_id}`; on success the banner clears and the ingest mutation resets so the user can retry the upload without reloading the wizard.
- New `BundleGateError` class + `isBundleGatePayload()` type guard in `frontend/src/api/setup.ts`. `postIngestFile()` now distinguishes 409 bundle-gate responses (re-thrown as `BundleGateError` carrying the structured payload) from other ingest failures (re-thrown as plain `Error` with the legacy `detail` string), so the existing 422 error block still renders unchanged.
- `BundleGatePayload` interface in `frontend/src/types/setup.ts` mirroring the Plan §5.4 wire shape (`error`, `installed_version`, `required_version`, `vendor`, `update_url`, `size_bytes`, `checksum_sha256`).
- New `frontend/src/test/setup-storage-step.test.tsx` (3 cases): retains the 4 GB headline summary, renders the per-DB breakdown panel, and explicitly asserts the ~600 MB VEP bundle callout names the AncestryDNA v2.0 union catalog and the `0.2.0+` floor.
- New `frontend/src/test/setup-upload-step.test.tsx` (3 cases): 409 → bundle-gate banner rendered with installed + required versions and computed MB size; CTA fires `POST /api/updates/trigger` with `db_name: "vep_bundle"`, polls job status, and clears the banner on completion; 422 ingest errors still surface in the original error block and never trigger the banner.

##### Changed

- `backend/db/database_registry.py::DATABASES["vep_bundle"].expected_size_bytes` bumped from `12_000_000` (~12 MB) to `600_000_000` (~600 MB) to reflect the union 23andMe v5 ∪ AncestryDNA v2.0 catalog that ships on `bundle-v2.0.0`. The `description` field gains the union-catalog suffix.

#### Step 16 — Backup/restore bundle-version gate

##### Added

- `backend/api/routes/setup.py::import_backup` now runs a Plan §7.6 pre-flight bundle-version check on every uploaded backup. Each archived per-sample DB is extracted to an isolated `tempfile.TemporaryDirectory` (no writes to `data_dir` yet) and its recorded `annotation_state.vep_bundle_version` is read. A missing `annotation_state` table — i.e. a pre-Phase-0 backup — falls back to `v1.0.0` per Plan §7.6. The lowest backup version is compared against `database_versions['vep_bundle'].version`; any major-version mismatch in either direction halts the restore with HTTP 409 and a structured payload (`{error: "bundle_version_mismatch", installed_version, backup_version, direction, sample_member}`). Fresh installs without a recorded bundle skip the comparison.
- Post-restore, every restored per-sample DB receives the idempotent three-step Plan §7.6 upgrade: `_add_missing_columns(engine, from_version=_get_schema_version(engine))` → `sample_metadata_obj.create_all(engine, checkfirst=True)` → `INSERT OR IGNORE INTO annotation_state (key, value) VALUES ('vep_bundle_version', 'v1.0.0')`. Anticipates migration 008's per-sample backfill semantics so freshly-restored pre-Phase-0 backups land with the same `annotation_state` shape every new sample DB ships. Corrupt or non-SQLite blobs (legacy test fixtures) are logged as `restore_sample_upgrade_skipped` and otherwise tolerated.
- New `frontend/src/components/setup/RestoreStep.tsx` — accessible (`role="alert"`, `aria-live="polite"`) bundle-version-mismatch banner. Renders backup vs. installed versions with direction-specific guidance ("Downgrade the installed VEP bundle…" / "Upgrade the installed VEP bundle…"), a "Choose a different backup" retry CTA, and a Back affordance.
- New `BundleVersionMismatchError` class + `isBundleVersionMismatchPayload()` guard in `frontend/src/api/setup.ts`. `postImportBackup()` now distinguishes 409 mismatch responses (re-thrown as `BundleVersionMismatchError` carrying the structured payload) from other import failures. `ImportBackupStep.tsx` routes the typed error through `<RestoreStep>` so the upload UI is swapped for the banner without leaking extraction-stage state.
- `BundleVersionMismatchPayload` interface in `frontend/src/types/setup.ts` mirroring the Plan §7.6 wire shape.
- New `tests/backend/test_restore_bundle_version_gate.py` (7 cases): explicit-v1 backup vs. installed-v2 mismatch + `data_dir`-untouched invariant; opposite-direction (`backup_above_installed`); match-success runs the three-step upgrade and preserves the existing `annotation_state` row; pre-Phase-0 backup against installed `v1.0.0` succeeds and backfills `vep_bundle_version='v1.0.0'`; pre-Phase-0 backup against installed `v2.0.0` blocks (fallback major comparison); fresh install (no installed bundle row) allows any backup; repeat-restore is idempotent (no duplicate `annotation_state` rows).
- New `frontend/src/test/setup-restore-step.test.tsx` (6 cases): banner renders both versions and the below-installed headline; opposite-direction flips headline + guidance copy; banner exposes `role="alert"` + `aria-live="polite"`; retry / back buttons fire the right callbacks; `ImportBackupStep` swaps to the banner on a 409 mismatch response; non-mismatch 409 (e.g. "backup export already in progress") falls back to the generic error path and never renders the banner.

#### Step 17 — Alembic migration `008_annotation_state_backfill.py`

##### Added

- New `alembic/versions/008_annotation_state_backfill.py` (Plan §7.4 step 2, §17.1). Per-sample backfill only — no reference-DB schema change. Walks every row in `samples`, resolves `db_path` against the bind's data directory when relative, opens each per-sample SQLite, and runs `CREATE TABLE IF NOT EXISTS annotation_state (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)` + `INSERT OR IGNORE INTO annotation_state (key, value) VALUES ('vep_bundle_version', 'v1.0.0')`. `INSERT OR IGNORE` preserves any row a freshly re-annotated sample already wrote (Plan §7.4's idempotency contract). Missing / non-SQLite / corrupt per-sample DBs are tolerated: each emits a structured `alembic_008_sample_db_skipped` warning with a `reason` field (`"missing"`, `"sqlalchemy_error"`, `"empty_db_path"`) and the migration moves on — a single bad sample DB never blocks the reference-DB schema bump. `downgrade()` is a deliberate no-op: per-sample provenance rows reflect real state and the staleness service relies on them.
- Extended `tests/backend/test_alembic_backfill.py` with `TestMigration008AnnotationStateBackfill` (9 cases) + `TestMigration008Helpers` (5 cases): table-creation + `v1.0.0` seed on a fresh sample DB; idempotent re-run preserves a pre-existing `v2.0.0` row; missing / corrupt / empty-`db_path` sample rows each emit the structured warning without raising; relative `samples.db_path` resolves against `data_dir`; empty `samples` table is a clean no-op; reference-DB schema is byte-identical before and after upgrade; downgrade-then-inspect confirms per-sample rows are intentionally left in place. Helper tests cover `_data_dir_from_bind` (memory-DB → `None`) and `_resolve_sample_db_path` (absolute pass-through, relative join, `None` `data_dir` fallback). Uses `structlog.testing.capture_logs` for structured-event assertions, matching the project's convention from `tests/backend/test_staleness.py`.

#### Step 22a — LAI runner: merged + backward-compat tests + retire `scripts/lai_runner.py`

##### Added

- New `tests/backend/test_lai_runner_merged_sample.py` (7 cases) locks the three-key uppercase `S1` / `S2` / `both` dispatch contract for merged samples (Plan §6.6). Builds an inline merged-sample payload with hits in each source bucket plus an autosomal off-bundle drop in `S1` and `S2`; asserts the resulting telemetry has exactly the three uppercase keys, per-bucket hit/drop counts (`S1: 2/1`, `S2: 2/1`, `both: 2/0`), per-source counts sum to matched/dropped totals, and no `""` / lowercase leakage. Two parametrize blocks lock the source-driven dispatch override: any non-empty `source` collapses to three-key regardless of `file_format` (`23andme_v5`, `ancestrydna_v2.0`, `merged_v1`, `""`), and a `merged_v1` file_format with only empty-source rows still emits three keys with zero counts for the missing buckets.
- New `tests/backend/test_lai_runner_backward_compat.py` (12 parametrized cases) locks the pre-Phase-3 single-key derivation. Builds an in-memory sample DB on the current schema (no `raw_variants.source` column — Phase-3 step 63 will add it) stamped with each shipped vendor, then asserts (a) `_read_sample_genotypes` defaults every genotype's `source=""`, (b) the accumulator only populates the empty-source bucket (no `S1` / `S2` / `both` leakage), and (c) `_build_coverage_telemetry` collapses to single-key `{<vendor>: {hits, drops}}`. The `LAIRunner._build_coverage_telemetry` parametrize block locks the exact `file_format.split("_", 1)[0].lower()` derivation across `23andme_v3..v5`, `ancestrydna_v2.0`, and case-insensitive `ANCESTRYDNA_v2.0` / `23andMe_v5`.
- New `tests/backend/test_scripts_lai_runner_removed.py` (3 cases) locks the deletion invariants: (1) `scripts/lai_runner.py` is absent from the working tree, (2) `git grep` of the deletion-PR pattern across `backend/`, `tests/`, `scripts/`, `frontend/` returns zero hits (excludes the test file itself via `:(exclude)` pathspec), (3) a stdlib `rglob` walk catches untracked references the `git grep` pre-deletion guard would miss.

##### Removed

- `scripts/lai_runner.py` — superseded by `backend/analysis/lai_runner.py` (the in-repo runner that already replaces the bcftools/bgzip/tabix subprocess flow with pysam). Pre-deletion verification: `git grep -nE "scripts/lai_runner|from scripts.lai_runner|import scripts.lai_runner"` across `backend/`, `tests/`, `scripts/`, `frontend/` returned zero hits after retiring the lone docstring reference in `backend/analysis/lai_runner.py`. The legacy file was untracked, so deletion was a bare `rm` (Plan §6.6).

##### Changed

- `backend/analysis/lai_runner.py` module docstring rewritten to drop the "Replaces ``scripts/lai_runner.py``" pointer so the step-22a removal-invariant grep stays clean across `backend/`.

##### Notes

- Risk-register touch points (Plan §1.3): **R-15a** (LAI bundle coverage) — locks the merged-sample telemetry contract that step 22's implementation made available; lets step 24's frontend surface render the three-row source-breakdown table without ambiguity about the key casing or dispatch ordering.

#### Step 22 — LAI runner per-source dropout telemetry — implementation + core tests

##### Added

- `LAIRunner.run()` now accepts a `file_format` kwarg (`"23andme_v5"`, `"ancestrydna_v2.0"`, `"merged_v1"`, …) and threads per-source hit/drop counts through `_filter_genotypes` + `_write_per_chrom_vcfs`. On completion the runner emits a structured `lai_coverage_telemetry` log line (Plan §6.6) with `total_variants`, `filtered`, `mapped`, `dropped`, `drop_rate`, `drop_rate_warning` (true when drop_rate > 0.15), `file_format`, and the per-source payload. The payload collapses to single-key `{<vendor>: {hits, drops}}` when every genotype's `source=""` (vendor derived from `file_format.split("_", 1)[0].lower()`, falling back to `"unknown"` when file_format is absent), and to three-key `{S1, S2, both}` when any non-empty source is present OR `file_format == "merged_v1"` — dispatch is source-driven per Plan §6.6.
- `LAIRunnerResult` gained a `coverage_telemetry: dict[str, dict[str, int]]` field (mirrored into `metadata.coverage_telemetry`) so step 24 can surface "X of Y rsIDs mapped (Z% dropout)" + a per-source breakdown in `AncestryView`. `metadata` also gained `drop_rate` and `drop_rate_warning` for the frontend banner trigger.
- `backend/analysis/lai.py` gained two helpers — `_read_sample_file_format(engine)` reads the per-sample `sample_metadata.file_format` (returns `""` on pre-metadata DBs), and `_read_sample_genotypes(engine)` reflects `raw_variants` columns at call time and only selects `source` when the Phase-3 v8 schema has added it (pre-v8 sample DBs collapse to `source=""`, preserving the existing 23andMe behavior). `run_lai_analysis` uses both and passes `file_format` into `runner.run()`.
- New `tests/backend/test_lai_runner_telemetry_parity.py` (8 cases) locks the byte-identical 23andMe write surface (filter→write call args are identical between a baseline input with no `source` key and an input where every genotype carries `source=""`) and the single-key telemetry shape (`23andme_v5` / `23andme_v4` → `{"23andme": {hits, drops}}`; missing file_format → `"unknown"` vendor; empty per-source dict still emits a zero-payload single-key entry).
- New `tests/backend/test_lai_runner_ancestrydna.py` (6 cases) builds an in-memory sample DB stamped with `file_format="ancestrydna_v2.0"` and exercises the read path + filter + accumulator. Asserts non-zero post-filter variant count, all retained contigs are autosomal (`chr1..chr22`), `_read_sample_genotypes` defaults `source=""` on pre-Phase-3 DBs, only the empty-source bucket exists (no `S1`/`S2`/`both` leakage on unmerged), and `_build_coverage_telemetry` collapses to single-key `{"ancestrydna": {hits, drops}}`. The full curated `sample_ancestrydna_v2.txt` fixture lands in step 34; this step uses an inline payload to keep step 22 self-contained per the per-step PR convention. Soft-gate (`degraded_coverage`) cases land in step 23.

##### Notes

- Risk-register touch points (Plan §1.3): **R-15a** (LAI bundle coverage) — this step is the in-app telemetry half of the R-15a mitigation; the bundle rebuild itself shipped in step 21, the soft-staleness gate ships in step 23, and the frontend surface ships in step 24.

#### Step 21 — Cut `lai-bundle-v2.0.0` release + manifest update

##### Changed

- `bundles/manifest.json` `lai_bundle` entry bumped to `version: "v2.0.0"` with the new GitHub Release asset URL (`https://github.com/bioedcam/GenomeInsight/releases/download/lai-bundle-v2.0.0/genomeinsight_lai_bundle_v2.0.0.tar.gz`), placeholder `sha256` (64 zeros — overwritten by the runbook §10 publish step once the cluster build produces the tarball), `size_bytes: 750_000_000` (~750 MB upper-bound from Plan §6.4 phase 3 union catalog estimate, ~700–750 MB), `min_app_version: "0.2.0"` (matches the app-version floor introduced in step 7), and `build_date: "2026-05-20"`. The manifest's `version` is the authoritative semver consulted by the soft-staleness gate (Plan §6.7) and the update flow; the bundle's internal `metadata.json::bundle_version` stays informational.
- `backend/db/database_registry.py::DATABASES["lai_bundle"]` `expected_size_bytes` bumped from `523_801_111` (~500 MB v1.1) to `750_000_000` (~750 MB v2.0.0) and `url` repointed to the `lai-bundle-v2.0.0` asset URL. The hardcoded `sha256="959ed0fd…"` v1.1 fallback stays in place per the step's explicit scope — the SHA-256 is updated in a follow-on release commit once the v2.0.0 tarball's actual hash is known. Until then, the manifest's runtime-fetched value overrides the registry default at download time (per `backend/api/routes/databases.py` injection — CHANGELOG PR1).
- Normalized the prior `"v1.1"` baseline to `"v1.1.0"` in `tests/backend/test_manifest.py` (SAMPLE_PAYLOAD + V2_PAYLOAD fixtures and their five accompanying assertions, including the `test_local_override_not_cached` swap fixture which now flips `v1.1.0 → v1.2.0`) for clean `packaging.version.Version` compares (per runbook §10). The on-disk LAI v1.1 tarball SHA stays pinned at `959ed0fd…` as a Step 7 invariant; only the version string is normalized.
- `tests/backend/test_lai_bundle_registry.py` `test_lai_bundle_metadata` now asserts the bumped `expected_size_bytes == 750_000_000`; `test_lai_bundle_url_set` is tightened from a `"lai-bundle" in db.url` substring check to a full-URL equality assertion (locks the v2.0.0 asset URL).

##### Added

- New `TestLAIBundleManifestV2` test class in `tests/backend/test_lai_bundle_registry.py` (Plan §12.2 LAI-00e item v): writes a v2.0.0 manifest fixture to a temp file, points `GENOMEINSIGHT_MANIFEST_PATH` at it, and locks two contracts on `fetch_manifest()` — (i) `lai_bundle.version == "v2.0.0"` alongside `min_app_version == "0.2.0"`, `size_bytes == 750_000_000`, and the asset URL ending `/lai-bundle-v2.0.0/genomeinsight_lai_bundle_v2.0.0.tar.gz`; (ii) the placeholder `sha256` (64 zeros) round-trips through the parser cleanly. Module-level `_clear_manifest_cache` fixture ensures each test sees a fresh in-memory manifest cache.

##### Notes

- The actual `genomeinsight_lai_bundle_v2.0.0.tar.gz` cluster build, bio-validator sign-off (≥0.88 mean per-window LAI accuracy, ≤0.0566 phasing switch error per Plan §6.4), GitHub Release cut, and SHA-256 + `size_bytes` finalization run **out-of-repo** per `docs/lai-bundle-release-runbook.md` §§5–9. Step 21 ships only the in-repo manifest + registry rewire that points the app at the (eventual) v2.0.0 asset; the runbook §10 publish step overwrites the placeholder SHA with the real `.sha256` sidecar value and the `size_bytes` with the actual tarball stat.
- The `lai-bundle-v1.1.0` GitHub tag (normalized from the historical `v1.1`) remains alive indefinitely per Plan §2.1's immutable-tag policy so older app versions (`v0.1.x`) keep downloading the v1.1 bundle on the additive-only manifest schema.

#### Step 20 — Port cluster build scripts to `scripts/lai_bundle_v2/`

##### Added

- New `scripts/lai_bundle_v2/` package porting the v1.1 cluster build pipeline into the repo so PR-0c can produce `genomeinsight_lai_bundle_v2.0.0.tar.gz` against the union 23andMe v5 ∪ AncestryDNA v2.0 site list (Plan §6.2, §6.4, LAI-00). Phase scripts (`01_download_panel.sh`, `02_prepare_sites.sh`, `03_subset_panel.sh`, `04_admixture_filter.sh`, `05_train_gnomix.sh`, `06_validate.sh`, `07_assemble_bundle.sh`) plus the Python helpers (`04c_filter_single_ancestry.py`, `06a_identify_trios.py`, `06b_mendelian_phasing.py`, `06d_phasing_accuracy.py`, `06e_lai_accuracy.py`, `07_write_metadata.py`) reproduce the v1.1 pipeline against the larger union catalog (~840k autosomal sites vs. ~605k for v1.1 — Plan §6.4 phases 2 + 3). All paths are parametrized via `env.sh` (WORKDIR, UNION_CATALOG_TSV, BEAGLE_JAR, GNOMIX_DIR_INSTALL, ADMIXTURE_SEED=42 locked per Plan §6.3 step 4) so the v1.1 working directory `/exports/people/mondragonlab/ecc1695/lai_bundle/` stays read-only reference and the v2.0.0 rebuild lives under `~/lai_bundle_v2/`.
- New `scripts/lai_bundle_v2/run_rebuild.sh` orchestrator driving the seven phases in order against the union site list. Each phase is independently re-runnable (skips outputs already present) and can be invoked solo by passing the phase number — `bash scripts/run_rebuild.sh 05` re-trains Gnomix without re-running Phases 1–4.
- New `docs/lai-bundle-release-runbook.md` documenting the cluster rebuild workflow per Plan §6.2 / §6.3 / §6.5: rsync of in-repo scripts onto `ssh two:~/lai_bundle_v2/scripts/`, conda env lock, source-data provenance, tool versions (bcftools / Beagle JAR sha256 / Gnomix git sha / fastmixture seed), bio-validator accuracy targets (≥0.88 mean per-window LAI accuracy, ≤0.0566 phasing switch error), release-cut sequence, manifest update with `min_app_version: 0.2.0`, soft-staleness post-publish behaviour (Plan §6.7), post-release smoke test, and rollback flow.
- `metadata.json` provenance writer (`07_write_metadata.py`) emits the Plan §6.5 schema (`bundle_version`, `build_date`, `build_host`, `git_commit`, `source_sites_sha256`, `tool_versions`, `admixture_seed`, `reference_panel`, `site_count`, `window_count`, `accuracy_per_window_mean`, `phasing_switch_error`) — these values are mirrored into the GitHub Release notes so consumers can audit provenance without untarring the bundle.
- New `tests/backend/test_lai_bundle_v2_scripts.py` (100 parametrized cases) locking the in-repo scripts package as a publishable artifact: phase-script presence + executable bit, orchestrator phase order (`ALL_PHASES=(01 02 03 04 05 06 07)` + per-phase dispatch table), every phase script sources `env.sh`, no script hardcodes the v1.1 cluster path or `~/lai_bundle` (v1) working dir, `env.sh` defaults (`WORKDIR=$HOME/lai_bundle_v2`, `LAI_BUNDLE_VERSION=v2.0.0`, `ADMIXTURE_SEED=42`, `UNION_CATALOG_TSV` required), `bash -n` syntax check on every shell script, `py_compile` on every Python helper, and runbook contract (rsync flow, both v1.1 + v2.0.0 path callouts, accuracy targets, orchestrator invocation).

##### Notes

- Cluster execution itself is out of scope for Step 20 — the actual rebuild against the union site list runs in Step 21 (LAI-00a) once the union catalog from the VEP rebuild is in place. Step 20 ships only the scripts + runbook so the rebuild has somewhere to run from.

#### Step 19 — Phase 0 frontend test sweep

##### Added

- Extended `frontend/src/test/setup-storage-step.test.tsx` with two coverage-fill cases (Plan §12.1, ADNA-00e): Continue button drives `useSetStoragePath` and advances via `onNext()` on a non-blocked result (covers `handleConfirm` mutateAsync success path); toggling the Custom location radio reveals the custom path input, accepts free-text entry, and disables Continue while the trimmed path is empty (covers the `useCustomPath && !customPath.trim()` branch and the conditional custom-path input render).
- Extended `frontend/src/test/setup-upload-step.test.tsx` with three coverage-fill cases (Plan §12.1, ADNA-00e): `/api/ingest` 200 response renders the parsed-summary success state with formatted variant count, no-call count, and `file_format` chip plus the "Go to Dashboard" CTA (covers the `ingestMutation.isSuccess` branch); the file picker rejects unsupported extensions (`.zip`) before any network call and shows the format-hint error (covers the `isValidFile === false` branch in `handleFileSelect`); the drop zone's keyboard handler activates the file picker on Enter and Space and ignores other keys (covers the `onKeyDown` branch).

##### Notes

- All four Phase 0 frontend test files green (22 cases total): `stale-sample-gate.test.tsx` (5), `setup-upload-step.test.tsx` (6), `setup-storage-step.test.tsx` (5), `setup-restore-step.test.tsx` (6). v8 line coverage on Phase 0 components: `StaleSampleGate.tsx` 100%, `RestoreStep.tsx` 100%, `StorageStep.tsx` 93.75%, `UploadStep.tsx` 76.36% — all comfortably above the ≥70% bar required by Plan §12.1.
