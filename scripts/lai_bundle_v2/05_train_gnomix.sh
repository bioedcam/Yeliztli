#!/usr/bin/env bash
# Phase 5 — Train Gnomix per chromosome at array density.
#
# Input:
#   $PANEL_DIR/ref_panel_chr{N}.vcf.gz        (Phase 3)
#   $ADMIX_DIR/sample_map.txt                 (Phase 4)
#   $RAW_DIR/genetic_maps_gnomix/chr{N}.map  (Phase 1; TAB-delimited 3-col chrom/pos/cM for gnomix)
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

require conda  # gnomix runs in its own env (GNOMIX_ENV) via `conda run`
require_file "$ADMIX_DIR/sample_map.txt"
require_file "$GNOMIX_DIR_INSTALL/gnomix.py"
require_file "$GNOMIX_CONFIG"

cp "$ADMIX_DIR/sample_map.txt" "$GNOMIX_DIR/sample_map.txt"

cd "$GNOMIX_DIR"

for chr in $CHROMS; do
  panel_vcf="$PANEL_DIR/ref_panel_chr${chr}.vcf.gz"
  # gnomix wants a 3-col TAB map (chrom, pos, cM); that is genetic_maps_gnomix/chrN.map,
  # NOT the 4-col space-delimited genetic_maps_grch38/.../plink.*.GRCh38.map (Beagle's format).
  genetic_map="$RAW_DIR/genetic_maps_gnomix/chr${chr}.map"
  out_dir="output_chr${chr}"
  require_file "$panel_vcf"
  require_file "$genetic_map"

  if [ -d "$out_dir" ] && ls "$out_dir"/*.pkl >/dev/null 2>&1; then
    phase_log "chr${chr}: gnomix model present, skipping"
    continue
  fi

  phase_log "chr${chr}: training gnomix"
  # gnomix.py infers its mode SOLELY from positional arg count (see
  # ~/tools/gnomix/gnomix.py): len(sys.argv)==6 -> pre-trained/inference;
  # ==8 or ==9 -> train. TRAINING needs exactly 7 positional args in this
  # source order:
  #   query_file  output_basename  chr_nr  phase  genetic_map  reference_file  sample_map
  # In training the phased reference panel is BOTH query_file and reference_file.
  # The old 6-arg call gave len(sys.argv)==7 -> "Incorrect number of arguments"
  # + sys.exit(0): a SILENT no-op that set -e cannot catch (exit 0).
  # The 7-positional form (args 1-7) is the proven v1.1 production invocation
  # (phase=True, chr_nr="chr${chr}", reference=panel — confirmed against the
  # cluster bash_history training loop; phase=True ships the model's phasing
  # module for unphased query data).
  # 8th arg = config file (len(sys.argv)==9): gnomix otherwise reads ./config.yaml
  # relative to CWD, which is $GNOMIX_DIR (no config there) -> FileNotFoundError.
  # Passing $GNOMIX_CONFIG (absolute) makes it CWD-independent AND lets the SLURM
  # array cap n_cores per task.
  # gnomix runs in its own env ($GNOMIX_ENV) — it needs sklearn_crfsuite/xgboost
  # the lai_bundle env lacks — via `conda run` so the rest of the pipeline (this
  # script, run_rebuild.sh) can stay in lai_bundle. --no-capture-output streams to tee.
  conda run -n "$GNOMIX_ENV" --no-capture-output \
    python "$GNOMIX_DIR_INSTALL/gnomix.py" \
    "$panel_vcf" \
    "$out_dir" \
    "chr${chr}" \
    True \
    "$genetic_map" \
    "$panel_vcf" \
    "$GNOMIX_DIR/sample_map.txt" \
    "$GNOMIX_CONFIG" \
    2>&1 | tee "$LOG_DIR/gnomix_train_chr${chr}.log"
  # gnomix exits 0 even on the bad-argc usage path; fail loudly if that happens
  # so the orchestrator stops instead of "completing" with no model.
  if grep -q "Incorrect number of arguments" "$LOG_DIR/gnomix_train_chr${chr}.log"; then
    phase_log "chr${chr}: gnomix rejected its arguments (see log)" >&2
    exit 1
  fi
done

phase_log "phase 5 complete"
missing=0
for chr in $CHROMS; do
  if [ -d "output_chr${chr}" ] && ls "output_chr${chr}"/*.pkl >/dev/null 2>&1; then
    phase_log "chr${chr}: OK ($(du -sh "output_chr${chr}" | awk '{print $1}'))"
  else
    phase_log "chr${chr}: MISSING"
    missing=1
  fi
done
if [ "$missing" -ne 0 ]; then
  phase_log "phase 5 FAILED: one or more gnomix models missing (see MISSING above)" >&2
  exit 1
fi
