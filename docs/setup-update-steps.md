# Setup Wizard ↔ Update Manager — Step List

Sequential, prompt-sized steps for executing `docs/setup-update-plan.md`. Each step is self-contained: read the files in **Read first**, do the work in **Do**, verify with **Tests**, do not touch files in **Out of scope**.

> **How to use:** invoke each step with the template prompt at the bottom of this file (or in `docs/setup-update-prompt.md`). Run steps in order. Do not skip the verification block. Confirm completion before advancing.

---

## Stage 1 — PR1: Manifest, bundle wiring, auto-update endpoint, wizard UX

### Step 1 — Create `bundles/manifest.json` skeleton with bundle entries
- **Files:** `bundles/manifest.json` (new).
- **Read first:** `docs/setup-update-plan.md` §3.1; `backend/db/database_registry.py` (DATABASES dict); `genomeinsight_lai_bundle_v1.1.tar.gz` (size: 523_801_111).
- **Do:**
  - Create `bundles/manifest.json` with `schema_version: 1`, `generated_at: 2026-05-08T00:00:00Z`.
  - Add bundle entries for `lai_bundle`, `vep_bundle`, `ancestry_pca`. Use the live LAI values: version `v1.1`, build_date `2026-04-07`, sha256 `959ed0fd9ebe2ad8fa542776a59ce73072d928c7ce59839ea81d0f1e78a5c18e`, size 523_801_111, url already in the registry.
  - For `vep_bundle` and `ancestry_pca`: compute sha256 + size from the on-disk files in `bundles/` and use the existing `db_info.url` value.
  - Stub `pipeline_pins: {}` (Step 2 fills it).
- **Tests:** `python -c "import json; json.load(open('bundles/manifest.json'))"` succeeds.
- **Out of scope:** any backend/frontend code.

### Step 2 — Populate `pipeline_pins` in the manifest
- **Files:** `bundles/manifest.json`.
- **Read first:** every `backend/annotation/<db>.py` matching `clinvar gnomad dbnsfp cpic gwas dbsnp mondo_hpo`. Pull URL constants (e.g. `CLINVAR_VCF_URL`) and current version literals (e.g. `version="5.3.1a"` in dbnsfp).
- **Do:** add `pipeline_pins.{clinvar,gnomad,dbnsfp,cpic,gwas_catalog,dbsnp,mondo_hpo}` with `{url, last_known_version}` extracted from those modules.
- **Tests:** every key has a non-empty `url`. JSON still parses.
- **Out of scope:** changing the annotation modules.

### Step 3 — New module `backend/db/manifest.py`
- **Files:** `backend/db/manifest.py` (new); `tests/backend/test_manifest.py` (new).
- **Read first:** `bundles/manifest.json`; `backend/utils/update_checker.py` for the existing httpx pattern.
- **Do:**
  - Dataclasses `BundleManifestEntry`, `PipelinePinEntry`, `Manifest` (frozen).
  - `fetch_manifest(timeout: float = 15.0) -> Manifest` with 1 h in-memory TTL cache. Honors `YELIZTLI_MANIFEST_PATH` env var (deprecated alias: `GENOMEINSIGHT_MANIFEST_PATH`) for tests.
  - `get_bundle_info(name) -> BundleManifestEntry | None`, `get_pipeline_pin(name) -> PipelinePinEntry | None`.
  - Manifest URL constant: `https://raw.githubusercontent.com/bioedcam/GenomeInsight/main/bundles/manifest.json`.
- **Tests (`tests/backend/test_manifest.py`):** local-file override path; cache TTL respected; network failure returns last good or raises a typed error (decide and assert).
- **Out of scope:** any consumer code; just the module + tests.

### Step 4 — Add `_record_db_version` helper to `backend/db/database_registry.py`
- **Files:** `backend/db/database_registry.py`.
- **Read first:** `backend/db/update_manager.py` `_record_version`; existing per-DB version recorders in `backend/annotation/{gnomad,dbnsfp,cpic,gwas,mondo_hpo}.py`.
- **Do:** add a single helper `_record_db_version(engine, db_name, version, file_size_bytes, sha256=None)` that upserts into `database_versions`. Export it from `database_registry.py`.
- **Tests:** unit test in `tests/backend/test_database_registry.py` (extend if exists, create if not) — insert + update path.
- **Out of scope:** call sites (next steps wire them in).

### Step 5 — Wire LAI extraction to record version
- **Files:** `backend/db/database_registry.py`.
- **Read first:** `_extract_lai_bundle`; `backend/db/manifest.py` (Step 3).
- **Do:** after successful extraction in `_extract_lai_bundle`, fetch manifest entry; on success use `version`/`sha256`; on failure use `version="unknown-pre-manifest"`. Call `_record_db_version("lai_bundle", version, dest_dir_size, sha256)`.
- **Tests:** `tests/backend/test_database_registry_lai.py` — extract a tiny fixture tarball; assert row is written.
- **Out of scope:** download path itself.

### Step 6 — Wire `encode_ccres` to record version
- **Files:** `backend/db/database_registry.py`.
- **Do:** in `_build_encode_ccres_db`, call `_record_db_version("encode_ccres", version=now_yyyymmdd, file_size, sha256=None)` after the SQLite is built.
- **Tests:** extend `test_database_registry.py` — fixture BED in, row out.
- **Out of scope:** other DBs.

### Step 7 — Hardcode LAI sha256 fallback in `DATABASES`
- **Files:** `backend/db/database_registry.py`.
- **Do:** set `sha256 = "959ed0fd9ebe2ad8fa542776a59ce73072d928c7ce59839ea81d0f1e78a5c18e"` on the `lai_bundle` `DatabaseInfo`.
- **Tests:** existing tests still pass.
- **Out of scope:** changing other DB entries.

### Step 8 — Manifest-driven sha256 injection in `databases.py`
- **Files:** `backend/api/routes/databases.py`.
- **Read first:** `_run_download` and `_run_build`; `backend/db/manifest.py`.
- **Do:** in the dispatch path, if `db_info.sha256 is None` (and `db_info.build_mode == "download"`), call `manifest.get_bundle_info(name)` and override the URL/sha256/size before `DownloadManager.start`. Manifest unreachable → keep registry defaults.
- **Tests:** add to `tests/backend/test_setup_wizard_databases.py` — manifest-overrides path with monkeypatched fetch.
- **Out of scope:** changing the SSE protocol.

### Step 9 — Standardize version recording across pipeline build modules
- **Files:** `backend/annotation/{clinvar,gnomad,dbnsfp,cpic,gwas,mondo_hpo,dbsnp}.py`; `backend/db/database_registry.py`; `backend/db/update_manager.py`; `tests/backend/test_database_registry.py`.
- **Read first:** the existing `record_*_version` helpers in each module; `_record_db_version` in `backend/db/database_registry.py` (added in Step 4); `_record_version` in `backend/db/update_manager.py` (lines 1015–1046); `setup-update-plan.md` §10 Step 4 notes.
- **Do:**
  - **Extend `_record_db_version`** to `(engine, db_name, version, file_size_bytes, sha256=None, file_path: str | None = None)` so the existing per-module `file_path` values keep landing in the `database_versions.file_path` column. Add a test case in `test_database_registry.py` for the new parameter.
  - **Replace each module's inline upsert** (`record_clinvar_version`, `record_gnomad_version`, `record_dbnsfp_version`, `record_cpic_version`, `record_gwas_version`, `record_mondo_hpo_version`, `record_dbsnp_version`) with a call to `_record_db_version`. Keep the per-module wrapper functions (other call sites import them) — just have their bodies delegate. Behavior must be identical.
  - **Retire `update_manager._record_version`**: replace its body with a thin pass-through to `_record_db_version`, or delete it and update `run_clinvar_update`'s call site (line 571) directly. Pick whichever leaves fewer dangling imports.
- **Tests:** existing per-module tests must still pass; new `_record_db_version` `file_path` test passes; `tests/backend/test_update_manager.py` and ClinVar update tests still pass.
- **Out of scope:** any logic change beyond the recording call site.

### Step 10 — Add `auto_update_settings` table to `backend/db/tables.py`
- **Files:** `backend/db/tables.py`.
- **Do:** define `auto_update_settings` table with `db_name TEXT PK`, `enabled BOOLEAN NOT NULL`, `updated_at DATETIME NOT NULL`.
- **Tests:** `tests/backend/test_tables.py` — column types.
- **Out of scope:** Alembic, business logic.

### Step 11 — Alembic migration: create table + seed defaults + backfill bundle versions
- **Files:** `alembic/versions/<rev>_add_auto_update_settings.py` (new).
- **Read first:** existing migrations under `alembic/versions/`; `AUTO_UPDATE_DEFAULTS` in `backend/db/update_manager.py`.
- **Do:**
  - `upgrade()`: create `auto_update_settings`; insert one row per `AUTO_UPDATE_DEFAULTS` key with current default. Idempotent (skip if rows exist).
  - Backfill: for `lai_bundle` (extracted dir at `data_dir/lai_bundle`) and `encode_ccres` (file at `data_dir/encode_ccres.db`), if file/dir present and no `database_versions` row, insert `version="unknown-pre-manifest"`.
  - `downgrade()`: drop the table; do **not** roll back the backfill.
- **Tests:** `tests/backend/test_alembic_backfill.py` — apply migration on a temp DB seeded with an LAI dir but no version row → row appears.
- **Out of scope:** scheduler / endpoint changes.

### Step 12 — `get_auto_update` / `set_auto_update` in `backend/db/update_manager.py`
- **Files:** `backend/db/update_manager.py`.
- **Do:** add `get_auto_update(engine, db_name) -> bool` (falls back to `AUTO_UPDATE_DEFAULTS` if row missing) and `set_auto_update(engine, db_name, enabled)`. Update `run_scheduled_update_check` to read via `get_auto_update`.
- **Tests:** `tests/backend/test_update_manager.py` — round-trip; missing-row fallback.
- **Out of scope:** the routes layer (Step 13).

### Step 13 — `POST /api/updates/auto-update` endpoint
- **Files:** `backend/api/routes/updates.py`; `frontend/src/api/updates.ts` (verify call site matches).
- **Read first:** the existing call in `frontend/src/api/updates.ts:148-158`.
- **Do:**
  - Add `POST /api/updates/auto-update` body `{db_name, enabled}` → `set_auto_update`. 404 for unknown DBs (validate against `DATABASES` keys + `AUTO_UPDATE_DEFAULTS`).
  - Update `GET /api/updates/status` to read `auto_update` per-DB from the table.
- **Tests:** `tests/backend/test_updates_routes.py` — POST persists; 404 path; GET reflects new value.
- **Out of scope:** frontend changes.

### Step 14 — `DatabasesStep.tsx`: per-DB checkboxes + running total
- **Files:** `frontend/src/components/setup/DatabasesStep.tsx`.
- **Read first:** existing `handleStartDownload`, `allRequiredDownloaded` logic; `frontend/src/api/setup.ts` to confirm `triggerDownload.mutate(names: string[])` signature.
- **Do:**
  - `selectedDbs: Set<string>` state. Required DBs seeded `true` and disabled. `lai_bundle` + `encode_ccres` default `true`. `bundled` DBs render "Included" without a checkbox.
  - Replace "Download All" with "Download Selected" passing `[...selectedDbs]`.
  - Running total at the bottom: `Total: X.X GB selected` recomputes on toggle.
- **Tests:** extend `frontend/src/test/setup-wizard.test.tsx` — toggle changes total; "Download Selected" fires with the chosen subset.
- **Out of scope:** post-download skip-reminder toast (Step 15).

### Step 15 — Soft reminder when optional DBs are skipped
- **Files:** `frontend/src/components/setup/DatabasesStep.tsx`; `frontend/src/pages/SetupWizard.tsx` if needed.
- **Do:** when the user clicks Continue with optional DBs unchecked, show a Sonner toast: *"You skipped LAI / ENCODE — download later from Settings > Update Manager."* Toast lists the actual unchecked names.
- **Tests:** vitest — toast appears with correct names; absent when all selected.
- **Out of scope:** scheduler changes.

### Step 16 — Stage 1 backend test sweep
- **Files:** `tests/backend/test_manifest.py`, `test_database_registry_lai.py`, `test_updates_routes.py`, `test_alembic_backfill.py` (any of these may already exist from earlier steps — fill remaining cases).
- **Do:** run `conda activate GI && pytest tests/backend/ -k "manifest or database_registry or updates_routes or alembic_backfill" -v`. Add cases until each step's claim is covered. Keep coverage ≥80% on touched files.
- **Tests:** suite passes.
- **Out of scope:** new behavior.

### Step 17 — Stage 1 frontend test sweep
- **Files:** `frontend/src/test/setup-wizard.test.tsx`.
- **Do:** run `cd frontend && npm test -- setup-wizard`. Add cases for checkbox toggle, total recompute, "Download Selected" subset, skip toast.
- **Tests:** vitest passes.
- **Out of scope:** new behavior.

---

## Stage 2 — PR2: Pipeline DB version checks + scheduler integration

### Step 18 — Dispatch dict + manifest-driven bundle checks
- **Files:** `backend/db/update_manager.py`.
- **Do:**
  - Add `CHECK_FNS = {...}` mapping each db_name to its check function.
  - Implement `check_lai_bundle_update(engine, settings)` and `check_ancestry_pca_update(engine, settings)` reading from manifest and comparing against `database_versions`.
- **Tests:** `tests/backend/test_update_manager_bundles.py` — manifest version newer → returns VersionInfo; same → returns None.
- **Out of scope:** pipeline checks (Steps 19–25).

### Step 19 — `check_gnomad_update`
- **Files:** `backend/annotation/gnomad.py`; `backend/db/update_manager.py` (register in `CHECK_FNS`).
- **Do:** HEAD or release-API call → compare against `database_versions.gnomad`. Manifest pin is the authoritative URL.
- **Tests:** mock httpx; older→VersionInfo; newer→None; network-error→None.
- **Out of scope:** running the update.

### Step 20 — `check_dbnsfp_update`
- Same pattern as Step 19, against the dbNSFP source pinned in manifest.

### Step 21 — `check_cpic_update`
- Same pattern, CPIC release date.

### Step 22 — `check_gwas_update`
- Same pattern, EBI GWAS Catalog API.

### Step 23 — `check_dbsnp_update`
- Same pattern, NCBI rsmerge `Last-Modified`.

### Step 24 — `check_mondo_hpo_update`
- Same pattern, Monarch Initiative release JSON.

### Step 25 — Refactor `check_all_updates` to dispatch via `CHECK_FNS`
- **Files:** `backend/db/update_manager.py`.
- **Do:** loop all keys in `CHECK_FNS`, call each, collect results into `UpdateCheckResult`. Drop the hardcoded ClinVar/VEP-only branches.
- **Tests:** mocked CHECK_FNS — every DB visited; aggregated correctly.
- **Out of scope:** scheduler.

### Step 26 — Bundle update functions for LAI, PCA, refactor VEP
- **Files:** `backend/db/update_manager.py`.
- **Do:**
  - Add `run_lai_bundle_update(settings)` and `run_ancestry_pca_bundle_update(settings)` — both use `DownloadManager` + manifest sha256 + `_record_db_version` + `_record_update_history`.
  - Refactor `run_vep_bundle_update` to also call `_record_db_version` + `_record_update_history`.
- **Tests:** `tests/backend/test_update_history_bundles.py` — each path leaves `database_versions` and `update_history` rows.
- **Out of scope:** scheduler dispatch (Step 27).

### Step 27 — `run_scheduled_update_check` dispatches all DBs
- **Files:** `backend/db/update_manager.py`.
- **Do:** loop over CHECK_FNS keys; honor `get_auto_update`; honor `should_download_now`; dispatch to `run_<bundle>_update` or `run_<pipeline>_update` (build_fn). Pipeline build paths reuse the existing `huey_tasks.run_database_update_task` plumbing — verify ordering/transactions.
- **Tests:** `tests/backend/test_scheduled_update_check.py` — toggle off → skip; window → defer; bundle dispatch hits manifest path.
- **Out of scope:** UI.

### Step 28 — Stage 2 test sweep
- **Files:** all Stage 2 test files.
- **Do:** `pytest tests/backend/ -k "update_manager or update_history_bundles or scheduled_update_check" -v`. Patch network calls deterministically.
- **Tests:** suite passes; coverage ≥80% on touched files.
- **Out of scope:** new code.

---

## Stage 3 — PR3: UI polish, app-update banner, Playwright

### Step 29 — `AppUpdateBanner` component on Dashboard
- **Files:** `frontend/src/components/layout/AppUpdateBanner.tsx` (new); `frontend/src/pages/Dashboard.tsx`.
- **Read first:** `frontend/src/api/updates.ts` `useAppUpdate`.
- **Do:** subtle banner reading `useAppUpdate`. Dismissible per-version via `localStorage["appUpdateDismissed"]`. Mount on Dashboard only.
- **Tests:** vitest — banner renders when update available; dismissal persists per-version; absent when up-to-date.
- **Out of scope:** auto-download.

### Step 30 — UpdateManager polish
- **Files:** `frontend/src/components/settings/UpdateManager.tsx`; `frontend/src/api/updates.ts`.
- **Do:**
  - Show `build_date` next to bundle versions: `v1.1 · 2026-04-07`. Pull from `version_display`.
  - Add an app-version row at the top using `useAppUpdate`.
  - "Update now" tooltip if outside `update_download_window`. Add a "Force update" mini-button that bypasses the window with a confirm dialog.
- **Tests:** vitest — build_date rendered; outside-window tooltip; force update calls trigger with `force=true` (extend backend `POST /api/updates/trigger` if needed; otherwise local-only force).
- **Out of scope:** scheduler.

### Step 31 — Remove obsolete manual-VEP-build hint
- **Files:** `frontend/src/components/setup/DatabasesStep.tsx`.
- **Do:** delete the "Run scripts/build_vep_bundle.py" hint block now that VEP updates flow through manifest.
- **Tests:** snapshot/test removed; nothing else regresses.
- **Out of scope:** other copy.

### Step 32 — Playwright E2E: setup wizard with LAI bundle
- **Files:** `tests/e2e/setup-wizard-lai.spec.ts` (new) or extend the existing wizard spec.
- **Do:** spin up the app in `YELIZTLI_HUEY_IMMEDIATE=true`. Step through the wizard with LAI checked. Use a fixture manifest pointing to a tiny tarball served from `tests/fixtures/`. Assert AncestryView shows "LAI bundle ready".
- **Tests:** runs on Chromium / Firefox / WebKit per `playwright.config.ts`.
- **Out of scope:** UpdateManager E2E (Step 33).

### Step 33 — Playwright E2E: UpdateManager "Update now" for LAI
- **Files:** `tests/e2e/update-manager-lai.spec.ts` (new).
- **Do:** preload an LAI bundle with `version="unknown-pre-manifest"`. Click "Update now". Assert history row appears + new version row.
- **Tests:** all browsers green.
- **Out of scope:** other DBs.

### Step 34 — Stage 3 vitest sweep
- **Files:** any new vitest files.
- **Do:** `cd frontend && npm test`. Cover `AppUpdateBanner`, UpdateManager polish.
- **Tests:** suite passes; coverage ≥70% on touched files.
- **Out of scope:** unrelated polish.

### Step 35 — Final verification + CHANGELOG
- **Files:** `CHANGELOG.md`; `README.md` if user-facing changes need a mention.
- **Do:**
  - Append CHANGELOG entries for PR1/PR2/PR3.
  - Run full backend + frontend + Playwright suites locally.
  - Run `ruff check`, `ruff format --check`, `eslint`.
  - Confirm no regressions in existing tests.
- **Tests:** all green.
- **Out of scope:** new features.

---

## Template prompt (use verbatim, replace `<N>`)

```
Implement step <N> from docs/setup-update-steps.md.

Context to load before any edits:
- docs/setup-update-plan.md  ← architectural decisions and rationale
- docs/setup-update-steps.md ← the step list; do only step <N>

Rules:
1. Read both files in full first. Re-read the step's "Read first" entries before editing.
2. Stay strictly within the step's "Files" list. Do not modify "Out of scope" files.
3. Follow the project's standing operating procedures (see CLAUDE memory: PRD §1.1 SOPs — Context7 mandatory for any library API; named subagents only; risk register check; tests ≥80% backend / ≥70% frontend on touched files).
4. Conda env: run `conda activate GI` before any pytest/python invocation.
5. Run the step's "Tests" block. If tests fail, fix the underlying issue (no skipping, no --no-verify).
6. Stop after the step is verified. Report:
   - Files changed (with line counts)
   - Tests added + passing
   - Anything from the step you intentionally deferred and why
   - Anything outside the step you noticed but did not change

Do not advance to step <N+1>. Wait for explicit go-ahead.
```
