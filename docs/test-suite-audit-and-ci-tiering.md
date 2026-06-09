# Test-Suite Audit & CI Tiering Review

**Date:** 2026-06-08
**Branch/worktree:** `worktree-tests` (`.claude/worktrees/tests`, at `026146f`)
**Scope:** (1) test flaws that mask improper functioning, (2) the parallel `validation` worktree's test work, (3) PR-vs-nightly CI tiering.

---

## How this was produced

A multi-agent audit fanned out over the **entire** test tree — **184 backend test files (~5,205 test functions, ~94.5k LOC) + 62 frontend test files (~19k LOC)** — in 19 balanced batches. Each batch was read in full against a masking-flaw rubric; every **high/critical** candidate was then handed to an **independent adversarial verifier** that read the *production code under test* and tried to construct a concrete broken-code scenario that would still pass the test green. Findings that survived that step are labelled **VERIFIED**.

- **72** candidate flaws surfaced.
- **12 VERIFIED** as masking a real defect (verifier read the SUT and confirmed).
- **2 false positives** (verifier refuted them — documented below so they aren't re-raised).
- **53** medium/low candidates (real weaknesses, not individually code-verified — grouped below).
- **0 batches failed** (a first run hit a transient server-side rate limit from a 16-wide burst; re-run throttled to 4 agents/wave).

CI tiering was analysed by a 2-perspective judge panel plus a heavy-test profiler that **measured actual pytest durations**. The validation worktree was reviewed by a dedicated agent that diffed it against `main`.

---

# Part 1 — Test flaws that mask improper functioning

## 1.1 The dominant theme: carriage / zygosity gaps

This repo's flagship defect class is the **genotype-agnostic annotation bug** — reporting a clinical finding without checking whether the person actually *carries* the variant (PR #315/#316 fixed parts of it; the `validation` worktree is finishing it). **The test suite has a matching blind spot: almost no test seeds a `hom_ref` (non-carrier) Pathogenic variant and asserts it is suppressed.** Every fixture is `het`/`hom_alt`. So a regression to genotype-agnostic behaviour — a non-carrier surfaced as a clinical finding — passes green across the board.

This is the single most important structural finding: **the tests encode the bug's happy path, not its guard.**

## 1.2 VERIFIED findings (code-confirmed)

Severity shown as *finder → verifier* (the verifier sometimes down-graded after reading the SUT). All are real; ordering is by impact.

| # | File : test | Type | Sev | What a green test currently hides |
|---|---|---|---|---|
| 1 | `test_rare_variant_finder.py` : `TestDefaultFilter.test_finds_rare_variants` (L225) | carriage_gap | **critical→high** | `find_rare_variants()` applies a zygosity condition **only when `filters.zygosity` is set** (`rare_variant_finder.py:307-308`); the default filter has **no carriage gate** and never imports `CARRIED_ZYGOSITIES`. Every fixture is het/hom_alt. A `hom_ref` Pathogenic variant the person does *not* carry is reported and stored as a `clinvar_pathogenic` finding. |
| 2 | `test_rare_variants_api.py` : `TestSearchEndpoint.test_search_default_filters` (L263) | carriage_gap | **critical→high** | Same defect through the **API + live `run_all._run_rare_variants`** path (which calls `find_rare_variants(RareVariantFilter())`). Verifier *empirically* inserted a hom_ref Pathogenic BRCA2 row and it leaked into findings with `pathogenic_count=1`. Default search asserts only rsid-by-AF membership, never hom_ref suppression. |
| 3 | `test_annotation_engine.py` : `TestRunAnnotation.test_all_fields_populated` (L570) | carriage_gap | **critical→high** | The engine path `run_annotation()/_lookup_clinvar()` (`engine.py:168-186`) **never computes/writes the `zygosity` column** — only the standalone `clinvar.py::annotate_clinvar` path does (the PR #315 fix at `clinvar.py:903`). This file has **zero** assertions on `row.zygosity` (grep: 0 hits). If the engine ships NULL zygosity, downstream carriage gates drop every carrier (NULL ∉ carried set → 0 findings) or leak hom_ref. |
| 4 | `test_custom_panels_api.py` : `TestSearchWithPanelEndpoint.test_search_with_panel[_and_filters]` (L371) | carriage_gap | **high** | Panel search routes through `find_rare_variants` (`custom_panels.py:305`), inheriting the no-default-gate bug. Both fixtures are het; assertions are `variants_found >= 1` / `findings_stored >= 1` — pass even if non-carriers are reported. |
| 5 | `test_sample_merge_full_pipeline.py` : `test_carrier_finding_source_attribution_emitted` (L600) | carriage_gap / fixture_stubs_sut | **critical→high** | The "end-to-end" carrier test **hand-overwrites the zygosity column it claims to validate**: `UPDATE annotated_variants SET zygosity='het' WHERE rsid='rs113993960'` before calling `extract_carrier_variants`. The real path: F508del is an **indel** (ref=`ATCT`, alt=`A`); `classify_zygosity('AT','ATCT','A')` returns **None**, so the CFTR carrier finding is actually *suppressed*. The overwrite masks a real indel-carriage defect. |
| 6 | `test_nutrigenomics.py` : `TestScorePathways.test_full_scoring_with_mthfr_ct` (L373) | tautology / pass-for-wrong-reason | **high** | Masks an allele-harmonization bug. A real het MTHFR C677T `rs1801133="CT"` is **not** scored Moderate — the panel keys it on G/A (`{GG,GA,AG,AA}`), so `"CT"` falls through to STANDARD. The `folate == MODERATE` assertion only passes because the test *also* seeds `rs1801131="AC"` (A1298C), which legitimately scores Moderate and dominates the pathway `max()`. The C677T SNP under test is never asserted on its own category. |
| 7 | `test_e2e_pipeline.py` : `test_reannotation_succeeds` (L499) | weak_assertion | **high→medium** | Sold as crash-recovery ("second annotation produces the same results"; engine *deletes* annotations before re-running). Asserts only that both jobs reach `status=='complete'`. A defect where re-annotation deletes then **fails to repopulate** (0 annotated variants, status still flips to complete) passes green. Never compares run-1 vs run-2 counts; never asserts run-2 count > 0. |
| 8 | `test_ancestry.py` : `test_eur_sample_classified_as_eur_or_nearest` (L633) | tautology | **high→medium** | The T3-25 acceptance test ("EUR sample lands in EUR cluster"), but the only correctness assertion is `result.top_population in bundle.populations` — **always true** for any classification. A real-bundle projection regression that misclassifies EUR as MID/AFR/EAS (the documented "MID misclassification" class) passes. Never asserts `top_population == 'EUR'`. |
| 9 | `test_lai.py` : `TestGlobalAncestry.test_proportions_sum_to_one` (L229) | weak_assertion | **high** | Constructs hap0=all-AFR, hap1=all-EUR (correct result: AFR=0.5, EUR=0.5) but asserts only `abs(sum-1.0) < 0.01`. An **index→population mislabel** (the known EUR↔MID LAI bug) normalizes to 1.0 and passes. No per-population value assertion. |
| 10 | `test_query_builder.py` : `test_greater_than` (L66) | weak_assertion | **high→medium** | `translate({... '>' ...})` asserted only `expr is not None`. `translate()` always returns a non-None expression for any valid operator, so a **sign-flipped `>`→`<`** bug (e.g. "CADD > 20" silently returning low-impact variants) passes. `>` has no value-asserting integration test. |
| 11 | `test_query_builder.py` : `test_greater_than_or_equal` / `_less_than_or_equal` / `_not_equals` / `_begins_with` / `_ends_with` / `_not_in` (L78…) | weak_assertion | **high→medium** | Same `assert expr is not None`-only pattern for 6 operators with **no** integration coverage. A wrong LIKE anchor (`beginsWith`→`'%value'`) or inverted `notIn`→`IN` returns the wrong variant set to the filter UI and passes. |
| 12 | `frontend/.../rare-variants.test.tsx` : `VariantDetailPanel > renders variant details` (L201) + `ResultsTable > renders variant rows` (L139) | weak_assertion / carriage_gap | **high→medium** | The zygosity label is decided at `VariantDetailPanel.tsx:92` (`Homozygous`/`Heterozygous`) and `ResultsTable.tsx:100` (`Hom`/`Het`) — and **never asserted**. A het↔hom label inversion shows a het carrier as homozygous (a clinically wrong call) and every test still passes (they check gene/rsid/significance/CADD only). |

### Cross-link to the `validation` worktree (important)
Findings **#1–#4** are exactly the root cause that the `validation` branch commit `e0107b5` ("wire carriage into the live engine + gate rare variants (C1+D1)") fixes — it adds `RareVariantFilter.carried_only`, gates on `CARRIED_ZYGOSITIES`, and adds a "hom-ref negative control" to the new suite. **That production fix is *not* in this worktree's branch.** The audit independently confirms the validation team is fixing the right defect — but the *legacy* tests above still need hom_ref negative-control assertions added so they lock the gate rather than encode the bug. **Do not close #1–#4 as "fixed by validation" without adding the negative-control assertions.**

## 1.3 Medium / low findings (53) — by theme

Not individually code-verified, but each names a concrete masked defect. Counts: **28 medium, 25 low**; ~31 weak-assertion, 6 tautology, 4 carriage_gap, plus over-mocking / no-assertion / skip / nondeterminism singletons. The notable ones:

**More carriage/zygosity blind spots (medium):**
- `test_variant_detail_api.py::test_returns_coverage_and_findings` — seeds `zygosity='hom_ref'` yet never asserts the endpoint surfaces carriage.
- `test_rare_variants_api.py::test_search_zygosity_filter` — asserts returned rows are `het` but never that the `hom_alt` row was excluded (one-sided filter check).
- `frontend findings-explorer.test.tsx::shows ClinVar significance` — fixture `zygosity:"het"`, `FindingRow` renders it, never asserted.
- `frontend variant-table.test.tsx::renders variant rows` — genotype/zygosity columns rendered, never asserted.

**Weak/tautological assertions that hide real bugs (medium):**
- `test_security_audit.py::test_cors_rejects_external` — asserts `allow_origin != 'evil.example.com'`; Starlette omits the header for disallowed origins, so the value is `None` and the assertion is vacuous (a CORS misconfig that echoes a *different* wrong origin would pass).
- `test_security_audit.py::test_no_variant_data_in_outbound` — the static-analysis regex is non-DOTALL, so `.*` stops at a newline; a multi-line outbound leak isn't matched (a privacy guard that under-detects).
- `test_backup_api.py::test_download_path_traversal` — titled "rejects `..`" but **never sends a filename containing `..`** (requests a clean name). The traversal guard is untested.
- `test_benchmark.py::test_annotation_600k_timing` — PRD target is <120s/<300s but the hard assert is relaxed to **1800s/2700s**; a 10x perf regression passes.
- `test_cross_module_integration.py::test_apoe_genotype_determination` — asserts `'3' in str(genotype)`; passes for ε2/ε3, ε3/ε4, etc., not just the intended ε3/ε3.
- `test_cross_module_integration.py::test_unified_findings_aggregates_all_modules` — asserts `len(findings) > 0`; aggregation could drop all-but-one module and pass.
- `test_export.py::test_vcf_export` — no assertion on VCF body; dropped variants / swapped REF-ALT / mis-encoded GT pass.
- `test_traits_api.py::test_prs_evidence_cap` / `test_prs_research_use_only` — expected value derived from the same path; a RUO PRS surfacing as clinical-grade (or unflagged) could pass.
- `test_update_manager.py::test_check_updates` — patches `backend.db.update_manager.check_clinvar_update`, but the endpoint dispatches through the module-level `CHECK_FNS` dict built at import → **the patch is ineffective** (over-mock that tests nothing).
- `test_pharmacogenomics.py::test_no_data_defaults_to_normal` — asserts only `diplotype=='*1/*1'`, not the phenotype call.
- `test_lai.py::test_painting_structure` / `test_remap_indices` — segment labels / population remap not value-asserted (same mislabel class as #9).

**Frontend chart mocks that discard the data under test (medium):**
- `density-chart.test.tsx`, `qc-charts.test.tsx` — the `react-plotly.js` mock collapses every trace to `data.length`; "correct bin counts" / het-hom-nocall trace values are never verified.
- `dark-mode.test.tsx` — docstring claims "System mode respects OS preference"; no test checks the resolved `.dark` class in System mode.
- `overlays.test.tsx` — suite docstring promises upload/apply/delete coverage; several are absent (no_assertion/coverage gap).

**Low-severity (representative):** `test_auth.py::test_authenticated_request` asserts `!= 401` (a 500 would pass); `test_skin_api.py::test_run_idempotent` asserts equal counts (doesn't prove no dup rows); `test_watches.py::test_list_multiple` orders via real `time.sleep(0.01)` (coarse-resolution `watched_at` flakes); `test_scripts_lai_runner_removed.py` skips on `git grep` exit 128 (skip masks failure); `test_variant_card.py::test_generate_pdf_endpoint_with_mock` fully mocks `generate_variant_card_pdf` (tests the mock).

> Full structured inventory (all 72 with evidence/fix/verdict) is in the workflow result JSON: `…/tasks/w4xi98n3j.output`.

## 1.4 False positives (verifier refuted — do not re-raise)
- `test_apoe_gate_api.py::test_e4_findings_accessible_with_gate` — flagged as status-only, but the per-category content/diplotype assertions elsewhere in the file (L325-360) cover the behaviour; this test legitimately checks only gate-accessibility.
- `test_allergy_api.py::TestRunScoring::test_run_scoring` — the "masking" premise was factually wrong; the real engine path is exercised and the findings_count assertion is adequate for what it claims.

## 1.5 Recommended test-quality guardrails
1. **Add a hom_ref negative-control convention.** Every analysis module that emits clinical findings should have at least one test seeding a `hom_ref` Pathogenic variant and asserting it is *absent* from findings. Consider a shared fixture `hom_ref_pathogenic_row()`.
2. **Ban `assert x is not None` / `status_code == 200`-only as the sole assertion** in unit tests for value-producing functions. For the query translator, compile with `literal_binds` and assert the rendered SQL.
3. **Assert zygosity rendering** in the variant table / detail / side panel (both `het` and `hom_alt` branches).
4. **Forbid hand-overwriting the column under test** in "end-to-end" fixtures (#5). If a seed can't reach the target state legitimately, the seed is wrong (or exposes a real bug).
5. **Tighten relaxed perf/timing asserts** or move them to the benchmark/nightly tier with the *real* target documented (don't keep a 1800s assert next to a 120s PRD target).

---

# Part 2 — CI tiering: what to keep on PR vs. push to nightly/merge (the "very important" item)

## 2.1 Current state (measured)

PR CI (`ci.yml`) runs on every `pull_request` **and** `push`→main: `lint`, `test-backend` (**3-OS matrix**: Linux + macOS-ARM + macOS-x86, each the full ~5,205-fn `pytest -m "not slow"`), `test-frontend`, `build-frontend`, `smoke-install` (**3-OS matrix**), `docker-build` (compose build + up + health), `actionlint`. `test-e2e` is already push-to-main only. **Measured PR wall-clock: 20–28 min.**

### The biggest, lowest-signal-per-PR cost centers
1. **2 macOS `test-backend` legs** — identical suite to Linux, billed **10× Linux minutes**, and `macos-15-intel` is flake-prone (the historical `test_liftover` flake).
2. **2 macOS `smoke-install` legs** — OS-portability validation, not per-change correctness.
3. **`docker-build` on every PR** — cold ~2–4 min image build even on docs-only/frontend-only PRs.
4. **No change-based scoping** — a frontend-only PR still pays the full backend suite, and vice-versa.

### The hidden offender: the `integration` marker does **not** gate
PR runs `pytest -m "not slow"` — **only the `slow` marker excludes a test.** `@pytest.mark.integration` does nothing for tiering, so heavy function-scoped integration tests run on **every PR**. Measured (GI conda env; relative ranking holds on CI):

| File | Measured | Why heavy | Action |
|---|---|---|---|
| `test_cross_module_integration.py` | **118.9s / 17 tests** | function-scoped fixture rebuilds ClinVar+gene-phenotype+CPIC+GWAS DBs **and** runs the real annotate pipeline **per test** | mark `slow` (nightly) **or** module-scope the fixture |
| `test_e2e_pipeline.py` | **73.9s / 19 tests** | rebuilds reference.db + vep + gnomad + dbnsfp and re-runs upload+annotate per test | mark `slow` (nightly) — keep one smoke test on PR |
| `test_huey_annotation.py` | **33.4s / 25 tests** | rebuilds reference.db + sample DB per test; drives `run_annotation_task` E2E | mark heaviest methods `slow`; keep unit-y guards on PR |
| `test_performance_optimization.py` | ~5.2s in 2 timing tests | `test_annotation_10k_with_timing` + `test_dbnsfp_rsid_lookup_performance` are wall-clock benchmarks (flaky on shared runners) | mark `slow` (belongs with `test_benchmark`) |
| `test_sample_merge_full_pipeline.py` | 11.1s / 5 tests | re-runs merge+annotate per sub-assert; bodies are read-only | **shrink**: `scope="module"` (5×4s → 1×4s) |
| `test_http_download.py` + `test_download_manager.py` | 11.1s / 35 tests | flat ~0.5s/test floor = fresh threaded HTTPServer startup/teardown (no real net/sleep) | **shrink**: shared session-scoped server; keep on PR |

> **Slow-marking just the top two (`test_cross_module_integration` + `test_e2e_pipeline`) reclaims ~193s/PR immediately.** Not heavy / leave as-is: `test_bulk_load`, `test_build_vep_bundle*` (build from a 50-row seed, not the real 14.7M-rsid bundle), `test_annotation_engine` (2.8s/83 in-memory tests), `test_lai` PR subset.

## 2.2 Recommended 3-tier model

- **Tier 1 — PR-blocking, target <8–12 min:** `lint`, `actionlint` (when workflows change), **Linux-only** `test-backend` (full `-m "not slow"`, the real logic gate), `test-frontend`, `build-frontend`, **Linux-only** `smoke-install`, and `docker-build` **only when Docker inputs change**. Gate backend/frontend jobs by path so a frontend-only PR skips the 5–8 min backend run and vice-versa.
- **Tier 2 — merge/post-merge (push→main, ideally a merge queue):** the **macOS** `test-backend` + `smoke-install` legs, `docker-build` (unconditional), and the existing 3-browser `test-e2e` — promoted to **required merge-queue checks** so a macOS/Docker/E2E break blocks the *merge* instead of being noticed afterwards.
- **Tier 3 — nightly (`nightly.yml`):** unchanged slow-tier real-bundle suite **plus** a daily full macOS matrix backstop (catches platform drift on quiet days) **plus** the newly `slow`-marked heavy integration/benchmark tests.

**Estimated impact:** PR wall-clock **20–28 min → ~6–12 min** (~55–70%); macOS-minute spend per PR ~**−66–100%** (4 macOS legs → 0 on most PRs); frontend-only/docs-only PRs drop to ~2–5 min.

## 2.3 Concrete changes

**A. `pyproject.toml` / test markers — move heavy tests to nightly:**
```python
# add @pytest.mark.slow to:
#   test_cross_module_integration.py (whole class)
#   test_e2e_pipeline.py (all but one representative smoke test, e.g. test_annotation_completes)
#   test_huey_annotation.py (the real-annotation methods only)
#   test_performance_optimization.py::test_annotation_10k_with_timing + ::test_dbnsfp_rsid_lookup_performance
```
Nightly already runs `pytest -m slow`, so these flow there automatically. (Optionally also gate them behind a `requires_real_bundle`-style skip if they need staged assets.)

**B. `ci.yml` — Linux-only matrices on PR, path filters, single aggregator required check.** Add a `changes` job (`dorny/paths-filter@v3`) emitting `backend`/`frontend`/`docker`/`workflows` booleans; set `needs: changes` + `if:` on each job; reduce `test-backend` and `smoke-install` PR matrices to `ubuntu-latest`; gate `docker-build` to `docker==true`; add a final `ci-required` aggregator (`if: always()`, `needs: [all]`, fails on any `failure`/`cancelled`) and make **only `ci-required` + `lint` required** in branch protection (skipped jobs otherwise hang a required check as "pending"). Full literal YAML diffs (both judge variants — one `if:`-based, one `fromJSON` dynamic-matrix + `merge_group`) are preserved in the workflow result JSON at `…/tasks/wfda4ml3c.output` under `ci.plans[].concreteDiffs`.

**C. `nightly.yml` — add a cross-OS backstop job:** a `macos-latest` + `macos-15-intel` matrix running the full `-m "not slow"` suite + a smoke install, `timeout-minutes: 60`, `fail-fast: false`, reusing the existing issue-filing pattern.

## 2.4 Risks & required mitigations
- **Pre-merge blind spot:** macOS-only and Docker regressions no longer caught at PR time. **Mitigation (required):** make the Tier-2 push/`merge_group` matrix a **required merge-queue check** (or at minimum a push→main job) — the macOS gap is then "at most one merge," not 24h. Enabling the merge queue is a **repo-admin action by owner `bioedcam`** (active `bioedca` is read-only).
- **Skipped required checks** read as perpetually pending in branch protection → use the `ci-required` aggregator as the sole required check.
- **Path-filter false-negatives** (a shared file the Docker image copies but the filter omits) → keep filters broad (`docker` filter must include `backend/**`, `pyproject.toml`, and—since the image bundles a frontend build—`frontend/**`) and run everything unconditionally on push→main as a backstop.
- **`fromJSON` dynamic-matrix trick** is brittle to quoting; validate with one PR run (expect 1 Linux leg) + one push run (expect 3 legs) before relying on it. The static-include + per-leg `if:` variant lints more cleanly.

---

# Part 3 — Coordination with the `validation` worktree

**Branch:** `validation/phase-cd-carriage` (`c1e7076`), built on `main` + PR #316. It adds the live-path **annotation-validation suite (M1–M8)** and fixes engine bugs (C1/D1/F31/F26).

## 3.1 The new M1–M8 suite — STRONG, no masking flaws
Rigorous and correctly designed: M1 recomputes carriage independently via the project's own `classify_zygosity` (no circularity); M2's synthetic truth-set has real **negative controls** (hom-ref → zero findings) that would have caught the root defect day one; M5 loads dbNSFP through the **production** parser so F31 is observable (a fixture suite would hide it); `xfail(strict)` markers correctly tag post-remediation expectations. **This is the model the legacy tests in Part 1 should follow.**

## 3.2 BLOCKING concern — deleted regression guards in `test_cancer_analysis.py`
The validation branch **deletes 63 lines** from `test_cancer_analysis.py`, removing:
- `test_detail_json_has_genotype` — the regression guard for the PR #316 blank-genotype-line fix.
- `TestFetchCancerFindingsExcludesPRS` — the API-contract guard that cancer findings are scoped to `category=='monogenic_variant'` so PRS rows don't leak.

These are **not** replaced anywhere in M1–M8 (which don't exercise the cancer module). **Action required:** confirm with the validation work whether this is (a) intentional supersession (then document the replacement), or (b) a rebase artifact / deleting-to-go-green. If (b), restore them. Treat as a **merge blocker** until resolved — otherwise PR #316's fixes ship unguarded.

The other existing-test edits are legitimate: `test_gnomad.py` (AF=0 → not-rare, a bug fix), `test_dbnsfp.py` (`MutPred_score`→`MutPred2_score`, fixes F31), `test_stale_sample_dependency.py` (missing annotation-state now raises 423 per Plan §7.4 — a deliberate behaviour change to coordinate with API consumers).

## 3.3 Coordination & landing-order risks
- **Landing order:** M2 truth-set tests (e.g. `test_multiallelic_picks_carried_allele`) are **not** `xfail`ed and assume C1 (carriage wiring, commit `e0107b5`) has landed. If the M1–M8 tests merge to `main` **before** the C1/F31/F26 fix commits, the suite is red on main. Either land fixes first or ship the assuming-tests as `xfail` until then.
- **Golden snapshot (M8) is dormant:** `golden_findings.json` doesn't exist yet; the test is `xfail(strict)` until Phase G. CI passes (xfail), but diff-based regression detection isn't active until the golden is committed and the marker removed.
- **No technical conflict surface** with this audit (different file trees), **but** both efforts touch tiering-adjacent surfaces. If Part 2's CI/marker changes and the validation merge land together, watch for collisions in `conftest.py` markers, `pyproject.toml` `[tool.pytest]`, and `ci.yml`/`nightly.yml`.

## 3.4 Overlap with this audit (the key synthesis)
Part 1 findings **#1–#4** (rare-variant default no-gate, engine doesn't write zygosity, panel search) are the **same root cause** the validation branch's `e0107b5` fixes in *production*. The two efforts are complementary:
- **Validation branch** fixes the engine/finder to gate on carriage.
- **This audit** shows the *legacy tests still encode the bug's happy path* — they need hom_ref negative-control assertions added so they lock the new gate. **Sequence:** land the validation engine fix, then patch the Part-1 carriage tests to assert hom_ref suppression. Don't close #1–#4 on the engine fix alone.

---

# Prioritized action list

| P | Action | Owner surface |
|---|---|---|
| **P0** | Resolve the `test_cancer_analysis.py` deletion (restore or document replacement) before the validation branch merges | validation worktree |
| **P0** | Confirm C1/F31/F26 fix commits land **before** (or with) the M1–M8 suite; `xfail` the assuming tests otherwise | validation worktree |
| **P1** | Slow-mark `test_cross_module_integration` + `test_e2e_pipeline` (reclaims ~193s/PR); add the heavy-test list in §2.1 to nightly | `pyproject.toml` markers / `ci.yml` |
| **P1** | `ci.yml`: Linux-only PR matrices + path filters + `ci-required` aggregator; full matrix → Tier-2/nightly | `ci.yml`, `nightly.yml` |
| **P1** | Add hom_ref negative-control assertions to Part-1 findings #1–#5 (after the validation engine fix merges) | `tests/backend/` |
| **P2** | Fix the value-blind unit tests #6–#12 (query translator `literal_binds`, LAI/ancestry per-population asserts, nutrigenomics C677T, zygosity rendering) | `tests/` |
| **P2** | Enable a merge queue with the Tier-2 matrix as required checks (closes the macOS/Docker/E2E pre-merge gap) — **owner `bioedcam`** | repo settings |
| **P3** | Sweep the 53 medium/low findings (esp. `test_security_audit` CORS/regex, `test_backup_api` path-traversal that sends no `..`, `test_update_manager` ineffective patch) | `tests/` |

---

*Appendix — raw structured results (evidence, suggested fixes, verifier reasoning for every finding):*
- *Test-flaw audit: `…/tasks/w4xi98n3j.output` (72 findings)*
- *CI tiering + heavy-test profile + literal YAML diffs: `…/tasks/wfda4ml3c.output`*
- *Workflow scripts: `.claude/wf_flaw_audit.js`, `.claude/wf_test_audit.js`*
