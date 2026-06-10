# Phase-F Annotation Validation — Remaining Plan

Continuation plan for the annotation-validation campaign described in
[`annotation-validation-strategy.md`](annotation-validation-strategy.md). It
covers the findings still open after the analysis-logic remediation merged so
far, with concrete code surfaces and a per-finding execution plan.

## Status

**Merged** (genotype-aware live engine + M1–M8 suite, then the Phase-F
follow-ups): F1–F37 carriage remediation (#320), F20 (#324), F38 (#327),
F19 (#329), **F22** (#332), **F23** (#342), **F24/F25** (#345), **F12** (#346),
**F15** (#349), **F30** (#364), **F34/F35** (#369), **G1** (#373).

Per-sample schema is at **v10** (`v9` = `deleterious_total_assessed` (F25),
`v10` = `gnomad_af_popmax` (F15)). Repo is `bioedcam/Yeliztli`.

**Remaining: none — this plan is complete.** F30, F34, F35 (build / provenance
group) and G1 (re-annotation trigger) are all merged; "Phase-B" as originally
scoped was subsumed by F30 (see below). The per-finding sections below are
retained as the as-built record.

---

## F30 — Genome-build manifest + cross-source provenance  *(Medium, merged #364)*

**Problem.** `database_versions` (reference.db) has **no genome-build column**
and there is no cross-source consistency check, so a stored finding cannot be
tied to the ClinVar/gnomAD/dbNSFP snapshot that produced it. Per-sample
provenance (`annotation_state`) records **only** `vep_bundle_version`. Separately,
`dbsnp_merges` is frozen at b151/2018 while ClinVar is 2026 (merges b152–b156
unresolvable) — out of scope here beyond recording the build/version.

**Code surfaces.**
- `backend/db/tables.py` — `database_versions` table (PK `db_name`; columns
  `version`, `file_path`, `file_size_bytes`, `downloaded_at`, `checksum_sha256`).
- `backend/db/database_registry.py::_record_db_version` — the single upsert
  helper every source funnels through.
- Per-source wrappers that call it: `clinvar.record_clinvar_version`,
  `gnomad.record_gnomad_version`, `dbnsfp.record_dbnsfp_version`,
  `gwas.record_gwas_version`, `cpic.record_cpic_version`,
  `gnomad_constraint.record_constraint_version`.
- Alembic migrations live in `alembic/versions/` (latest:
  `010_gnomad_gene_constraint.py`).

**Plan.**
1. Alembic `011_database_versions_genome_build.py`: `ADD COLUMN genome_build TEXT`
   to `database_versions`; add the column to the `tables.py` definition.
2. Add a `genome_build` parameter to `_record_db_version` (default `None`) and
   thread it through each `record_*_version` wrapper with the source's build:
   - **GRCh37**: clinvar, gnomad (r2.1.1), gwas, cpic, vep_bundle.
   - **GRCh38**: **dbNSFP 5.x** — this makes F35's coordinate mismatch explicit
     in the manifest (see F35).
3. Add a consistency helper, e.g.
   `database_registry.check_genome_build_consistency(reference_engine) -> list[str]`,
   returning sources whose recorded build differs from the pipeline build
   (GRCh37). Hook it at annotation start (engine) and/or registry init and
   **log a warning** — do **not** hard-fail: the dominant rsid path is
   build-agnostic and dbNSFP is *legitimately* GRCh38. The check exists to make
   an unexpected skew (e.g. a GRCh38 gnomAD bundle) visible.
4. *(Stretch)* Persist the per-source versions into per-sample provenance
   (`annotation_state`) at annotation time so a stored finding is reproducible
   against its exact snapshot set. Can be a follow-up.

**Tests.** Migration adds the column; `_record_db_version` stores `genome_build`;
each `record_*_version` records the right build (dbNSFP → GRCh38); the
consistency helper flags a planted mismatch and is clean on the expected set.

**Cross-cutting** (reference-DB Alembic + 6 callers) → run the full backend
sweep, and the Alembic/reference-schema tests (`test_alembic_backfill.py`,
`test_schema.py`).

---

## F34 — Mitochondrial liftover guard  *(Low, merged #369)*

**Problem.** `backend/ingestion/liftover.py::convert_coordinate` maps `MT`→`chrM`
(line ~88) and lifts via the UCSC hg19→hg38 chain. But UCSC hg19 `chrM` is the
old Yoruba sequence, **not rCRS** (which the chip data uses), so MT liftover
yields wrong GRCh38 coordinates (`263→None`, `750→748`, …). Autosomes are
correct. Currently dead (0 % of `chrom_grch38` populated; opt-in endpoint), so
this is a latent landmine, not an active bug.

**Plan.** In `convert_coordinate`, short-circuit `MT`/`chrM`: return
`(None, None)` (no GRCh38 coordinate) with a comment citing rCRS ≠ UCSC-hg19-chrM,
rather than emitting a bogus lifted position. Co-land a test asserting an `MT`
input yields `(None, None)` while an autosomal input still lifts correctly.

---

## F35 — dbNSFP GRCh38-coordinate provenance / guard  *(Low, merged #369)*

**Problem.** The dbNSFP DB is **GRCh38-coordinate** (`rs1801133` at 11,796,321)
while the pipeline is GRCh37 (11,856,378). Harmless today because the dbNSFP
**position fallback is dead** (F32) and the live path joins by rsid — but a
landmine once ref/alt-bearing inputs (VCF/WGS) start exercising the
position-join path (`dbnsfp.lookup_dbnsfp_by_positions`).

**Plan.** Two parts, small:
1. Record dbNSFP's build as GRCh38 in the manifest — delivered by **F30** step 2.
2. Guard the cross-build position join: either disable/guard
   `lookup_dbnsfp_by_positions` against GRCh37 coordinates (raise/skip with a
   clear message) or document the GRCh37-rsid-only contract at the call site.
   Co-land a test that the position path is not silently used cross-build.

> **Suggested grouping:** F34 + F35 are both small, latent, build-assembly
> guards — bundle them into a single "build-assembly guards" PR after F30 (which
> establishes the build manifest they reference).

---

## Phase-B — schema additions *(subsumed)*

Originally scoped as the per-sample columns `deleterious_total_assessed` and
`database_versions.genome_build`. The per-sample columns are **already done**
(`deleterious_total_assessed` via F25 = schema v9; `gnomad_af_popmax` via F15 =
schema v10). The only remaining item — `database_versions.genome_build` — is
delivered by **F30**. So there is no separate Phase-B PR; it collapses into F30.

---

## G1 — Re-annotation trigger for pre-existing samples  *(Cross-cutting, merged #373)*

**Problem.** Samples annotated before the carriage/zygosity fix have 100 % NULL
`zygosity` (so cancer/cardio/carrier modules silently return 0), and samples
annotated before F25/F15 have NULL `deleterious_total_assessed` / `gnomad_af_popmax`.
None of these are recomputed until `is_sample_stale` flags them — and the
staleness gate (`backend/services/staleness.py::is_sample_stale`) compares
**only** the sample's recorded `vep_bundle` **major** against the installed
`database_versions['vep_bundle'].version` major.

**Plan.** Bump the installed `vep_bundle` **major** version so `is_sample_stale`
re-flags every pre-existing sample → the re-annotation banner prompts → a live
re-run repopulates `zygosity` and the new columns through the corrected engine.
Verify the staleness path end-to-end (`is_sample_stale` → banner → re-annotate →
zygosity + new columns populated). Test: a sample recorded at the prior
`vep_bundle` major is reported stale after the bump.

**Cross-cutting** → full sweep.

**As built (#373).** Version-only bump: `bundles/manifest.json` `vep_bundle`
`version` v2.0.0 → **v3.0.0**, while `url`/`sha256`/`size_bytes` keep pointing at
the real published `bundle-v2.0.0` asset (the catalog is unchanged — the
corrections are *code*, not bundle data). So the manifest version intentionally
leads the asset tag; no new release asset is required and downloads keep working.
A documenting `comment` key in the manifest entry (tolerated/ignored by the
parser) and a note at the `run_vep_bundle_update` parity check explain the single
expected, non-fatal `vep_bundle_metadata_version_mismatch` advisory on update.
AncestryDNA still gates on ≥ 2.0.0 (v3.0.0 satisfies it).

---

## Per-finding workflow (campaign conventions)

1. Branch off **current** `origin/main` (never a stale base); one PR per finding.
2. Co-land a test (live-path `build_live_run` M-test, or a focused unit test).
3. Lint: `ruff check backend/ tests/` **and** `ruff format --check backend/ tests/`
   — run **both after every edit**, including docstring/comment tweaks (CI Lint
   enforces E501; CodeRabbit does not flag it).
4. Tests: targeted module tests **+ the full `tests/backend/test_annotation_engine.py`**
   for any `count_deleterious`/evidence/engine change.
5. **On any `SAMPLE_SCHEMA_VERSION` bump**, also update the two hard-coded
   literals: `test_panel_coverage_migration.py::test_schema_version_is_N` and
   `test_sample_schema_migration_v7_v8.py::test_upgrade_stamps_v8`. A feature-keyword
   grep misses them — grep `== <oldN>` and `version_is_<oldN>`.
6. Local CodeRabbit: `coderabbit review --agent -t committed --base origin/main`
   (**`origin/main`**, not `main` — local `main` is stale).
7. Push + merge as **`bioedcam`** (`bioedca` is read-only → 403): use
   `gh auth switch --user bioedcam` for push/PR/merge, then restore `bioedca`.
8. Watch CI to green (PR tier runs Linux-only backend + lint + docker + smoke;
   macOS/E2E are nightly/merge-queue), then squash-merge.
