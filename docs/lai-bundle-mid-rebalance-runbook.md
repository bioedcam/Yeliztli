# LAI Bundle — MID Re-balance Rebuild Runbook

How to fix the residual **MID (Middle-Eastern) misclassification** in a future
`lai_bundle v2.0.0` rebuild, and how to **execute, stage, and send it live**.

This is a focused follow-up to the 2026-06-04 EUR-fix rebuild (CHANGELOG
`[Unreleased] → Fixed`; PRs #299/#302). It assumes the general procedure in
[`lai-bundle-release-runbook.md`](./lai-bundle-release-runbook.md) and only
documents what is *different* for the MID re-balance, plus the gotchas that cost
real time the first time around.

---

## 1. The problem

The 2026-06-04 rebuild fixed the European release-blocker (held-out Iberian
HG01502 went 0.3% → 96% EUR) and 6 of 7 superpopulations classify perfectly.
The **held-out per-superpopulation inference check** (`06f_*`, §8 of the release
runbook) surfaced one residual:

```
AFR 5/5 (0.998)  AMR 5/5 (0.895)  CSA 5/5 (0.955)  EAS 5/5 (0.981)
EUR 5/5 (0.985)  OCE 5/5 (0.965)  MID 2/5 (mean self-frac 0.384)     → 32/35
```

All three MID misses go to **EUR** (HGDP00567 / HGDP00640 / HGDP01268 →
EUR 0.40–0.50; even the misses carry ~0.38 MID).

### Root cause (same class as the original EUR=3 bug, milder)

MID is continentally **intermediate** (between EUR, AFR and CSA) and the training
panel is **imbalanced**: MID has 152 founders vs EUR's 738. The per-region
composition *gate* (`--min-per-region`, default 20) only enforces a **floor** —
it guarantees MID is *present*, not that it is *competitive*. With ~5× more EUR
founders in a genetically-adjacent region, gnomix's decision boundary favours EUR
and pulls borderline MID windows across.

> The build-time floor prevents the catastrophic EUR=3 case; **class balance** is
> the separate lever for intermediate groups, and the **held-out per-superpop
> inference check is the only thing that measures it** — the mean per-window
> accuracy (`06e`, 0.90+) is blind to it, exactly as it was blind to EUR=3.

Current full-panel composition (post-`04c`, before hold-out), for reference when
choosing a cap:

```
AFR 985   EAS 810   CSA 762   EUR 743   AMR 238   MID 157   OCE 30
```

---

## 2. Fix options (ranked)

### A. `--per-region-cap` — class re-balance  ★ recommended (cheap, supported)

Cap the large classes so MID/AMR/OCE are not swamped. Already wired end-to-end
(`env.sh::PER_REGION_CAP` → `04c_filter_single_ancestry.py --per-region-cap`),
so this is a **one-knob change + retrain**, no new code.

- Suggested first try: **`PER_REGION_CAP=250`**. That caps EUR/AFR/EAS/CSA down
  to 250 each and leaves AMR (238), MID (157), OCE (30) untouched — so the panel
  becomes ~250/250/250/250/238/157/30 instead of 985/810/762/743/238/157/30.
  MID's relative weight roughly triples.
- Trade-off: the big classes lose training data. They currently classify 5/5
  with ~0.95–1.00 self-fraction, i.e. they have **headroom**; 250 founders is
  still ample for distinct continental groups (v1.1 trained fine at this scale).
  The held-out gate is the arbiter — if a big class drops below 5/5, raise the
  cap (e.g. 350) and re-validate.
- The selection is seeded (`--seed`, default 42) so the cap is reproducible.

### B. More MID training samples

HGDP+1KG carries only ~150–160 MID individuals total, so the panel already holds
nearly all of them — **limited upside** from the existing source. A PGP/other
harvest of additional Middle-Eastern genomes (Bedouin / Druze / Palestinian /
Mozabite / etc., GRCh37, sentinel-validated like the AncestryDNA harvest) could
add real MID founders, but it is a multi-day data-acquisition effort. Pursue only
if (A) alone does not clear the gate.

### C. gnomix simulation / class weighting

Increase simulated admixed individuals seeded from MID founders, or weight the
smoother toward minority classes. This is a gnomix-config / source change
(more invasive, harder to validate, touches the shared `gnomix` env) — **defer**
unless A + B are insufficient.

**Recommendation:** do **A** first, re-validate with the held-out per-superpop
gate, and only escalate to B/C if MID still misses. Target: **MID ≥ 4/5** with
no regression on the other six (each must stay 5/5).

---

## 3. Execute the rebuild (on the cluster, `ssh two`)

Prereqs (all already in place from the EUR-fix rebuild):

- Cluster work tree `~/lai_bundle_v2/` with phases 00–04 populated and the
  **seed-locked fastmixture Q** (`04_admixture_filtering/admix_K7.K7.s42.Q`) —
  the ~10 h ADMIXTURE step does **not** rerun (the SNP panel is unchanged).
- Conda envs: `lai_bundle` (bcftools/plink2/fastmixture/pysam), `gnomix`
  (xgboost/sklearn_crfsuite), `gi_val` (py3.12 + backend deps, for §3.5
  validation). `~/tools/gnomix/`, `~/tools/beagle.jar`.
- Repo scripts synced: `rsync -a scripts/lai_bundle_v2/ two:lai_bundle_v2/scripts/`.

### 3.1 Re-run phase 04 with the cap (seconds — fastmixture is skipped)

```bash
ssh two 'bash -lc "
  source ~/miniconda3/etc/profile.d/conda.sh; conda activate lai_bundle
  cd ~/lai_bundle_v2
  PER_REGION_CAP=250 bash scripts/04_admixture_filter.sh
  echo === new panel ===; cut -f2 04_admixture_filtering/sample_map.txt | sort | uniq -c
"'
```

Confirm every target superpopulation is present and the composition gate passes
(exit 0). With `PER_REGION_CAP=250` expect ~250/250/250/250/238/157/30.

### 3.2 Build the held-out validation split (before training)

Hold a few samples per superpopulation **out** of the gnomix founders so they are
a genuine inference test (they stay in the phasing panel for fixture extraction):

```bash
ssh two 'bash -lc "
  source ~/miniconda3/etc/profile.d/conda.sh; conda activate gi_val
  cd ~/lai_bundle_v2
  python scripts/06f_select_heldout.py \
    --sample-map 04_admixture_filtering/sample_map.txt \
    --n 5 --seed 42 --force HG01502:EUR \
    --out-full-backup 04_admixture_filtering/sample_map.full.txt \
    --out-training 04_admixture_filtering/sample_map.txt \
    --out-heldout 06_validation/held_out_validation.tsv \
    --min-per-region 20
"'
```

> Note: with `--n 5` and OCE capped at 30, OCE training drops to 25 (still ≥ 20).
> If you raise the hold-out count, keep every training region ≥ `--min-per-region`.

### 3.3 Retrain gnomix (phase 05, SLURM array)

Clear the stale models so all 22 chromosomes retrain on the re-balanced panel,
then submit the array + finish. The array sbatch already carries the fixes from
the EUR rebuild — **`--no-requeue`, `--mem=64G`, and the read-only
`$ADMIX_DIR/sample_map.txt`** (no shared-path `cp` race, BUG E):

```bash
ssh two 'bash -lc "
  cd ~/lai_bundle_v2
  rm -rf 05_gnomix_training/output_chr* logs/gnomix_train_chr*.log 07_final_bundle/* \
         genomeinsight_lai_bundle_v2.0.0.tar.gz*
  export WORKDIR=\$HOME/lai_bundle_v2 LAI_SCRIPTS_DIR=\$HOME/lai_bundle_v2/scripts \
         G1K_PED=\$HOME/lai_bundle_v2/06_validation/20130606_g1k.ped \
         UNION_CATALOG_TSV=\$HOME/lai_bundle_v2/00_raw_downloads/union_sites.tsv
  jid=\$(sbatch --parsable --export=ALL --partition=gpu --chdir=\$HOME/lai_bundle_v2/logs \
        --array=1-22%8 scripts/slurm/05_train_gnomix.sbatch)
  echo gnomix array=\$jid
"'
```

> ⚠️ **BUG F (gnomix post-train inference hang).** gnomix is invoked with the
> full 4091-sample panel as its query, so *after* it saves
> `output_chrN/models/model_chm_chrN/model_chm_chrN.pkl` it runs a post-train
> inference that re-phases all 4091 query haplotypes and can **hang for hours**
> (`Phasing individual N/4091`). The saved `.pkl` is everything the bundle needs
> (07b re-exports it; the inference output is unused). Two ways to handle it:
>
> 1. **Harvest** (what the EUR rebuild did): once a chromosome's `.pkl` exists
>    **and** its `logs/gnomix_train_chrN.log` shows `Estimated val accuracy:`,
>    `scancel` that array task to free the slot; when all 22 `.pkl` exist,
>    `scancel` the array and run `finish` directly (no `afterok`). A poll loop
>    that does this is the safest unattended approach.
> 2. **Proper fix (preferred for this rebuild):** pass gnomix a **1-sample
>    query** instead of the whole panel (training uses `reference` + `sample_map`
>    only, so the model is identical, but the post-train inference becomes
>    trivial). Keep `phase=True` so the model still ships its phasing module for
>    real unphased AncestryDNA uploads. See the BUG-F note at the top of
>    `scripts/lai_bundle_v2/05_train_gnomix.sh`. **Validate** the minimal-query
>    model is byte-identical to a harvested one on one chromosome before trusting
>    it for all 22.

### 3.4 Finish (phase 06 + 07 → assemble the bundle)

Phase 06 reuses the existing trio/Beagle phasing outputs (model-independent) and
re-scores `06e` from the new logs; phase 07 re-exports the 22 models and tars:

```bash
ssh two 'bash -lc "
  cd ~/lai_bundle_v2
  export WORKDIR=\$HOME/lai_bundle_v2 LAI_SCRIPTS_DIR=\$HOME/lai_bundle_v2/scripts \
         G1K_PED=\$HOME/lai_bundle_v2/06_validation/20130606_g1k.ped \
         UNION_CATALOG_TSV=\$HOME/lai_bundle_v2/00_raw_downloads/union_sites.tsv
  sbatch --export=ALL --partition=gpu --chdir=\$HOME/lai_bundle_v2/logs scripts/slurm/finish.sbatch
"'
# When done, record the new asset identity:
ssh two 'cd ~/lai_bundle_v2 && sha256sum genomeinsight_lai_bundle_v2.0.0.tar.gz && stat -c %s genomeinsight_lai_bundle_v2.0.0.tar.gz'
```

Sanity-check `07_final_bundle/metadata.json`: `accuracy_per_window_mean`, and a
chr1 model's `population_order` should still list all 7 ancestries (`A=7`).

### 3.5 Validate — held-out per-superpopulation inference (the gate)

Run each held-out sample through the **production** `run_lai_analysis` against the
**assembled** bundle. Build the fixtures, then run the SLURM array:

```bash
ssh two 'bash -lc "
  source ~/miniconda3/etc/profile.d/conda.sh; conda activate gi_val
  cd ~/lai_bundle_v2
  # rsync the latest backend first if app code changed:  (from a workstation)
  #   rsync -a backend pyproject.toml two:GenomeInsight_val/
  python scripts/extract_heldout_fixtures.py        # -> 06_validation/heldout_fixtures/
  rm -f 06_validation/heldout_results/*.json
  sbatch --chdir=\$HOME/lai_bundle_v2/logs scripts/slurm/heldout_val.sbatch   # array 1-35
"'
# When the array drains, aggregate:
ssh two 'bash -lc "source ~/miniconda3/etc/profile.d/conda.sh; conda run -n gi_val python ~/lai_bundle_v2/aggregate_heldout.py"'
```

**Pass criteria:** MID ≥ 4/5 (ideally 5/5) **and** AFR/AMR/CSA/EAS/EUR/OCE all
still 5/5. If a previously-perfect class regresses, the cap is too aggressive —
raise `PER_REGION_CAP` and rebuild from §3.1.

> ⚠️ **Validation is NFS-slow.** Each task does 22-chromosome Beagle phasing
> against the 4091-sample panel read from NFS at 8 cores/task ≈ ~85 min; all 35
> ≈ ~4 h. Either let it run unattended, or validate a representative subset
> (e.g. all 5 MID + 2 each of the rest) first for a fast read, then the full set.
> (Locally it is CPU-bound on a 24-thread/13 GB box: ~6 min/sample but only
> ~2 concurrent — the cluster is better for the full set, NFS notwithstanding.)

---

## 4. Stage + send live (publish)

Only after §3.5 passes. The published `lai-bundle-v2.0.0` release is **already
public**, so this *replaces* the asset and re-points the manifest.

### 4.1 Bump every sha/size pin (cross-cutting — grep, don't guess)

A registry sha/size change touches **five** files. Update all of them to the new
`<SHA>` / `<SIZE>` / `<DATE>`, then grep to prove none are missed:

| File | What |
|---|---|
| `bundles/manifest.json` | `bundles.lai_bundle` `sha256` / `size_bytes` / `build_date` |
| `backend/db/database_registry.py` | `DATABASES["lai_bundle"]` `sha256` / `expected_size_bytes` |
| `tests/backend/test_manifest.py` | `LAI_BUNDLE_SHA256` / `LAI_BUNDLE_SIZE_BYTES` + the `build_date` pin in `TestRepoManifest` |
| `tests/backend/test_lai_bundle_registry.py` | `test_lai_bundle_metadata` `expected_size_bytes` + `sha256` |
| `tests/backend/test_database_registry_lai.py` | `LAI_V2_0_0_SHA256` |

```bash
# Must return only doc/comment hits, never an un-bumped pin:
grep -rn "<OLD_SHA_PREFIX>\|<OLD_SIZE>" --include=*.py --include=*.json .
# Run the affected files locally BEFORE pushing (registry change = cross-cutting):
conda run -n GI python -m pytest tests/backend/test_manifest.py \
  tests/backend/test_database_registry_lai.py tests/backend/test_lai_bundle_registry.py \
  tests/backend/test_lai.py tests/backend/test_lai_bundle_v2_scripts.py -q
```

> 🩹 Lesson from the EUR rebuild: a *targeted* local run missed two of these five
> pins and CI went red. This change is **cross-cutting** (CLAUDE.md SOP #8) —
> sweep all five + grep before pushing.

### 4.2 PR → CI green → merge (owner account)

```bash
git checkout -b fix-lai-mid-rebalance && git add -A && git commit -m "fix(lai): re-balance MID (--per-region-cap) + republish v2.0.0"
# bioedca is READ-ONLY (push/merge 403). Use owner bioedcam, and because the
# active account FLIPS BACK between shells, switch + act in ONE command:
gh auth switch --user bioedcam && git push -u origin fix-lai-mid-rebalance
gh auth switch --user bioedcam && gh pr create --base main --title "..." --body "..."
# wait for full CI (backend ×3 + lint + frontend + docker + smoke + CodeRabbit; E2E skips on PRs):
gh pr checks <PR>
gh auth switch --user bioedcam && gh pr merge <PR> --squash --delete-branch
```

### 4.3 Replace the public asset + verify + nightly

```bash
# replace the asset on the already-public release:
gh auth switch --user bioedcam && gh release upload lai-bundle-v2.0.0 \
  /path/to/genomeinsight_lai_bundle_v2.0.0.tar.gz --clobber
# verify the published asset matches manifest@main:
gh auth switch --user bioedcam && gh workflow run bundle-release.yml \
  -f release_tag=lai-bundle-v2.0.0 -f bundle_key=lai_bundle
gh run watch "$(gh run list --workflow=bundle-release.yml -L1 --json databaseId --jq '.[0].databaseId')" --exit-status
# re-run the nightly slow-tier (LAI EUR regression test on the dense fixture):
gh auth switch --user bioedcam && gh workflow run nightly.yml
gh run watch "$(gh run list --workflow=nightly.yml -L1 --json databaseId --jq '.[0].databaseId')" --exit-status
gh auth switch --user bioedca   # restore the read-only default when done
```

> Ordering matters: **merge the manifest PR first** (so `main` carries the new
> sha), **then** upload the asset, **then** verify — otherwise the verify step
> sees a manifest/asset sha mismatch. The brief window between merge and upload
> is acceptable (the prior state was already broken).

### 4.4 (Optional) add a MID fixture to the nightly

The nightly currently gates only EUR (`tests/fixtures/heldout_eur_HG01502.adna.txt.gz`,
top==EUR & EUR≥0.85). To guard MID going forward, commit a dense held-out MID
fixture (public HGDP, e.g. one of the held-out MID samples extracted by
`extract_heldout_fixtures.py`) and add a parallel `top==MID` assertion in
`tests/backend/test_lai.py::TestRealBundleLAIAccuracy`.

---

## 5. Gotchas checklist (hard-won)

- **Accounts:** `bioedca` is read-only (push/merge/publish 403); `bioedcam` is
  owner. The active account **flips back to `bioedca` between shells
  inconsistently** — wrap every owner `gh`/`git push` as
  `gh auth switch --user bioedcam && <op>` in one command. Restore `bioedca` at
  the end.
- **Cross-cutting pins:** five files pin the bundle sha/size (§4.1). Grep + run
  all five locally before pushing.
- **BUG E (fixed):** phase-05 array tasks must not `cp` `sample_map` to a shared
  `$GNOMIX_DIR` path (NFS `EEXIST` → `set -e` death → requeue loop). Already
  fixed (read from `$ADMIX_DIR`; `--no-requeue`).
- **BUG F (fixed-with-workaround):** gnomix post-train inference on the full
  panel hangs → harvest models, or apply the minimal-query fix (§3.3) and
  validate it.
- **Validation is the arbiter, not the mean accuracy:** `06e` (per-window mean)
  is blind to per-population balance — always run the `06f` held-out
  per-superpopulation inference check before publishing.
- **Cluster validation is NFS-slow** (~4 h for 35); subset for a fast read.
- **fastmixture Q is seed-locked** (`ADMIXTURE_SEED=42`) and reusable — phase 04
  re-runs in seconds; only phase 05 (training) is the long pole.
