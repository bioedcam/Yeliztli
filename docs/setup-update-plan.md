# Setup Wizard ↔ Update Manager Wiring — Implementation Plan

**Status:** Drafted 2026-05-08. Awaiting go-ahead before Stage 1 begins.
**Owner:** solo dev + Claude Code agents (PRD §1.3 mapping).
**Scope:** wire the setup wizard to deliver every needed external DB/bundle (including the LAI bundle) and make the update manager keep them all current via a single manifest-driven source of truth.

---

## 1. Background & gap analysis

### What works today
- Setup wizard has 6 steps (disclaimer → import-backup → storage → credentials → databases → upload).
- `DatabasesStep.tsx` triggers parallel downloads via `POST /api/databases/download` and streams progress via SSE.
- `database_registry.py` defines all DBs with `build_mode` ∈ {`pipeline`, `download`, `manual`, `bundled`}.
- Update Manager UI exists (`Settings > UpdateManager.tsx`) with per-DB rows, history log, re-annotation prompts, "Update now", and an auto-update toggle in the UI.
- Periodic Huey task `periodic_update_check` runs daily; lifespan hook fires once on startup.
- ClinVar has full HEAD-based update detection. VEP bundle has a GitHub-commit-based check.

### Gaps the work must close
1. **`POST /api/updates/auto-update` does not exist.** Frontend `UpdateManager.tsx` calls it; the toggle silently 404s. There is also no DB persistence — `AUTO_UPDATE_DEFAULTS` is a hardcoded module dict.
2. **LAI bundle is half-wired.**
   - `required=False` and not pre-checked in the wizard, so most users miss it.
   - `sha256=None` — no integrity check on a 500 MB download.
   - Not in `check_all_updates`, not in `run_scheduled_update_check`, no `database_versions` row written after extract, no GitHub Releases/manifest version check.
3. **`encode_ccres` and `lai_bundle` (download mode) skip `database_versions`.** UpdateManager pulls from that table → DBs appear "Not installed" even when they're on disk.
4. **VEP bundle update path skips `_record_update_history` and `_record_version`.** Updates invisible in history; subsequent checks still see the old `bundle_metadata.build_date`.
5. **`ancestry_pca` (bundled) has no update mechanism.** Same problem as VEP without even the commit check.
6. **`run_scheduled_update_check` only auto-applies ClinVar + VEP.** gnomAD / dbNSFP / CPIC / GWAS / dbSNP / MONDO+HPO have no remote-version detection.
7. **No single manifest** for bundle URLs / hashes / sizes / versions. Each path is bespoke.

---

## 2. Decisions (locked in via interview)

| Topic | Decision |
| --- | --- |
| Bundle versioning | `bundles/manifest.json` in repo, fetched via raw GitHub URL |
| Manifest scope | Bundles **and** pinned upstream URLs/versions for pipeline DBs |
| Initial pipeline_pins | Extract from existing build modules |
| LAI bundle SHA-256 bootstrap | Hardcode current `959ed0fd…` in registry as fallback; manifest overrides |
| Auto-update store | New `auto_update_settings` table |
| Migration trigger | Run automatically on app startup (Alembic) |
| Backfill existing installs | If extracted bundle valid + no row → write `database_versions("lai_bundle", version="unknown-pre-manifest", …)` |
| Wizard UX | Per-DB checkboxes with running size total |
| Wizard defaults | LAI + ENCODE cCREs **on by default** (still uncheckable) |
| Setup completion gate | Required DBs only |
| Skipped-optional UX | Soft reminder "download later from Update Manager" |
| Pipeline check rollout | Ship as default, no feature flag |
| Manifest cadence | Lazy + 1h in-memory cache |
| LAI download retries | Reuse `DownloadManager` (resumable + SHA-256) |
| Bundle build_date display | `v1.1 · 2026-04-07` style (release tag + build date) |
| Bundle auto-apply | Auto-apply when toggle is on; respect `update_download_window` for ≥100 MB |
| App-update banner | Dashboard only + Update Manager row |
| App-update | Subtle indicator only — no auto-update of the app itself |
| Delivery | 3 staged PRs |

---

## 3. Stage 1 — Manifest, bundle wiring, auto-update endpoint, wizard UX (PR1)

### 3.1 Manifest

**New file:** `bundles/manifest.json`

```json
{
  "schema_version": 1,
  "generated_at": "2026-05-08T00:00:00Z",
  "bundles": {
    "lai_bundle": {
      "version": "v1.1",
      "build_date": "2026-04-07",
      "url": "https://github.com/bioedcam/GenomeInsight/releases/download/lai-bundle-v1.1.0/genomeinsight_lai_bundle_v1.1.tar.gz",
      "sha256": "959ed0fd9ebe2ad8fa542776a59ce73072d928c7ce59839ea81d0f1e78a5c18e",
      "size_bytes": 523801111
    },
    "vep_bundle":   { "version": "...", "build_date": "...", "url": "...", "sha256": "...", "size_bytes": ... },
    "ancestry_pca": { "version": "...", "build_date": "...", "url": "...", "sha256": "...", "size_bytes": ... }
  },
  "pipeline_pins": {
    "clinvar":      { "url": "...", "last_known_version": "..." },
    "gnomad":       { "url": "...", "last_known_version": "..." },
    "dbnsfp":       { "url": "...", "last_known_version": "5.3.1a" },
    "cpic":         { "url": "...", "last_known_version": "..." },
    "gwas_catalog": { "url": "...", "last_known_version": "..." },
    "dbsnp":        { "url": "...", "last_known_version": "..." },
    "mondo_hpo":    { "url": "...", "last_known_version": "..." }
  }
}
```

Pipeline-pin URLs and versions are extracted from the existing `backend/annotation/<db>.py` modules (their hardcoded constants and version literals such as `dbnsfp` `version="5.3.1a"`).

### 3.2 New backend module — `backend/db/manifest.py`

```python
@dataclass(frozen=True)
class BundleManifestEntry:
    version: str
    build_date: str
    url: str
    sha256: str
    size_bytes: int

@dataclass(frozen=True)
class PipelinePinEntry:
    url: str
    last_known_version: str

@dataclass(frozen=True)
class Manifest:
    schema_version: int
    generated_at: str
    bundles: dict[str, BundleManifestEntry]
    pipeline_pins: dict[str, PipelinePinEntry]
```

Functions:
- `fetch_manifest(timeout: float = 15.0) -> Manifest` — httpx GET against `https://raw.githubusercontent.com/bioedcam/GenomeInsight/main/bundles/manifest.json`. Lazy fetch + 1 h in-memory cache (TTL).
- `get_bundle_info(name) -> BundleManifestEntry | None`.
- `get_pipeline_pin(name) -> PipelinePinEntry | None`.
- Test override: `YELIZTLI_MANIFEST_PATH` env var (deprecated alias: `GENOMEINSIGHT_MANIFEST_PATH`) loads from local file.

### 3.3 `backend/db/database_registry.py` changes

- `lai_bundle` keeps `build_mode="download"` but routes through `DownloadManager` (already supports SHA-256 and resumable downloads).
  - `sha256 = "959ed0fd9ebe2ad8fa542776a59ce73072d928c7ce59839ea81d0f1e78a5c18e"` as hardcoded fallback. If `fetch_manifest()` succeeds, the manifest value overrides at download time.
  - `_extract_lai_bundle` calls `_record_db_version("lai_bundle", manifest_version, file_size, sha256)` after a successful extraction.
- `encode_ccres` post-build calls `_record_db_version`.
- `vep_bundle`: keep `build_mode="bundled"` for first-launch copy, but the **update path** routes through manifest (`run_vep_bundle_update` reads URL/SHA-256 from manifest instead of `db_info.url`).
- New helper `_record_db_version(engine, db_name, version, file_size, sha256)` — single insert/update path used by all flows.

### 3.4 `backend/api/routes/databases.py`

- `_run_download` and `_run_build`: write a `database_versions` row on completion. Today, only some pipeline build functions (`gnomad.py`, `dbnsfp.py`, `cpic.py`, `gwas.py`, `mondo_hpo.py`) record versions; LAI/encode_ccres do not. Standardize so every successful path writes one.
- Pre-flight: if `db_info.sha256 is None`, fetch manifest and inject before `DownloadManager.start`.
- Post-download for LAI emits an `extracting` SSE status (so AncestryView's existing handler activates).

### 3.5 Auto-update settings

**New table** in `backend/db/tables.py`:
```python
auto_update_settings = sa.Table(
    "auto_update_settings",
    metadata,
    sa.Column("db_name", sa.Text, primary_key=True),
    sa.Column("enabled", sa.Boolean, nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)
```

**Alembic migration** (`alembic/versions/<rev>_add_auto_update_settings.py`):
- Create table.
- Data migration: seed one row per `AUTO_UPDATE_DEFAULTS` entry with the existing default.
- Idempotent — safe to re-run. Runs on app startup (existing pattern).

**Backfill migration** (same revision):
- For each bundle DB with files on disk but no `database_versions` row, insert `database_versions(db_name, version="unknown-pre-manifest", downloaded_at=mtime, file_size_bytes=…)`. Avoids forcing re-download for existing users.

**`backend/db/update_manager.py`** changes:
- `get_auto_update(engine, db_name) -> bool` — falls back to `AUTO_UPDATE_DEFAULTS` if row missing (defensive).
- `set_auto_update(engine, db_name, enabled) -> None`.
- `run_scheduled_update_check` reads per-DB toggle via `get_auto_update`.

**`backend/api/routes/updates.py`**:
- Add `POST /api/updates/auto-update` taking `{db_name, enabled}` → writes via `set_auto_update`. Returns 404 for unknown DBs.
- `GET /api/updates/status` returns `auto_update` from the table, not `AUTO_UPDATE_DEFAULTS`.

### 3.6 Setup wizard — per-DB checkboxes

**`frontend/src/components/setup/DatabasesStep.tsx`**:
- New `selectedDbs: Set<string>` state. Required DBs are seeded `true` and disabled. LAI + ENCODE cCREs default to `true` (per decision). VEP/PCA bundled DBs render as "Included" (no checkbox).
- Per-DB checkbox column (left of the status icon).
- Running total at the bottom: `Total: 4.2 GB selected`.
- Single button: "Download Selected" → `triggerDownload.mutate([...selectedDbs])`. Existing API already accepts an arbitrary list.
- Setup-completion gating already considers only `required && !downloaded` — keep as-is. Optional DBs unchecked → setup still completes, with a soft post-step toast: *"You skipped LAI / ENCODE — download later from Settings > Update Manager."*

### 3.7 `database_versions` write standardization

A new `_record_db_version` helper called from:
- `clinvar.py` (already implemented; refactor to call helper)
- `gnomad.py`, `dbnsfp.py`, `cpic.py`, `gwas.py`, `mondo_hpo.py`, `dbsnp.py` (refactor)
- `_extract_lai_bundle` (new)
- `_build_encode_ccres_db` (new)
- `run_vep_bundle_update` (new — currently doesn't record)

This guarantees the Update Manager always sees a row regardless of which DB type completed.

### 3.8 Tests (PR1)

- `tests/backend/test_manifest.py` — schema validation, fetch + cache, fixture-based local override, network-failure path.
- `tests/backend/test_database_registry_lai.py` — extract path writes `database_versions`; SHA-256 mismatch is rejected.
- `tests/backend/test_updates_routes.py` — new `/auto-update` endpoint; 404 for unknown DB; persists across calls.
- `tests/backend/test_setup_wizard_databases.py` — selecting a subset, manifest-driven SHA propagation, optional-skip path.
- `tests/backend/test_alembic_backfill.py` — extracted-but-unrecorded LAI bundle gets `unknown-pre-manifest` row after migration.
- `frontend/src/test/setup-wizard.test.tsx` — new checkbox UI; running total recompute; "Download Selected" passes the chosen subset.

### 3.9 Files touched (PR1, summary)

**New:**
- `bundles/manifest.json`
- `backend/db/manifest.py`
- `alembic/versions/<rev>_add_auto_update_settings.py`
- `tests/backend/test_manifest.py`
- `tests/backend/test_alembic_backfill.py`

**Modified:**
- `backend/db/database_registry.py`
- `backend/db/update_manager.py`
- `backend/db/tables.py`
- `backend/api/routes/databases.py`
- `backend/api/routes/updates.py`
- `backend/annotation/{clinvar,gnomad,dbnsfp,cpic,gwas,mondo_hpo,dbsnp}.py` (refactor to `_record_db_version`)
- `frontend/src/components/setup/DatabasesStep.tsx`
- `frontend/src/test/setup-wizard.test.tsx`

---

## 4. Stage 2 — Pipeline DB version checks + scheduler integration (PR2)

### 4.1 Per-pipeline `check_*_update`

Each annotation module gains a `check_<db>_update(engine, settings) -> VersionInfo | None`:

| DB | Strategy |
| --- | --- |
| `clinvar` | Existing HEAD on NCBI VCF — keep as-is |
| `gnomad` | HEAD on the pinned manifest URL → release-tag compare |
| `dbnsfp` | GitHub Releases API for the dbNSFP repo OR manifest pin → version compare |
| `cpic` | CPIC GitHub Releases (pharmcat) → version date |
| `gwas_catalog` | EBI GWAS Catalog API (`/api/gwas/api/version`) or HEAD `Last-Modified` |
| `dbsnp` | NCBI dbSNP build version (rsmerge URL `Last-Modified`) |
| `mondo_hpo` | Monarch Initiative release JSON |

Each check has a defensive fallback: returns `None` on any HTTP error and logs a warning. Toggle-respecting, no feature flag.

`update_manager.py` exposes a small dispatch dict:
```python
CHECK_FNS = {
    "clinvar": check_clinvar_update,
    "vep_bundle": check_vep_bundle_update,
    "lai_bundle": check_lai_bundle_update,    # new — manifest-driven
    "ancestry_pca": check_ancestry_pca_update, # new — manifest-driven
    "gnomad": check_gnomad_update,
    "dbnsfp": check_dbnsfp_update,
    "cpic": check_cpic_update,
    "gwas_catalog": check_gwas_update,
    "dbsnp": check_dbsnp_update,
    "mondo_hpo": check_mondo_hpo_update,
}
```

### 4.2 `run_scheduled_update_check`

Loop over all DBs:
1. `enabled = get_auto_update(engine, db_name)` — skip if false.
2. `info = CHECK_FNS[db_name](engine, settings)` — skip if None.
3. `should_download_now(info.download_size_bytes, settings.update_download_window)` — defer if outside window.
4. Dispatch update:
   - Bundles → manifest-driven `run_<bundle>_update`.
   - Pipeline DBs → existing build function.

### 4.3 History recording for bundles

`run_vep_bundle_update`, new `run_lai_bundle_update`, new `run_ancestry_pca_bundle_update` each call:
- `_record_db_version(...)`
- `_record_update_history(...)`

So bundles show up in the history log identically to pipeline DBs.

### 4.4 Tests (PR2)

- `tests/backend/test_update_manager_pipelines.py` — mock httpx, each `check_*_update` returns expected `VersionInfo`. Network failure → returns None.
- `tests/backend/test_scheduled_update_check.py` — toggle off → skip; bandwidth window → defer; bundle dispatch hits manifest path.
- `tests/backend/test_update_history_bundles.py` — VEP/LAI/PCA updates each leave a history row.

---

## 5. Stage 3 — UI polish, app-update banner, scheduler hardening (PR3)

### 5.1 App-update banner

- New component `frontend/src/components/layout/AppUpdateBanner.tsx`.
- Mounted on `Dashboard.tsx` only.
- Reads `useAppUpdate()`. Subtle banner: *"Yeliztli v1.2.0 is available — view release notes."* Dismissible per-version (localStorage keyed by `lastDismissedVersion`).
- UpdateManager grows a top row "Yeliztli" showing app version + "Open release notes" link via `useAppUpdate`.

### 5.2 UpdateManager polish

- Show LAI/VEP/PCA `build_date` next to version: `v1.1 · 2026-04-07`.
- "Update now" tooltip if outside `update_download_window`: *"Outside bandwidth window (02:00–06:00). Update will run in window or click Force update."*
- New "Force update" mini-button bypasses the window (large-download safety prompt).

### 5.3 Wizard polish

- `DatabasesStep.tsx`: remove the manual VEP-build hint (now handled via download).
- Backup/restore step unchanged: reference DBs excluded by default — they're re-downloadable.

### 5.4 Tests (PR3)

- Playwright E2E: full setup wizard with LAI checked → file extracted → AncestryView shows "LAI bundle ready".
- Playwright E2E: UpdateManager "Update now" for LAI in immediate Huey mode → verify history row.
- Vitest: `AppUpdateBanner` dismissal persists per-version.

---

## 6. Risks & mitigations

| Risk | Mitigation |
| --- | --- |
| **R-08** Data-source URL changes | Manifest pins URLs and last-known-good versions. HEAD failures logged; UI shows error state. |
| **R-04** Bundle integrity / corruption | SHA-256 enforced by `DownloadManager`. Failed verification leaves prior bundle in place. |
| **R-12** Large-download UX (LAI 500 MB) | Resumable via `DownloadManager` + bandwidth window enforcement at ≥100 MB. |
| **Migration safety** | `auto_update_settings` is additive. Defaults seeded by data migration. Backfill migration writes `unknown-pre-manifest` rather than forcing re-download. |
| **Manifest unreachable** | Hardcoded SHA-256 fallback in `database_registry.py` covers the LAI bundle. Manifest override is best-effort. |
| **Pipeline check false negatives** | Each `check_*_update` returns `None` on error — no spurious "update available" banners. |

---

## 7. Definition of Done (per PRD §1.1 SOP #8)

- Backend coverage ≥ 80%, frontend ≥ 70%.
- No Ruff / ESLint errors.
- OpenAPI spec updated for `POST /api/updates/auto-update`.
- Bio-validator sign-off on bundle SHA-256s and pipeline-pin versions.
- Playwright verification: setup wizard with LAI + UpdateManager "Update now" pass on Chrome / Firefox / Safari.
- Performance: LAI download progress events ≤500 ms latency over SSE; manifest fetch < 2 s.
- Docs updated in same phase: `docs/setup-update-plan.md` (this file) + brief mention in `README.md` if user-facing.

---

## 8. Out of scope (deferred)

- Pipeline-DB diff statistics (`variants_added`, `variants_reclassified`) for non-ClinVar updates — best effort only in this batch.
- App self-update (downloading new app binaries) — PRD says "subtle indicator only".
- Plugin / extension manifest schema — separate effort.
- Manifest signing (GPG / Sigstore) — re-evaluate when a public release pipeline is established.

---

## 9. Sequencing & approval

- **PR1 (this batch):** Stage 1 — manifest, bundle wiring, auto-update endpoint + table, wizard checkboxes, version-record standardization, backfill migration.
- **PR2:** Stage 2 — pipeline `check_*_update` functions + scheduler integration.
- **PR3:** Stage 3 — app-update banner, UpdateManager polish, E2E tests.

Each PR is independently shippable; PR1 alone closes the most user-visible gap (LAI bundle setup + missing auto-update endpoint).

---

## 10. Implementation notes (logged during execution)

Findings surfaced while executing the step list that were intentionally left for later steps. Each entry: where the work lives, what to do, and which step is the natural home.

### Step 4 (2026-05-08) — `_record_db_version` helper added

- **`_record_db_version` does not write `file_path`.** The helper signature is `(engine, db_name, version, file_size_bytes, sha256=None)` per plan §3.7 / Step 4. The existing per-DB recorders in `gnomad.py`, `dbnsfp.py`, `cpic.py`, `gwas.py`, `mondo_hpo.py` currently populate the `database_versions.file_path` column.
  - **Action for Step 9:** extend the helper signature to `(engine, db_name, version, file_size_bytes, sha256=None, file_path: str | None = None)` and pass the existing per-module `file_path` value through. Update Step 4's already-merged tests to cover the new parameter. Do not switch to a follow-up `UPDATE` — single-statement upsert is the whole point of the helper.
- **`backend/db/update_manager.py:_record_version` (lines 1015–1046) is now a near-duplicate** of `_record_db_version` (only differences: no sha256, no `Engine` annotation, no `file_path`).
  - **Action for Step 9:** add `backend/db/update_manager.py` to the step's Files list. Replace the body of `_record_version` with a thin pass-through to `_record_db_version` (or delete it and update `run_clinvar_update`'s call site at line 571 directly). Verify `tests/backend/test_update_manager.py` and ClinVar update tests still pass.
