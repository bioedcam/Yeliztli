#!/usr/bin/env bash
# Phase 5 — Train Gnomix per chromosome at array density.
#
# Input:
#   $PANEL_DIR/ref_panel_chr{N}.vcf.gz        (Phase 3)
#   $ADMIX_DIR/sample_map.txt                 (Phase 4)
#   $RAW_DIR/genetic_maps_grch38/plink.chr{N}.GRCh38.map (Phase 1)
#   $GNOMIX_DIR_INSTALL/gnomix.py             (cloned from AI-sandbox/gnomix)
#
# Output:
#   $GNOMIX_DIR/output_chr{N}/                — pickled XGBoost models + config
#   $LOG_DIR/gnomix_train_chr{N}.log          — per-chrom training log
#
# Plan §6.4: phase unchanged from v1.1; models retrain against the larger
# window count (~30% bigger total). Bio-validator validates per-window
# accuracy ≥0.88 mean before publication.

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PHASE_NAME=05_train_gnomix
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

require python
require_file "$ADMIX_DIR/sample_map.txt"
require_file "$GNOMIX_DIR_INSTALL/gnomix.py"

cp "$ADMIX_DIR/sample_map.txt" "$GNOMIX_DIR/sample_map.txt"

cd "$GNOMIX_DIR"

for chr in $CHROMS; do
  panel_vcf="$PANEL_DIR/ref_panel_chr${chr}.vcf.gz"
  genetic_map="$RAW_DIR/genetic_maps_grch38/plink.chr${chr}.GRCh38.map"
  out_dir="output_chr${chr}"
  require_file "$panel_vcf"
  require_file "$genetic_map"

  if [ -d "$out_dir" ] && ls "$out_dir"/*.pkl >/dev/null 2>&1; then
    phase_log "chr${chr}: gnomix model present, skipping"
    continue
  fi

  phase_log "chr${chr}: training gnomix"
  python "$GNOMIX_DIR_INSTALL/gnomix.py" \
    "$panel_vcf" \
    sample_map.txt \
    "$out_dir/" \
    "chr${chr}" \
    False \
    "$genetic_map" \
    2>&1 | tee "$LOG_DIR/gnomix_train_chr${chr}.log"
done

phase_log "phase 5 complete"
for chr in $CHROMS; do
  if [ -d "output_chr${chr}" ] && ls "output_chr${chr}"/*.pkl >/dev/null 2>&1; then
    phase_log "chr${chr}: OK ($(du -sh "output_chr${chr}" | awk '{print $1}'))"
  else
    phase_log "chr${chr}: MISSING"
  fi
done
