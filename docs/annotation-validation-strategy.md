# Yeliztli Annotation — Validation Strategy & Findings

**Date:** 2026-06-07
**Scope:** The live variant-annotation pipeline and the findings it produces, for DTC genotyping-chip inputs (23andMe / AncestryDNA).
**Nature of this document:** A validation *strategy* for finding problems in the annotation approach, together with the *findings* that strategy produced. **No application code was changed.** All evidence is read-only.
**Method:** Static code reading (`backend/annotation/*`, `backend/analysis/*`, `backend/ingestion/*`, `backend/tasks/huey_tasks.py`) + read-only empirical probing of the **real production install** at `~/.yeliztli` (the user's own sample already run end-to-end through the live pipeline: `samples/sample_1.db`, 677,436 raw variants / 676,971 annotated), cross-checked against `reference.db`, `gnomad_af.db`, `dbnsfp.db`, `vep_bundle.db`, and `RsMergeArch.bcp.gz`. Every claimed flaw was independently re-derived by an adversarial verifier (a 28-agent multi-dimension audit) that re-ran each query and re-read each cited line; severities below are the post-verification ratings.

---

## 1. Executive summary

The annotation pipeline has one pervasive, architecture-level defect with many downstream faces, plus a set of independent correctness, coverage, and methodology problems.

**The core defect: annotation and findings are genotype-agnostic.** A genotyping chip reports a call at *every* probe regardless of whether the person carries the variant. The live engine annotates purely by `rsid` and **never computes carriage**: `annotated_variants.zygosity`, `ref`, and `alt` are **NULL for 100 % of 676,971 rows** in the real sample. As a result:

- The **rare-variant finder dumps ~97 % of the genotype as "findings"** — **655,865** of 676,971 variants — including **30,472 "ClinVar Pathogenic"** findings of which only **~13 (0.04 %)** are actually carried. ~24,700 are homozygous-reference (the person carries only the normal allele) and ~7,250 are indels/no-calls that cannot be scored at all.
- The carriage-aware fix that was supposedly merged (PR #315) is **dead code in the live path**. The carriage-aware ClinVar writer (`annotate_sample_clinvar`) and the dbSNP-merge resolver (`annotate_sample_dbsnp`) are referenced only in their own docstrings and tests — the live engine never calls them.
- Because the gate column is NULL, the modules that *do* gate on carriage (**cancer monogenic, cardiovascular, carrier-status**) silently return **zero** findings. So the *same* root cause produces **false positives** on one surface (rare-variants) and **false negatives** (total suppression) on the most clinically actionable surface — simultaneously.
- The noise reaches the **dashboard's high-confidence card**: its top-5 (evidence_level ≥ 3) on the real sample are all `clinvar_pathogenic` for **MECP2 (Rett syndrome)** where the chip genotype is the `II` indel no-call sentinel — i.e. structurally unscoreable, surfaced as a 4-star "Pathogenic" finding.
- On an **XX (female) sample**, **1,631 chrY findings** are surfaced (1,628 from `--` no-call probes), including **14 "Pathogenic SRY"** findings — biologically impossible. There is no sex/chromosome gate anywhere in the annotation or finding path.

A second, important consequence of the dead carriage machinery: the **strand-handling, palindrome, and build/liftover logic is currently inert** (it is only reachable through the carriage path). That makes those bugs *latent today* — but they will all **activate at once** the moment carriage is wired in, with no production exercise behind them. The validation strategy in §4 is designed to catch both the active defects and these latent ones before any re-annotation goes live.

There are also independent defects: multi-allelic record selection that picks by review-stars not by carried allele (a *confirmed* false-Pathogenic for FOXC1 `rs376405759`); an arbitrary-ALT collapse in dbNSFP that flips the ensemble flag at ~17 % of multi-allelic sites; gene→disease mapping that mislabels dominant genes (BRCA1/2, LMNA, MSH2, HTT, VHL, LDLR) as **Autosomal recessive** and surfaces **obsolete** MONDO terms ("obsolete Li-Fraumeni syndrome 1" for TP53); a `MutPred2` column that is **100 % NULL** because of a wrong dbNSFP field name; gnomAD frequency that uses global-AF (not popmax) from an **exomes-only** source (91 % of chip variants get no AF and are mislabeled "novel"); 8-year reference-DB version skew (dbSNP merge archive frozen at b151/2018 vs ClinVar 2026) with no build field; and destructive crash-recovery that deletes the prior good annotation before re-running.

---

## 2. How the pipeline works (the part that matters for validation)

```text
upload → parser_23andme / parser_ancestrydna  (rsid, chrom, pos, genotype; NO ref/alt)
       → raw_variants  (PK = rsid; columns: rsid,chrom,pos,genotype,source,…  — no ref/alt)
       → huey_tasks.run_annotation_task → engine.run_annotation()
              · reads raw_variants (rsid,chrom,pos,genotype,source only)
              · per-rsid lookups: VEP bundle, ClinVar, gnomAD, dbNSFP, gene-phenotype
              · merge → annotation_coverage bitmask → bulk upsert (ON CONFLICT rsid)
       → run_all.run_all_analyses()  → findings table  (rare_variants, cancer, …)
```

Two facts drive most findings:

1. **Everything is keyed on `rsid`.** `raw_variants` carries no `ref`/`alt`; the engine's `SELECT` (`engine.py:838-847`) pulls only `rsid,chrom,pos,genotype,source`. So allele identity is never available to the engine.
2. **There are two divergent ClinVar/dbSNP annotation code paths.** The *live* one (`engine.run_annotation` → `lookup_clinvar_by_rsids` **without** `genotype_by_rsid`) is genotype-blind. The *correct* one (`annotate_sample_clinvar`, `annotate_sample_dbsnp`) is carriage-aware but **orphaned** (only referenced in docstrings/tests).

---

## 3. Findings

Severity reflects **real user impact today**. Many items are tagged **LATENT** = real code defect that produces no wrong output *yet* because the carriage path is dead; these become **ACTIVE** the moment zygosity is populated.

### 3.1 Master list (severity-ranked)

| # | Severity | State | Finding | Key evidence (real data) |
|---|----------|-------|---------|--------------------------|
| F1 | **Critical** | Active | Engine never computes carriage; `zygosity`/`ref`/`alt` 100 % NULL | 676,971/676,971 NULL; `_UPSERT_COLUMNS` omits all three |
| F2 | **Critical** | Active | Rare-variant finder dumps ~97 % of genotype as findings, ungated | 655,865 findings; default `RareVariantFilter()` |
| F3 | **Critical** | Active | "ClinVar Pathogenic" findings genotype-agnostic | 30,472 findings; **~13 carried (0.04 %)**; ~24.7k hom-ref |
| F4 | **Critical** | Active | `ensemble_pathogenic` findings genotype-agnostic | 9,188 findings; **23 carried (0.25 %)**; 99.3 % hom-ref |
| F5 | **Critical** | Active | gnomAD `rare`/`rare_flag` genotype-agnostic | `rare_flag`=1 on 37,346; **229 carried (0.61 %)**; 20,254 "rare" findings, 91 % hom-ref |
| F6 | **Critical** | Active | Monogenic cancer / cardiovascular / carrier findings silently suppressed (NULL ∈ list ⇒ 0) | cancer disease findings 0 (only 4 PRS), cardiovascular 0, carrier 0 |
| F7 | **High** | Active | Real carried P/LP findings lost too (SDHC, NF1, LDLR) — PR #315's "preserved" set drops to 0 | reconstructed 2/1/0 vs advertised 2/1/0; actual table = 0/0/0 |
| F8 | **High** | Active | "Pathogenic SRY" + 1,631 chrY findings on an **XX** sample; no sex/chrom gate | 14 SRY P/LP, 1,628 of 1,631 from `--` no-calls; sex never gates findings |
| F9 | **High** | Active | MECP2 `II`-indel (unscoreable) surfaced as top-5 dashboard high-confidence | dashboard card = evidence_level 4 `clinvar_pathogenic`, genotype `II` |
| F10 | **High** | Active | Multi-allelic ClinVar picks highest-star record, not carried allele → confirmed false-Pathogenic | FOXC1 `rs376405759`: carried allele = *Likely benign*, surfaced *Pathogenic*; 94,696 multi-sig rsids |
| F11 | **High** | Active | dbNSFP records an arbitrary ALT's scores at multi-allelic sites; ensemble flag flips for ~17 % | live `rs267608161` (hom-ref) stored `ensemble_pathogenic=1` from a non-carried ALT |
| F12 | **High** | Active | 595,951 common dbSNP SNPs mislabeled "novel" (absence-of-gnomAD ≠ novelty) | 91.4 % of variants have AF NULL; `rs3131972` (MAF≈0.2) → "novel" |
| F13 | **High** | Active | gnomAD source is **exomes-only** → 91 % AF-NULL ceiling | 8.63 % AF coverage; 0 MT rows; `Y`=6,385 (exome signature) |
| F14 | **High** | Active | gene→disease inheritance mislabel: dominant genes tagged **Autosomal recessive** | BRCA1/BRCA2/LMNA/MSH2/HTT/VHL/LDLR all "Autosomal recessive" (all dominant) |
| F15 | **High** | Active | Global-AF rarity, no popmax → ancestry-common variants flagged "rare" | 665 `rare_flag` variants are ≥5 % in some population; e.g. `rs56126202` afr=0.114 |
| F16 | **High** | Active | Indel P/LP can never be carriage-confirmed (43 % of P/LP; CFTR F508del, ~75 % of BRCA1/2) | 117,424 P/LP indels; 7,230 genotyped here; F508del `rs113993960` genotype `II` |
| F17 | **High** | LATENT | All strand/palindrome reconciliation is dead on the live path (no production exercise) | `classify_zygosity` has 0 callers in `engine.py`; activates with any re-annotation |
| F18 | **Medium** | Active | rsid-merge not reconciled → carried homozygous **Gaucher (GBA1)** call dropped | `rs397515515`→`rs439898` hom-alt Pathogenic, absent from `annotated_variants` |
| F19 | **Medium** | Active | "ensemble_pathogenic" auto-promotes pure in-silico findings to ★★ MODERATE | ~9k findings at evidence_level 2 (VUS/Conflicting/Benign) on in-silico alone |
| F20 | **Medium** | Active | No ClinVar review-star floor; 0-star "Pathogenic" surfaced | 5,688 of 30,472 (18.7 %) `clinvar_pathogenic` findings are 0-star |
| F21 | **Medium** | Active | Obsolete MONDO terms surfaced as the user's disease label | 67 "obsolete …" terms; TP53 → "obsolete Li-Fraumeni syndrome 1" on 235 variants |
| F22 | **Medium** | Active | gene→disease is gene-level, applied to every variant regardless of its pathogenicity | 2,632 BRCA2 variants all labeled "breast-ovarian cancer susceptibility 2" |
| F23 | **Medium** | Active | Only one arbitrary disease per gene survives (insertion-order `annots[0]`) | PIK3CA → "obsolete cerebral malformation" (16 dropped); LMNA → DCM only |
| F24 | **Medium** | Active | Ensemble votes count correlated meta-predictors (REVEL/MetaSVM/MetaLR + CADD) as independent | pairwise call concordance REVEL~MetaSVM 90 %; count spikes at 5 |
| F25 | **Medium** | Active | Missing predictors silently count as "not deleterious" against a fixed threshold of 3 | 10,522 (19.7 %) dbNSFP-covered variants have <3 voters → can never be flagged |
| F26 | **Medium** | Active | gnomAD AF=0 ("never observed") treated identically to observed-ultra-rare | 1,012 variants AF=0 → `ultra_rare_flag=1` |
| F27 | **Medium** | Active | MT mislabeled "novel" (gnomAD r2.1.1 has zero MT); no heteroplasmy model | 200 of 259 MT variants → "novel" |
| F28 | **Medium** | Active | Destructive crash recovery: prior good annotation deleted (own committed txn) before re-run | `_delete_all_annotations` commits before source detection / batch loop |
| F29 | **Medium** | Active | Per-source failure / unavailable-DB swallowed; run still reported `complete` | `_check_engine_available` bare `except`; errors never downgrade status |
| F30 | **Medium** | Active | dbSNP merge archive frozen at b151/2018 vs ClinVar 2026; no build field; per-sample provenance = vep only | `dbsnp_merges` MAX(build)=151; `database_versions` has no build column |
| F31 | **Low** | Active | `MutPred2` column 100 % NULL — loader maps `MutPred_score` not `MutPred2_score` | 0 non-null across all sampled regions; rendered as blank "MutPred2" in UI |
| F32 | **Low** | Active | dead gnomAD/dbNSFP **position fallback** (requires ref/alt the engine never has) | guard `if chrom and pos and ref and alt` always False |
| F33 | **Low** | Active | GWAS coverage bit never set; `CPIC_BIT == GENE_PHENOTYPE_BIT == 16` (collision) | MAX(`annotation_coverage`)=31; bit 5 never set |
| F34 | **Low** | LATENT | MT liftover produces wrong GRCh38 coords (UCSC hg19 chrM ≠ rCRS) | `263→None`, `750→748`, …; autosomes correct; columns currently 0 % populated |
| F35 | **Low** | LATENT | dbNSFP DB is GRCh38-coordinate while pipeline is GRCh37; position joins cross builds | `rs1801133` dbNSFP pos 11,796,321 vs pipeline 11,856,378 |
| F36 | **Low** | Active | 465 variants matched by no source are dropped with no `coverage=0` marker | raw 677,436 − annotated 676,971 = 465; 0 rows with `coverage IS NULL` |
| F37 | **Low** | LATENT | `classify_zygosity` can mark a biallelic genotype as carrying **two** distinct ALTs (palindrome/complement) | `classify_zygosity('CC','T','G')='hom_alt'` AND `('CC','T','C')` carried |
| F38 | **Low** | Active | PolyPhen threshold 0.453 ("possibly") inconsistent with sibling 0.909 → ~6 % flag inflation | lenient 23,642 vs strict 17,472 deleterious calls |
| F39 | **Low** | Active | Engine `fetchall()`s all raw variants; rare-finder `fetchall()`s all matches (O(N) memory) | fine for chips; OOM risk for WGS/WES on roadmap |

### 3.2 Theme A — The core defect: genotype-agnostic annotation (F1–F7)

**Root cause (F1).** `engine.run_annotation` reads only `rsid,chrom,pos,genotype,source` (`engine.py:838-847`); `_merge_annotations` (`engine.py:390-443`) builds rows from `rsid,chrom,pos,genotype` plus matched source columns; `_lookup_clinvar` (`engine.py:168-186`) discards the `ref`/`alt` that `ClinVarAnnotation` actually carries; `_UPSERT_COLUMNS` (`engine.py:467-519`) lists no `zygosity`/`ref`/`alt`. `grep` confirms `engine.py` references neither `classify_zygosity` nor `CARRIED_ZYGOSITIES`. **Empirical:** `zygosity`, `ref`, `alt` are NULL for 676,971/676,971 rows; the only distinct `zygosity` value is `None`.

**Over-call (F2/F3/F4/F5).** `run_all.py:280` calls `find_rare_variants(RareVariantFilter())` — all defaults (`include_novel=True`, `zygosity=None`). The only WHERE condition is `gnomad_af_global < 0.01 OR gnomad_af_global IS NULL` (`rare_variant_finder.py:277-283`); the optional zygosity filter (`:307-308`) is never applied — and could not work anyway because the column is NULL. One finding is emitted per matching variant:

| category | findings | actually carried (recomputed with the project's own `classify_zygosity`) |
|---|---|---|
| `clinvar_pathogenic` | 30,472 | ~13 (0.04 %); ~24,727 hom-ref; ~7,250 indel/no-call |
| `ensemble_pathogenic` | 9,188 | 23 (0.25 %); 9,125 hom-ref |
| `rare` | 20,254 | 208 (1.03 %); 90.6 % hom-ref |
| `rare_flag`=1 (annotation) | 37,346 | 229 (0.61 %); 91.1 % hom-ref |
| `novel` | 595,951 | n/a (no AF/ref/alt to test) |

**Suppression (F6).** `cancer.py:307`, `cardiovascular.py:325` add `zygosity.in_(['het','hom_alt'])` to SQL; `carrier_status.py:320` does `if row.zygosity != 'het': continue`. With `zygosity` NULL, SQL `NULL IN (...)` is unknown→no rows, and `None != 'het'` is True→skip. **Empirical:** cancer disease findings = 0 (only 4 PRS), cardiovascular = 0, carrier-status = 0. The 0 is an accident of NULL, not of carriage logic.

**Lost true positives (F7).** Reconstructing carriage from the engine's *stored* `clinvar_significance` + ClinVar ref/alt reproduces PR #315's advertised residuals exactly (cancer 4533→2 [SDHC `rs786202200`, NF1 `rs786203950`], cardiovascular 1435→1 [LDLR `rs879255105`], carrier 0). In the live table all three are **0** — the findings PR #315 was designed to *keep* are dropped. (Caveat, independently flagged: all three are hom-alt at ultra-rare AF — a classic strand/probe-artifact signature — so they may not be biologically real; that is a *separate* QC gap, see F37, not a mitigation.)

**Double-edged consequence.** The identical NULL-zygosity root cause yields **false positives** through `rare_variants` and **false negatives** (total suppression) through the carriage-gated clinical modules. Re-annotating to populate `zygosity` would flip the clinical modules back on — and simultaneously activate every LATENT strand/build bug below.

### 3.3 Theme B — Orphaned "fix" code (F1/F17/F18/F30)

A recurring pattern: carriage/normalization-aware code exists, is unit-tested, and is **wired to nothing**.

- `annotate_sample_clinvar` (carriage-aware, writes `zygosity`/`ref`/`alt`, passes `genotype_by_rsid`) — referenced only in `clinvar.py:13,19` (docstring) and tests. `git show 50124f5` (PR #315) touched `cancer.py`, `cardiovascular.py`, `zygosity.py`, `clinvar.py` + tests but **not `engine.py`**.
- `annotate_sample_dbsnp` + `lookup_merged_rsids` (rsid-merge resolution; the data exists — `dbsnp_merges` has 11,963,907 rows) — referenced only in `dbsnp.py` docstring + tests. `dbsnp_rsid_current` is NULL for 100 % of rows.
- This is *why the test suite is green while the live pipeline is broken*: the tests exercise the correct orphaned path, not the live engine. **Any validation strategy must test the live `run_annotation` path on realistic data** (see §4).

### 3.4 Theme C — Surfacing / over-calling beyond carriage (F8/F9/F12/F19/F20/F21/F22/F23)

- **F8 SRY-on-XX.** `infer_biological_sex(sample_1)='XX'`, used only to gate the Y-haplogroup walk (`ancestry.py:1565`) — never to gate annotation or findings. `find_rare_variants` and `findings.py` apply no chromosome/sex filter. Result: 1,631 chrY findings (1,628 from `--` no-calls) including 14 "Pathogenic SRY" on a female, ranked to the top by `evidence_level`.
- **F9 MECP2 indel on the dashboard.** The high-confidence card (`findings.py:250`, evidence ≥3) top-5 are MECP2 (Rett) `clinvar_pathogenic` with genotype `II` (an indel no-call sentinel) — unscoreable, surfaced as 4-star Pathogenic.
- **F12 "novel" mislabel.** `is_novel := gnomad_af_global is None` (`rare_variant_finder.py:155-158`). 91.4 % of variants have AF NULL (gnomAD exomes-only), so 595,951 common, catalogued dbSNP SNPs (99.6 % non-coding/low-impact) are badged "Novel (no gnomAD)".
- **F19 in-silico ★★ promotion.** `assign_clinvar_evidence_level` (`evidence.py:145-147`) returns MODERATE (★★) whenever `ensemble_pathogenic` is True and ClinVar is non-P/LP/absent. The project's own rubric reserves ★★ for *functional* evidence; ~9k VUS/Conflicting/Benign findings get ★★ on pure computation.
- **F20 no star floor.** 18.7 % of `clinvar_pathogenic` findings are 0-star (no assertion criteria). They are correctly down-ranked to evidence_level 2 but still carry the `clinvar_pathogenic` category label and inflate the count.
- **F21/F22/F23 gene-phenotype labels** — see Theme I.

### 3.5 Theme D — Allele / multi-allelic correctness (F10/F11/F26)

- **F10 (confirmed false-Pathogenic).** Live `engine._lookup_clinvar` calls `lookup_clinvar_by_rsids` without `genotype_by_rsid`, so `_pick_clinvar_row` returns `rows[0]` = highest review-stars regardless of carried allele. 94,696 rsids have >1 distinct significance. Concrete: `rs376405759` (FOXC1) has C>G Pathogenic(2★), C>T Likely-benign(1★), C>A Pathogenic(0★); the sample genotype is `TT` so the carried allele is C>T = *Likely benign*, but the engine stored `Pathogenic` and surfaced "FOXC1 rs376405759 — Pathogenic".
- **F11 dbNSFP arbitrary ALT.** `lookup_dbnsfp_by_rsids` (`dbnsfp.py:986-1023`) has no ALT predicate and `results[row.rsid]=…` keeps the last row (alphabetically-highest ALT under PK order). On a per-rsid recompute, `deleterious_count` differs by ALT for ~41 %, and `ensemble_pathogenic` **flips** for ~17 % of multi-allelic sites. Live example: `rs267608161` (genotype CC = hom-ref) stored `ensemble_pathogenic=1, cadd=27.3` from the non-carried T allele.
- **F26 AF=0.** `compute_rare_flags(0.0)=(True,True)`; AF=0 ("ALT never seen in gnomAD") is treated like an observed-ultra-rare allele (1,012 variants), conflating "monomorphic-reference" with "rare".

### 3.6 Theme E — Coverage / recall (F13/F16/F18/F32/F36)

- **F13 exomes-only ceiling.** `GNOMAD_VCF_URL = gnomad.exomes.r2.1.1` (`gnomad.py:53-58`). 8.63 % AF coverage; a differential check (2,000 "novel" variants) found 0 present in `gnomad_af.db` by *either* (chrom,pos) *or* rsid — a reference-data ceiling, not a lookup bug. (By contrast dbNSFP's 7.89 % is *appropriate* — it covers 97.85 % of missense, its by-design scope.)
- **F16 indels uncheckable.** `classify_zygosity` returns None for I/D genotypes and for multi-base ref/alt. 43.3 % of ClinVar P/LP records are indels (117,424); 7,230 are genotyped in this sample (22.6 % of genotyped P/LP). This includes CFTR F508del and ~75 % of pathogenic BRCA1/2 — *intrinsically* unresolvable on a chip, but currently surfaced ungated, and a **permanent false-negative** once the carriage gate is wired.
- **F18 rsid-merge recall.** 25 chip rsids are deprecated dbSNP IDs pointing to a *different* current rsid; 21 hit ClinVar under the current id (13 P/LP), e.g. **GBA1 `rs397515515`→`rs439898`** Pathogenic 2★ Gaucher, sample genotype `AA` = **hom-alt carried** — yet absent from `annotated_variants` entirely (one of the 465 dropped rows). A carried homozygous Gaucher variant is invisible to the user.
- **F32 dead position fallback.** The gnomAD/dbNSFP position fallbacks gate on `if chrom and pos and ref and alt`, but the engine never fetches ref/alt → the guard is always False → `lookup_gnomad_by_positions`/`lookup_dbnsfp_by_positions` never run. Only the VEP fallback (chrom,pos-only) is live. (Quantitatively negligible for this sample but it removes the *only* non-rsid rescue for merged/internal IDs, compounding F18.)
- **F36 silent drops.** 465 variants matched no source and are dropped (`if bitmask > 0`), with no `annotation_coverage=0` marker, so "processed-but-no-match" is indistinguishable from "never processed".

### 3.7 Theme F — Strand (F17/F37) — currently LATENT

Strand reconciliation lives only inside `classify_zygosity`, which the live engine never calls; `zygosity` is NULL, so **no surfaced finding is affected today**. The defects are real and will activate together on re-annotation:

- **Palindromic SNPs (A/T, C/G)** are taken at face value on the + strand (the reference-strand branch returns before any palindrome guard). `classify_zygosity('GG','C','G')='hom_alt'` but `('GG','G','C')='hom_ref'` — the call inverts with strand. 3,994 palindromic P/LP sites are genotyped here.
- **Unconditional reverse-strand complement fallback** is rationalized for 23andMe but applied to all vendors, including AncestryDNA, whose header declares "+ strand" — so a complement-only match is more likely a different allele than a reverse-strand encoding.
- **F37 double-carry invariant violation.** At a multi-allelic palindromic locus, the same genotype can be classified as *carried* for two distinct ALTs (e.g. `classify_zygosity('CC','T','G')='hom_alt'` via complement). A biallelic genotype cannot carry two distinct ALTs — this is a clean property-invariant to assert.
- `annotated_variants.strand` actually stores the **VEP gene/transcript** strand, not the chip allele strand (a naming/provenance hazard if ever read as the latter).

### 3.8 Theme G — Build / assembly / liftover (F34/F35) — mostly LATENT

The dominant rsid path is build-agnostic, so chip annotation is build-robust today. But:

- The sample **build is parsed then discarded** — `samples`/`sample_metadata` have no build column; a 23andMe **v3 (hg18/build-36)** upload would be silently treated as GRCh37 (VEP coord-fallback, liftover, VCF `##reference=GRCh37` header all build-blind).
- **dbNSFP DB is GRCh38-coordinate** (`rs1801133` at 11,796,321) while the pipeline is GRCh37 (11,856,378) — harmless now (position fallback dead, F32) but a landmine when ref/alt-bearing inputs (VCF/WGS) arrive.
- **MT liftover is wrong** — UCSC hg19 chrM is the old Yoruba sequence, not rCRS; `263→None`, `750→748` (−2), etc. Autosomes are correct. Currently dead (0 % of `chrom_grch38` populated; opt-in endpoint).
- No reference DB records a genome build, so runtime build-mismatch detection is impossible.

### 3.9 Theme H — In-silico ensemble methodology (F11/F19/F24/F25/F31/F38)

`count_deleterious` (`dbnsfp.py:265-276`) is an unweighted 3-of-5 vote over SIFT, PolyPhen-2, CADD, REVEL, MetaSVM:

- **F24 correlated votes.** REVEL and MetaSVM are meta-predictors trained on the component scores (MetaLR is MetaSVM's sibling; CADD shares features). Pairwise call concordance REVEL~MetaSVM = 90 %; the `deleterious_count` distribution spikes at 5 (12,874) exceeding count=3+4 combined — inconsistent with independent voting. "3 independent tools" is methodologically unsound.
- **F25 NULL-as-no-vote.** Missing predictors are skipped against a fixed threshold of 3; 19.7 % of dbNSFP-covered variants have <3 voters present and can *never* be flagged (variable-denominator bias toward benign). The sibling `evidence_conflict.count_deleterious_tools` already returns `(deleterious, total_assessed)` — the correct pattern is known but not used here.
- **F38 lenient PolyPhen.** Threshold 0.453 ("possibly damaging") vs the sibling's 0.909; ~6 % flag inflation and an intra-codebase inconsistency.
- **F31 MutPred2 dead column.** Loader maps `MutPred_score` (`dbnsfp.py:91,416`); dbNSFP 5.x distributes `MutPred2_score`, so the column is 100 % NULL — rendered as a permanently blank "MutPred2" field in the variant-detail UI. **The test fixtures use the same wrong column name**, so the suite never catches it.
- Multi-transcript scores collapse to "first-non-missing" independently for score and prediction (no MANE/canonical selection) — score and pred can come from different transcripts.

### 3.10 Theme I — Gene→phenotype mapping (F14/F21/F22/F23)

`engine._lookup_gene_phenotype` takes `annots[0]` (`engine.py:370-382`); `lookup_gene_phenotypes` has **no ORDER BY** (`mondo_hpo.py:672-679`), so "first = most relevant" is really MIN(id) = MONDO-TSV insertion order. Genes have up to 17 disease records; 1,229 have ≥2.

- **F14 inheritance mislabel (High).** One gene-wide inheritance value is stamped on every disease (`mondo_hpo.py:397-409`); the first-in-file value wins. BRCA1, BRCA2, LMNA, MSH2, MLH1, MSH6, HTT, VHL, LDLR are all labeled **"Autosomal recessive"** — every one is classic autosomal **dominant**. A real BRCA1 Pathogenic frameshift finding (`rs80359876`) shows "Autosomal recessive" in its detail panel, directly contradicting correct risk/reproductive framing.
- **F23 arbitrary single disease.** PIK3CA → "obsolete cerebral malformation" (16 dropped, incl. hereditary breast carcinoma, Cowden); LMNA → "dilated cardiomyopathy 1A" only (progeria, Emery-Dreifuss dropped).
- **F22 gene-level fanout.** Disease is attached to *every* variant in the gene with no pathogenicity gate — 2,632 BRCA2 variants (benign included) all carry "breast-ovarian cancer susceptibility 2".
- **F21 obsolete terms.** No "obsolete" filter; 67 obsolete-prefixed terms reach the UI, e.g. TP53 → "obsolete Li-Fraumeni syndrome 1" on 235 variants.
- *Containment (verified):* cancer/cardiovascular/carrier modules use curated panels and do **not** read these fields, so gating is not corrupted; the damage is in the variant-detail drawer.

### 3.11 Theme J — Concurrency / integrity / crash recovery (F28/F29/F39)

- **F28 destructive recovery.** `_delete_all_annotations` runs `annotated_variants.delete()` in its **own committed transaction** (`engine.py:127-134,854`) *before* source detection and the 68-batch loop. A crash/cancel/locked-DB after the delete leaves the table empty or a partial prefix, with the prior good run gone. (Mitigation: `raw_variants` is the source of truth, so it's regenerable; the loss is recompute time, and the staleness gate forces a re-run.)
- **F29 swallowed failures.** Per-source future failures are logged to `result.errors` but never downgrade status from `complete` (`huey_tasks.py:335-346`). Worse, `_check_engine_available` has a bare `except` that returns None without recording anything — a single >30 s lock or corrupt DB during the one-time pre-flight drops an entire source (all gnomAD or dbNSFP NULL) for the whole run, reported as success. Missing-due-to-failure is indistinguishable from genuinely-absent because the coverage bit just stays unset.
- **F39 unbounded memory.** Engine `fetchall()`s all raw variants; the rare-finder `fetchall()`s all matches and builds a dict-per-finding (655,865 here). Fine for chips, OOM risk for the WGS/WES roadmap.

### 3.12 Theme K — Reference-DB version skew & provenance (F30/F33/F35)

- **F30 skew.** `dbsnp_merges` is frozen at b151 (`version='20180207'`); ClinVar is `20260601` — 8 years; merges from b152–b156 are unresolvable. `database_versions` has **no genome_build / dbSNP-build column** and there is **no cross-source consistency check**. Per-sample provenance (`annotation_state`) records **only `vep_bundle_version`** — a stored finding cannot be tied to the ClinVar/gnomAD/dbNSFP snapshot that produced it. Re-annotation prompting watches **only ClinVar** (pre-check) and **only vep_bundle major** (staleness gate) — gnomAD/dbNSFP/dbSNP/gene-phenotype/CPIC/GWAS updates never prompt.
- **F33 bitmask.** `GENE_PHENOTYPE_BIT == CPIC_BIT == 0b010000` (collision; dormant because the engine never sets CPIC). The GWAS bit (32) is never set in the live `run_all` path (the GWAS-coverage updaters are wired only into on-demand API routes), so coverage telemetry reports 0 % GWAS. MAX(`annotation_coverage`)=31.

---

## 4. The validation strategy

The strategy is built around one principle the audit kept proving: **test the *live* `run_annotation` path on *realistic* data, and check carriage, not just presence.** The green test suite missed everything because it exercises orphaned correct code and uses fixtures with wrong column names.

The methods below are ordered by leverage. Each lists the flaws it catches and is reproducible.

### M1 — Carriage ground-truth audit (statistical-audit) — *catches F2–F7, F10, F11, F16, F18, F26*

The single highest-value check, and the one that exposed the core defect. For a fully-annotated sample, recompute carriage independently and assert plausibility.

- For every surfaced finding in `clinvar_pathogenic` / `ensemble_pathogenic` / `rare`: join `finding.rsid → raw_variants.genotype → source ref/alt` (ClinVar/gnomAD/dbNSFP) and run the project's own `classify_zygosity`.
- **Assert:** the carried fraction of "Pathogenic"/"rare"/"ensemble" findings is within a sane band (e.g. a chip person carries on the order of tens, not 30,000, P/LP alleles). Alarm threshold: any `clinvar_pathogenic` finding that is `hom_ref`.
- Emit a per-category carriage table (carried / hom-ref / undetermined). On the real sample this immediately reads `13 / 24,727 / 7,250` → fail.
- Productionize as a CI/QC gate over a golden sample and as a runtime QC metric.

### M2 — Synthetic truth-set fixtures (synthetic-data) — *catches F1, F3–F11, F16, F17, F37, and regressions on the fix*

Build small 23andMe and AncestryDNA files with **known** genotypes at **known** loci, then assert the resulting findings exactly match expected carriage. Cover one row per hazard class:

| Class | Construction | Expected |
|---|---|---|
| hom-ref at a ClinVar P/LP SNV | genotype = ref/ref | **no** pathogenic finding |
| het carrier | ref/alt | het carrier finding |
| hom-alt | alt/alt | hom-alt finding |
| reverse-strand het | complemented alleles | het (after strand resolution) |
| palindromic A/T hom | both pairs identical | flagged ambiguous / documented behavior |
| multi-allelic, carry ALT#2 | genotype = ALT#2 | significance of ALT#2, not the highest-star ALT |
| indel (I/D) at P/LP indel | `II`/`DI` | unscoreable, **not** surfaced as confident Pathogenic |
| merged rsid | old rsid; ClinVar under current | resolved & annotated |
| chrY variant on XX | `--`/`GG` on Y, sample XX | **no** Y finding |
| MT variant | MT genotype | MT-aware, not "novel" if common |

Negative controls (hom-ref ⇒ no pathogenic finding) are the most important; they would have failed on day one. Run them through the **actual `run_annotation` + `run_all`**, not `annotate_sample_clinvar`.

### M3 — Property-based invariants (property-invariant) — *catches F1, F3–F8, F16, F25, F33, F36, F37*

Assertions to run after any annotation, on any sample:

1. No finding in `{clinvar_pathogenic, ensemble_pathogenic, rare}` has `zygosity == 'hom_ref'`. *(Today: violated 30k+ times.)*
2. Every annotated SNV with a source ref/alt has non-NULL `zygosity`. *(Today: 0 % populated.)*
3. For distinct ALTs at one `(chrom,pos,ref)`, no biallelic genotype is `CARRIED` for both (the F37 double-carry invariant).
4. No finding on chrY/chrX-nonPAR contradicts inferred sex (no Y finding on XX).
5. `annotation_coverage` bit set ⇒ the corresponding source column is non-NULL (catches F33 and "claims coverage it doesn't have").
6. `count(raw_variants) == count(annotated_variants) + count(coverage=0 rows)` (forces an explicit no-match marker; catches F36).
7. A finding whose genotype is a no-call/indel sentinel may not be `evidence_level ≥ 3`.
8. `deleterious_count` denominator (voters present) recorded; flag requires `k`-of-*present*, not `k`-of-5 (catches F25).

### M4 — Differential vs a gold-standard annotator (differential/gold-standard) — *catches F10, F12, F13, F15, F16, F35, and quantifies overall precision/recall*

Convert the chip to a proper VCF by resolving `ref`/`alt` from the GRCh37 reference at each position (the step the pipeline skips), then annotate with an authoritative stack — `bcftools norm` (left-align/split multi-allelic) + VEP + ClinVar/gnomAD via `vcfanno`, or Hail. Compare:

- per-variant ClinVar significance (catches F10 multi-allelic mis-pick),
- gnomAD AF and popmax (catches F13 exomes-only, F15 global-AF),
- consequence/HGVS (catches the unconfirmed Ensembl-112-vs-GRCh37 HGVS sub-question),
- the **final carriage-gated finding set** → report precision/recall and a confusion matrix.

This is the definitive external check; everything else is internal consistency.

### M5 — Reference-data QA gates (reference-data) — *catches F13, F20, F21, F26, F27, F30, F31*

- **Build manifest:** add a genome-build field to every reference DB; assert all share the pipeline build at startup; assert dbNSFP coords match (catches F35) or are lifted.
- **ClinVar quality policy:** explicit minimum review-star floor for the `clinvar_pathogenic` *category* (or a visible low-confidence sub-tier); handle `|`/`,` compound CLNSIG; stop collapsing `/` silently.
- **Obsolete/term hygiene:** filter `obsolete *` MONDO terms; source inheritance per-disease from a curated map, not first-in-file.
- **Frequency source:** add gnomAD **genomes** + **MT** (v3.1) and use **popmax**; treat AF=0 distinctly from observed-rare; distinguish "absent from source" from "novel".
- **Column-mapping regression:** assert non-zero coverage of each in-silico score against a *real* dbNSFP header (would have caught MutPred2 = `MutPred2_score`); make fixtures use real column names.

### M6 — Coverage / recall reconciliation (statistical-audit) — *catches F12, F13, F18, F32, F33, F36*

After each run, emit and threshold: per-source hit-rate from the bitmask; raw-vs-annotated reconciliation with an explicit `coverage=0` bucket; rsid-merge resolution rate (how many chip rsids were deprecated and whether they were recovered); a "dead fallback" assertion that the gnomAD/dbNSFP position fallback either runs or is removed; "novel" defined as "absent from a *whole-genome* AF source", not "absent from an exome lookup".

### M7 — Failure-injection & integrity tests (regression) — *catches F28, F29, F39*

- Lock/remove a reference DB mid-run; assert the job ends **`partial`/`failed`**, not `complete`, and that `annotation_coverage` distinguishes "source failed" from "no match".
- Kill the worker after batch *k*; assert the **prior good annotation survives** (transactional swap / annotate-to-temp-then-rename instead of delete-first).
- Large-input memory test (synthetic WGS-scale) with `yield_per`/streaming.

### M8 — Golden end-to-end snapshot (regression-harness) — *catches regressions across all of the above*

Commit a small synthetic sample + a frozen reference subset + a **golden findings snapshot** (counts per category, the carriage table from M1, the top-N dashboard findings). CI runs the full live pipeline and diffs. This is what turns the one-off audit into a standing guardrail — and what would make the orphaned-code class of bug (F1/F17/F18) impossible to reintroduce, because the snapshot is produced by the live path.

### Method → flaw coverage matrix

| Method | Flaws covered |
|---|---|
| M1 Carriage audit | F2,F3,F4,F5,F6,F7,F10,F11,F16,F18,F26 |
| M2 Synthetic truth-set | F1,F3,F4,F5,F6,F7,F8,F9,F10,F11,F16,F17,F37 |
| M3 Property invariants | F1,F3,F4,F5,F6,F7,F8,F16,F25,F33,F36,F37 |
| M4 Differential gold-standard | F10,F12,F13,F15,F16,F35 |
| M5 Reference-data QA | F13,F20,F21,F26,F27,F30,F31,F35 |
| M6 Coverage reconciliation | F12,F13,F18,F32,F33,F36 |
| M7 Failure-injection | F28,F29,F39 |
| M8 Golden snapshot | all (regression backstop; also forces live-path testing → F1,F17,F18) |

---

## 5. Recommended remediation order (informed by the findings)

1. **Stop the bleeding (surfacing):** apply a carriage filter to `rare_variants` and a sex/chromosome filter to all finding generators — even before zygosity is computed, gate on a recomputed-from-genotype carriage so hom-ref/`--`/Y-on-XX stop reaching the dashboard. (F2,F3,F4,F5,F8,F9)
2. **Wire the existing fix into the live engine:** have `run_annotation` fetch source ref/alt and pass `genotype_by_rsid`, compute and persist `zygosity`/`ref`/`alt` (re-using `annotate_sample_clinvar`'s logic). Re-annotate existing samples. (F1,F6,F7,F10)
3. **Before that re-annotation ships, stand up M1+M2+M3** so the latent strand/build/multi-allelic bugs (F17,F37,F11) are caught the moment they activate.
4. Reference-data and methodology fixes (M5): star floor, inheritance source, obsolete-term filter, gnomAD genomes/popmax, MutPred2 mapping, ensemble denominator. (F13–F15,F19–F27,F31)
5. Integrity & provenance (M7 + build/version metadata). (F28–F30,F33–F36)

---

## Appendix A — Reproduction (read-only)

All probes were run against the real install with `?mode=ro` URIs and never invoked the pipeline. Representative commands:

```bash
DB=~/.yeliztli/samples/sample_1.db; REF=~/.yeliztli/reference.db
RO="file:$DB?mode=ro"

# F1: zygosity/ref/alt never populated
sqlite3 "$RO" "SELECT COUNT(*) total,
  SUM(zygosity IS NULL) z_null, SUM(ref IS NULL) ref_null, SUM(alt IS NULL) alt_null
  FROM annotated_variants;"        # -> 676971 | 676971 | 676971 | 676971

# F2/F3/F4/F5: findings dump by module/category
sqlite3 "$RO" "SELECT module, COUNT(*) FROM findings GROUP BY module ORDER BY 2 DESC;"
sqlite3 "$RO" "SELECT category, COUNT(*) FROM findings GROUP BY category ORDER BY 2 DESC;"

# F6: clinical modules suppressed
sqlite3 "$RO" "SELECT module,COUNT(*) FROM findings
  WHERE module IN ('cancer','cardiovascular','carrier_status') GROUP BY module;"

# per-source coverage (1=VEP 2=ClinVar 4=gnomAD 8=dbNSFP 16=genepheno/CPIC 32=GWAS)
sqlite3 "$RO" "SELECT SUM(annotation_coverage&1>0),SUM(annotation_coverage&2>0),
  SUM(annotation_coverage&4>0),SUM(annotation_coverage&8>0),
  SUM(annotation_coverage&16>0),SUM(annotation_coverage&32>0) FROM annotated_variants;"
```

```python
# M1: carriage ground-truth for surfaced P/LP findings (uses the project's own classifier)
import sqlite3
from backend.analysis.zygosity import classify_zygosity, CARRIED_ZYGOSITIES
samp = sqlite3.connect("file:.../sample_1.db?mode=ro", uri=True)
ref  = sqlite3.connect("file:.../reference.db?mode=ro", uri=True)
geno = {r[0]: r[1] for r in samp.execute("SELECT rsid,genotype FROM raw_variants")}
best = {}
for rsid, rf, al, st in ref.execute(
    "SELECT rsid,ref,alt,review_stars FROM clinvar_variants "
    "WHERE significance IN ('Pathogenic','Likely pathogenic') AND rsid IS NOT NULL"):
    st = st or 0
    if rsid not in best or st > best[rsid][2]: best[rsid] = (rf, al, st)
carried = homref = undet = matched = 0
for rsid,(rf,al,_) in best.items():
    g = geno.get(rsid)
    if g is None: continue
    matched += 1
    z = classify_zygosity(g, rf, al)
    carried += z in CARRIED_ZYGOSITIES; homref += (z=="hom_ref"); undet += z is None
# -> matched 32015 | carried 13 (0.04%) | hom_ref 24727 | undetermined 7275
```

## Appendix B — Key file references

- `backend/annotation/engine.py` — live engine; `_lookup_clinvar` (168), `_merge_annotations` (390), `_UPSERT_COLUMNS` (467), position-fallback guards (246, 321), raw SELECT (838).
- `backend/analysis/rare_variant_finder.py` — `RareVariantFilter` defaults (114), `is_novel` (155), WHERE builder (277), category assignment (419).
- `backend/analysis/zygosity.py` — `classify_zygosity` (88), `is_no_call` (36), `CARRIED_ZYGOSITIES` (74), complement/palindrome handling (142).
- `backend/annotation/clinvar.py` — orphaned `annotate_sample_clinvar` (833), `_pick_clinvar_row` (645), `lookup_clinvar_by_rsids` (666).
- `backend/annotation/dbnsfp.py` — `count_deleterious` (252), thresholds, `MutPred_score` mapping (91, 416), `lookup_dbnsfp_by_rsids` (986).
- `backend/annotation/gnomad.py` — `compute_rare_flags` (210), exomes URL (53), AF parse (332).
- `backend/annotation/mondo_hpo.py` — `lookup_gene_phenotypes` no-ORDER-BY (672), inheritance stamping (397).
- `backend/analysis/cancer.py` (307), `cardiovascular.py` (325), `carrier_status.py` (320) — carriage gates.
- `backend/tasks/huey_tasks.py` (208) — live path; `run_all.py` (280) — `find_rare_variants(RareVariantFilter())`.

---

*Findings verified read-only against the production install on 2026-06-07; no application code or sample data was modified.*
