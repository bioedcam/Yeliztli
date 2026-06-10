# Test-Suite Audit Debt — Remediation Plan

**Created:** 2026-06-09
**Last refreshed:** 2026-06-09 — re-verified against `main` @ `ed51cd3` (post `#362`) by a multi-agent sweep that read every referenced test + module under current code. Line numbers, test names, and the remaining-work scope below reflect that re-verification, not the original audit snapshot.
**Source:** an internal 2026-06-08 multi-agent test-suite audit (working notes, not committed to the repo). Its findings are reproduced inline below, so this plan is self-contained.
**Purpose:** track the remaining test-quality debt after the first remediation wave, with concrete, PR-sized actions.

---

## 1. Background

The audit's headline finding: the test suite encoded the **genotype-agnostic bug's happy path, not its guard** — almost no test seeded a `hom_ref` (non-carrier) Pathogenic variant and asserted it is suppressed, and several value-producing functions were covered only by `assert x is not None` / `status_code == 200`. It surfaced **12 code-verified masking flaws**, **~53 medium/low weaknesses**, CI-tiering issues, and validation-worktree blockers.

### Already remediated (for context — not part of this plan)

| Area | PRs |
|---|---|
| Live-engine carriage gate + zygosity column + M1–M9 validation suite | #315, #316, #320 |
| CI 3-tier model (path filters, Linux-only PR matrices, `ci-required` aggregator, nightly cross-OS backstop, slow-marks) | #325 |
| Value-blind unit tests #7–#12 (query-translator `literal_binds`, ancestry/LAI per-population asserts, e2e re-annotation counts, frontend zygosity labels) | #326 |
| P3 named items: `test_security_audit` CORS/regex, `test_backup_api` path-traversal | #326 |
| Carriage negative-controls + interactive `/search`, `/run`, panel-search gating (#2/#4) | #326, #339 |
| Nutrigenomics MTHFR C677T strand fix (#6) + shared strand-aware lookup across all 8 panel-scoring modules | #340, #344, #347 |
| `test_update_manager` ineffective-patch → real `CHECK_FNS` dispatch | #341 |
| Expansion-wave disease modules **ship with their own negative controls** — every risk-genotype module's `assess_*()` returns `calls == []` (or an indeterminate, never a false-negative) on an all-reference / non-carrier genotype, and each has an explicit test for it (thrombophilia `test_both_reference_no_finding`, HFE `test_homozygous_reference_no_finding`, alpha-1 `test_normal_no_finding`, AMD `test_no_risk_no_finding`, APOL1 `test_single_g1_allele_low_risk_no_finding`, gout `test_ref_ref_no_abcg2_finding`, Parkinson's `test_reference_no_finding`, MT-RNR1/LHON `test_reference_call_no_finding`, sex-aneuploidy `test_typical_xx_no_signal`). The shared `risk_genotype` engine itself is covered by `test_all_reference_no_calls`. | #322–#361 |
| Live-path validation suite (M1–M9) landed **and** the PR #316 cancer-regression guards (`test_detail_json_has_genotype`, `TestFetchCancerFindingsExcludesPRS`) confirmed still present on `main` — the audit Part 3 "deleted-guards" merge-blocker is **resolved** | #320 |

> **Re-verification note (2026-06-09).** The audit's §1.5.3 asked to "fill gaps (cancer, cardiovascular, the new disease modules …)". That sweep is now **done for the risk-genotype / GWAS-risk modules** — they were authored with negative controls from the start (rows above). The *only* clinical-finding module still missing a negative control is **`carrier_status`** (a ClinVar-carriage finder, like `cancer`/`cardiovascular`, which it predates) plus two panel-scoring nuances (`pharmacogenomics` wildtype→no-alert, `nutrigenomics` standard→no-elevated-finding). The remaining-work list below is scoped to those, not a blanket per-module pass.

### What remains (this plan)

- **A.** De-mask the one remaining "hand-overwrite the column under test" end-to-end test (audit finding #5 / guardrail §1.5.4). *Partial progress already on main:* an `xfail(strict)` indel tracker (`test_f508del_indel_carriage_resolved`) and an `test_f508del_indel_unscoreable_on_chip` invariant exist; what remains is removing the `UPDATE … SET zygosity='het'` by driving the test with a genuinely-scoreable SNV carrier.
- **B.** Operationalize the test-quality guardrails from the audit's §1.5: (b1) anti-`assert-is-not-None` convention doc; (b2) relaxed perf-assert cleanup (inline the real target); (b3) close the last hom_ref negative-control gap — **`carrier_status`** (+ pgx/nutrigenomics panel cases) — the risk-genotype modules are already done.
- **C.** The medium/low masked-assertion sweep (the items not yet touched).
- **D.** Owner-only repo settings (branch protection + merge queue).

> Every file/test reference below should be **re-verified against current code** before editing — line numbers drift and some tests have been renamed.

---

## 2. Prioritized remaining work

### P1 — De-mask the hand-overwritten end-to-end test (audit #5)

**Problem.** `tests/backend/test_sample_merge_full_pipeline.py::test_carrier_finding_source_attribution_emitted` hand-writes the column it claims to validate (`UPDATE annotated_variants SET zygosity='het' WHERE rsid='rs113993960'`) before calling `extract_carrier_variants`. The real path: CFTR F508del is an **indel** (ref `ATCT`, alt `A`), and `classify_zygosity(genotype='AT', ref='ATCT', alt='A')` returns `None` (it only scores single-base ref/alt), so the carrier finding is actually *suppressed*. The overwrite masks a real indel-carriage gap.

**Action (choose one, in priority order):**
1. **De-mask the test** — drive it with a genuinely scoreable carrier (a SNV carrier whose zygosity the production path computes), so no manual `UPDATE` is needed. Keep the existing `xfail(strict)` indel-carriage test (`test_f508del_indel_carriage_resolved`) as the tracking marker for the real gap.
2. **(Larger, optional) Support indel carriage** — extend `classify_zygosity` (and any allele-set logic) to score simple indels, then remove the overwrite *and* flip the `xfail`. Scope this only if indel carrier-status is a product requirement.

**DoD:** the end-to-end test no longer mutates `zygosity`; it fails if the production carriage path regresses. ~1 PR.

---

### P1 — Test-quality guardrails (audit §1.5) — make them repo norms, not one-offs

1. **Anti-`assert x is not None` / `status_code == 200`-only convention. — DONE.**
   - `CONTRIBUTING.md` now carries a "Test assertion standards" section (value-producing functions must assert the *value*; status-only assertions are insufficient; two-sided filter checks; no vacuous empty-list loops; no hand-overwriting the column under test; inline perf targets) plus the hom_ref negative-control convention.
   - The advisory check is a path-scoped `.coderabbit.yaml` review rule for `tests/**` (a semantic LLM check, since the ~1000 existing `is not None` / `status_code == 200` lines make a grep-based blocking lint infeasible — confirmed 593 + 438 hits). It flags *new* sole-`is not None` / status-only assertions and missing hom_ref controls, and is explicitly **advisory, not CI-blocking**.
   - **DoD:** documented convention (✓) + advisory check (✓).

2. **Relaxed perf / timing asserts.**
   - `tests/backend/test_benchmark.py::test_annotation_600k_timing` asserts `< 1800s/2700s` while the PRD (Product Requirements Document) target is `<120s/<300s`. Either tighten to the real target or move to the nightly/benchmark tier — and **inline the target values (`120s/300s`) in a comment directly adjacent to the assertion** (the module comment references "the PRD target" but the assert site shows only the relaxed limits), so a future relaxation sees the 10× gap at the point of edit.
   - Sweep for sibling relaxed timing asserts (e.g. `test_performance_optimization` timing tests — already `slow`-marked; confirm their thresholds are meaningful).
   - **DoD:** no perf assert sits next to a target it's 10× looser than. ~1 PR.

3. **hom_ref negative-control convention.**
   - The shared `tests/backend/_carriage_fixtures.py` (`hom_ref_pathogenic_row` / `het_pathogenic_row`) exists and is used by the ClinVar-carriage finders (`rare_variant_finder`, `custom_panels`).
   - **Coverage as of 2026-06-09 (re-verified):** `cancer` (`test_excludes_non_carried_zygosity`) and `cardiovascular` (`test_fh_status_negative_when_only_homozygous_reference`) are covered. The expansion-wave **risk-genotype** modules are covered by construction — their gate is risk-allele *dosage*, not ClinVar significance, so the correct negative control is "all-reference genotype → `calls == []`", which each module already asserts (see the "Already remediated" table). Non-carriage modules (`apoe` — everyone carries two alleles; `sex_aneuploidy`/`roh`/`kinship`/`qc` — sample-level metrics) are **N/A**.
   - **`carrier_status` — DONE.** `test_carrier_analysis.py::test_hom_ref_pathogenic_excluded` now seeds a `hom_ref` Pathogenic CFTR variant (with a real het carrier as a positive control) and asserts the non-carrier is suppressed through both `extract_carrier_variants` and `store_carrier_findings` (distinct from the pre-existing homozygous-ALT/affected exclusion `test_t3_37_hom_plp_excluded`).
   - **`pharmacogenomics` — DONE.** `test_no_data_defaults_to_wildtype` now also asserts the phenotype (`Normal Metabolizer`), and `test_absent_data_fabricates_no_risk_metabolizer` asserts absent pgx data resolves every gene to `*1/*1` Normal Metabolizer and that `generate_prescribing_alerts` never fabricates a Poor/Rapid/Intermediate/Ultrarapid alert from that absence.
   - **Still open (folded into the P2 sweep):** `nutrigenomics` (standard genotype → no elevated-category SNP finding).
   - **DoD:** `carrier_status` (✓) and `pharmacogenomics` (✓) have negative controls; the nutrigenomics wildtype case remains (P2).

---

### P2 — Targeted masked-assertion fixes (highest-signal medium items)

Each is a real masked defect; fix the assertion (and the SUT if the assertion then fails).

| Item | Test | Fix |
|---|---|---|
| Carriage not surfaced | `test_variant_detail_api` (seeds `zygosity='hom_ref'`, never asserts the endpoint reflects carriage) | Assert the endpoint surfaces/suppresses by carriage. |
| One-sided zygosity filter | `test_rare_variants_api::test_search_zygosity_filter` | Also assert the `hom_alt` row is **excluded** (not just that returned rows are `het`). |
| VCF body unverified | `test_export::test_vcf_export` | Assert REF/ALT/GT on data lines (catches dropped variants, swapped REF↔ALT, mis-encoded GT). |
| Aggregation could drop modules | `test_cross_module_integration::test_unified_findings_aggregates_all_modules` | Assert each expected module contributes, not just `len(findings) > 0`. |
| APOE genotype too loose | `test_cross_module_integration::test_apoe_genotype_determination` | Assert the exact diplotype (e.g. `ε3/ε3`), not `'3' in str(genotype)`. |
| PGx phenotype unchecked | `test_pharmacogenomics::test_no_data_defaults_to_wildtype` (renamed since the audit) | Assert the **phenotype** call (`Normal Metabolizer`), not only `diplotype=='*1/*1'`. Pairs with the carrier-convention pgx wildtype-no-alert case (P1-B3). |
| PRS RUO/cap is a circular fixture | `test_traits_api::test_prs_evidence_cap` / `test_prs_research_use_only` | **Re-verified:** the `/api/analysis/traits/prs` endpoint is *pass-through* — it does not clamp. The cap/flag are enforced at the **producer** (`store_prs_findings`), where they are already independently tested on a computed-then-stored finding (`test_prs.py::test_findings_have_prs_category` asserts `evidence_level == 1`; `::test_detail_json_has_ancestry_source_tag` asserts `detail["research_use_only"] is True`). The API tests are surface checks; hardened with a non-empty-items guard (so the loop can't pass vacuously) + a comment pointing to the producer enforcement. |
| LAI label/remap not value-asserted | `test_lai::test_painting_structure` / `test_remap_indices` | Assert per-segment labels / remap values against an independent truth table (same mislabel class as the fixed #9). |

**DoD:** each test asserts the behavior it claims to. Batch into ~2–3 PRs by subsystem (variant/export, cross-module, traits/pgx/lai).

**Progress:** the **variant/export** subsystem is done — `test_variant_detail_api` now asserts hom_ref carriage is surfaced; `test_rare_variants_api::test_search_zygosity_filter` is two-sided (a rare hom_alt carrier is excluded by the het filter and surfaced by the hom_alt filter); `test_export::test_vcf_export` asserts per-line REF/ALT/GT (no dropped variants / swapped REF↔ALT / mis-encoded GT). The **cross-module** subsystem is done — `test_unified_findings_aggregates_all_modules` asserts findings span ≥2 distinct modules (pharmacogenomics + another), and `test_apoe_genotype_determination` asserts the exact `ε3/ε3` diplotype. The **pgx** phenotype/negative-control fixes are done (see P1-B3). The **traits/lai** subsystem is done — `test_lai::test_remap_indices` now asserts the **production** `GnomixModel.pop_remap` (not a re-implementation), `test_lai::test_painting_structure` asserts the per-segment population labels + palette colors (catching the index→population mislabel class), and the traits PRS API tests are clarified as surface checks over the producer-enforced (and producer-tested) RUO/evidence cap, guarded against vacuous empty-items loops. **The P2 backend masked-assertion sweep is complete.**

---

### P2 — Frontend chart/mocks that discard the data under test — DONE

| Item | Test | Fix |
|---|---|---|
| Plotly mock collapses traces | `density-chart.test.tsx`, `qc-charts.test.tsx` | ✅ The mock now exposes `data-traces` (each trace's `name` + `y`); tests assert the per-bin impact counts, the het/hom/nocall series, and the per-chromosome het rates (`het/(het+hom)`) — not just `data.length`. |
| System dark mode untested | `dark-mode.test.tsx` | ✅ Two tests stub `matchMedia('(prefers-color-scheme: dark)')` and assert `.dark` is applied (OS dark) / removed (OS light) in System mode. |
| Promised coverage absent | `overlays.test.tsx` | ✅ Added the apply→results-table and delete (confirm + `DELETE` request) interaction cases. |
| Findings/variant zygosity not asserted | `findings-explorer.test.tsx`, `variant-table.test.tsx` | ✅ `findings-explorer` asserts the rendered `het`/`hom` labels; `variant-table` asserts the genotype value and renders `het` **and** `hom_alt` via a zygosity-column preset. |

**DoD:** chart mocks preserve the data under test; promised cases exist. ✅ Done in one frontend PR (983 vitest tests green, `eslint` + `tsc -b` clean).

---

### P3 — Low-severity cleanups — DONE

- ✅ `test_auth::test_authenticated_request_passes` now asserts `== 200` (a 500/403 that `!= 401` allowed is caught).
- ✅ `test_skin_api::test_run_idempotent` now also queries the skin `findings` table and asserts the row count equals `findings_count` after two runs (the delete-then-insert really cleared the first — equal `findings_count` alone could not catch appended duplicates).
- ✅ `test_watches::test_list_multiple` now injects strictly-increasing timestamps (patching the route clock) instead of `time.sleep(0.01)`, so the desc-ordering assertion is deterministic.
- ✅ `test_scripts_lai_runner_removed` now fails loudly (`assert returncode != 128`) instead of skipping on a broken `git grep` environment.
- ✅ `test_variant_card::test_generate_pdf_endpoint_with_mock` — clarified its scope: it validates endpoint *plumbing* (bytes → content-type/filename); the Playwright/Chromium PDF rendering itself is an E2E-tier concern (`backend/reports/variant_card.py`), not a backend unit test, so the generator mock is deliberate. (Plus: the `test_benchmark` status-print now reports the PRD hard limit in minutes for unit consistency.)

**DoD:** each low item asserts real behavior / de-flaked. ✅ Done in one catch-all PR.

---

### Owner — repo settings (requires `bioedcam`)

1. **Branch protection on `main` — DONE (2026-06-09).** A repository ruleset **"main protection"** (id `17483890`, `enforcement: active`) targets the default branch and requires the **`CI Required`** + **`Lint`** status checks to pass before `main` can be updated (`strict_required_status_checks_policy: false`, so branches need not be up-to-date). The `CI Required` aggregator (`ci.yml`) treats *skipped* jobs as pass, so it is safe as the sole gate alongside `Lint`; on a PR the Tier-2 macOS/Docker/E2E legs are skipped and `CI Required` passes on the Linux jobs. Verify with `gh api repos/bioedcam/Yeliztli/rules/branches/main`.

2. **Merge queue — N/A on this repo.** GitHub's merge queue is only available for **organization-owned** repositories; `bioedcam/Yeliztli` is owned by a **personal (User) account**, so the "Require merge queue" rule is absent from the ruleset UI and the rulesets REST API rejects the `merge_queue` rule type (`422 Invalid rule 'merge_queue'`, even as admin with documented defaults). It cannot be enabled without transferring the repo to an organization.
   - **Consequence:** the Tier-2 macOS/Docker/E2E legs cannot gate *pre-merge* via a `merge_group` event. They continue to run on **`push`→`main` (post-merge)** plus the **nightly cross-OS backstop**, so a Tier-2 break is caught "at most one merge later." This is the documented fallback (audit Part 2.4) and is the recommended steady state for a repo this size.
   - **If pre-merge Tier-2 gating is ever required without a queue:** change each Tier-2 job's `if:` in `ci.yml` to also run on `pull_request` (they are already in `CI Required`'s `needs:`, so they would then block PRs directly). Cost: macOS (~10× Linux minutes), a Docker build, and 3-browser Playwright on **every** PR — the exact per-PR cost the 3-tier model was built to avoid — so this is a deliberate trade, not a default.

**DoD:** `main` is protected by the `CI Required` + `Lint` required checks (✓). Merge queue is **not applicable** on a personal-account repo; Tier-2 stays post-merge (`push`) + nightly.

---

## 3. Suggested sequencing & PR grouping

One PR per logical change (repo convention), each rebased on current `main`, reviewed locally with the CodeRabbit CLI (`coderabbit review`) before push.

1. **De-mask #5** (P1) — small; unblocks the last "fixture stubs the SUT" case.
2. **Perf-assert cleanup** (P1 guardrails item 2) — small, isolated to `test_benchmark`.
3. **hom_ref negative-control coverage** (P1 guardrails item 3) — now a small PR: `cancer`/`cardiovascular` and every risk-genotype module are already covered, so this is just `carrier_status` + the pgx/nutrigenomics wildtype cases.
4. **Backend masked-assertion fixes** (P2) — split into ~2–3 PRs by subsystem.
5. **Frontend chart/mocks** (P2) — ~1–2 frontend PRs.
6. **Low-severity catch-all** (P3) — a single cleanup PR.
7. **Anti-`assert-is-not-None` convention** (P1 guardrails item 1) — docs plus an optional advisory check.
8. **Branch protection + merge queue** — owner-only config, no PR.

Rough order of effort: P1 items are small and high-signal; the P2 sweep is the bulk; P3 is a single catch-all; the owner items are config, not code.

---

## 4. Definition of done (overall)

- ✅ No "end-to-end" test mutates the column it validates. *(de-mask #5)*
- ✅ Every clinical-finding module has a `hom_ref` (or all-reference) negative control. *(carrier_status added; risk-genotype modules already covered)*
- ✅ Convention against `assert x is not None` / `status_code == 200`-only assertions is documented (`CONTRIBUTING.md`) with an advisory `.coderabbit.yaml` check; the audit's high-signal value-blind tests are fixed. *(Legacy lines are migrated opportunistically, not in a blocking sweep.)*
- ✅ No perf assert sits next to a target it is an order of magnitude looser than. *(120s/300s inlined at the benchmark assert)*
- ✅ Frontend chart tests assert the data, not the trace count *(density/qc charts expose per-trace y-values; dark-mode System-mode OS preference; overlays apply→results + delete; findings/variant zygosity rendering)*.
- ✅ **`main` is protected** by the `CI Required` + `Lint` required checks (ruleset "main protection", active, 2026-06-09). The **merge queue is N/A** — it requires an organization-owned repo and this is a personal-account repo, so Tier-2 (macOS/Docker/E2E) stays post-merge (`push`) + nightly rather than pre-merge (see "Owner — repo settings").
