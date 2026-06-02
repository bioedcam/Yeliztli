#!/usr/bin/env bash
# Phase 4 — ADMIXTURE filtering to single-ancestry training labels.
#
# Input:
#   $PANEL_DIR/ref_panel_all_autosomes.vcf.gz  (Phase 3)
#   $RAW_DIR/gnomad_meta_updated.tsv           (Phase 1)
#
# Output:
#   $ADMIX_DIR/ref_panel_pruned.{bed,bim,fam}  — LD-pruned PLINK files
#   $ADMIX_DIR/admix_K{7,12,20}.K{7,12,20}.s${ADMIXTURE_SEED}.Q — ancestry proportions (fastmixture naming)
#   $ADMIX_DIR/sample_map.txt                  — sample_id<TAB>population (Gnomix input)
#   $ADMIX_DIR/single_ancestry_samples.tsv     — filtered table
#   $ADMIX_DIR/excluded_admixed_samples.tsv    — audit log
#
# Plan §6.4: phase unchanged from v1.1. ADMIXTURE seed is locked
# (env.sh::ADMIXTURE_SEED) so re-running reproduces labels bit-for-bit.

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PHASE_NAME=04_admixture_filter
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

require plink2
require fastmixture
require python

require_file "$PANEL_DIR/ref_panel_all_autosomes.vcf.gz"
require_file "$RAW_DIR/gnomad_meta_updated.tsv"

phase_log "converting subset panel to LD-pruned PLINK"

cd "$ADMIX_DIR"

if [ ! -s ref_panel_pruned.bed ]; then
  plink2 --vcf "$PANEL_DIR/ref_panel_all_autosomes.vcf.gz" \
    --make-bed \
    --out ref_panel_plink \
    --set-all-var-ids '@:#:$r:$a' \
    --max-alleles 2

  plink2 --bfile ref_panel_plink \
    --indep-pairwise 50 10 0.1 \
    --out pruned_sites

  plink2 --bfile ref_panel_plink \
    --extract pruned_sites.prune.in \
    --make-bed \
    --out ref_panel_pruned
fi

phase_log "LD-pruned SNP count: $(wc -l < ref_panel_pruned.bim)"

for K in $ADMIXTURE_K_LIST; do
  # fastmixture writes <out>.K<k>.s<seed>.Q (e.g. admix_K7.K7.s42.Q), NOT
  # admix_K7.Q — match the real output name or this guard never fires.
  if [ -s "admix_K${K}.K${K}.s${ADMIXTURE_SEED}.Q" ]; then
    phase_log "fastmixture K=$K already complete, skipping"
    continue
  fi
  phase_log "running fastmixture K=$K seed=$ADMIXTURE_SEED"
  fastmixture \
    --bfile ref_panel_pruned \
    --K "$K" \
    --threads "$BCFTOOLS_THREADS" \
    --out "admix_K${K}" \
    --seed "$ADMIXTURE_SEED"
done

phase_log "filtering to single-ancestry individuals (>= ${SINGLE_ANCESTRY_THRESHOLD})"
python "$SCRIPT_DIR/04c_filter_single_ancestry.py" \
  --q-matrix "$ADMIX_DIR/admix_K7.K7.s${ADMIXTURE_SEED}.Q" \
  --fam "$ADMIX_DIR/ref_panel_pruned.fam" \
  --meta "$RAW_DIR/gnomad_meta_updated.tsv" \
  --threshold "$SINGLE_ANCESTRY_THRESHOLD" \
  --out-sample-map "$ADMIX_DIR/sample_map.txt" \
  --out-single-ancestry "$ADMIX_DIR/single_ancestry_samples.tsv" \
  --out-excluded "$ADMIX_DIR/excluded_admixed_samples.tsv"

phase_log "phase 4 complete: $(wc -l < sample_map.txt) single-ancestry training samples"
