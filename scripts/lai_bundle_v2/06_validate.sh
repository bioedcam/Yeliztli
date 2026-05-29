#!/usr/bin/env bash
# Phase 6 — Validation. Runs the v1.1 phasing + LAI accuracy harness against
# the v2 models. Plan §6.4 phase unchanged; targets:
#   - mean per-window LAI accuracy ≥ 0.88
#   - phasing switch error rate ≤ 0.0566
# Bio-validator gates publication on these targets (Plan §6.4 last paragraph).
#
# This script orchestrates the validation Python scripts; the heavy lifting
# lives in 06a/06b/06d/06e_*.py. Each helper is independently re-runnable.

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PHASE_NAME=06_validate
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

require python
require bcftools
require java
require_file "$BEAGLE_JAR"

cd "$VALIDATION_DIR"

phase_log "identifying trio children from metadata"
python "$SCRIPT_DIR/06a_identify_trios.py" \
  --meta "$RAW_DIR/gnomad_meta_updated.tsv" \
  --single-ancestry "$ADMIX_DIR/single_ancestry_samples.tsv" \
  --out-trios "$VALIDATION_DIR/trio_children.txt" \
  --out-pedigree "$VALIDATION_DIR/trio_pedigree.tsv"

phase_log "extracting trio samples per chromosome"
# Build the union sample list (children + parents).
awk -F'\t' 'NR>1 {print $1; print $2; print $3}' "$VALIDATION_DIR/trio_pedigree.tsv" \
  | sort -u > "$VALIDATION_DIR/trio_samples_all.txt"

for chr in $CHROMS; do
  out="trio_truth_chr${chr}.vcf.gz"
  if [ -s "$out" ] && [ -s "${out}.tbi" ]; then continue; fi
  bcftools view \
    -S "$VALIDATION_DIR/trio_samples_all.txt" \
    "$PANEL_DIR/ref_panel_chr${chr}.vcf.gz" \
    -Oz -o "$out"
  bcftools index -t "$out"
done

phase_log "truth-phasing via Mendelian inheritance"
python "$SCRIPT_DIR/06b_mendelian_phasing.py" \
  --pedigree "$VALIDATION_DIR/trio_pedigree.tsv" \
  --in-dir "$VALIDATION_DIR" \
  --chroms "$CHROMS"

phase_log "leave-one-out Beagle phasing"
bash "$SCRIPT_DIR/06c_beagle_loo_phasing.sh"

phase_log "measuring switch error rate"
python "$SCRIPT_DIR/06d_phasing_accuracy.py" \
  --validation-dir "$VALIDATION_DIR" \
  --out-report "$VALIDATION_DIR/phasing_accuracy_report.json"

phase_log "scoring LAI accuracy against held-out single-ancestry samples"
python "$SCRIPT_DIR/06e_lai_accuracy.py" \
  --gnomix-dir "$GNOMIX_DIR" \
  --validation-dir "$VALIDATION_DIR" \
  --single-ancestry "$ADMIX_DIR/single_ancestry_samples.tsv" \
  --out-report "$VALIDATION_DIR/lai_accuracy_report.json"

phase_log "phase 6 complete — bio-validator: confirm both accuracy reports clear targets before phase 7"
