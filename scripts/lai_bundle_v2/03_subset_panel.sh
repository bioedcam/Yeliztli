#!/usr/bin/env bash
# Phase 3 — Subset the gnomAD HGDP+1KG phased BCFs to the union site list.
#
# Input:
#   $RAW_DIR/hgdp1kgp_chr{N}.filtered.SNV_INDEL.phased.shapeit5.bcf (Phase 1)
#   $LIFTOVER_DIR/array_sites_grch38_regions.tsv                    (Phase 2)
#
# Output:
#   $PANEL_DIR/ref_panel_chr{N}.vcf.gz{,.tbi}    — per-chrom subset
#   $PANEL_DIR/ref_panel_all_autosomes.vcf.gz{,.tbi} — merged (for Phase 4)
#   $LOG_DIR/subset_counts.log                  — variant count audit
#
# Plan §6.4 phase 3 — same logic as v1.1, but the regions file now spans the
# ~840k union catalog instead of ~605k 23andMe v5 sites.

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PHASE_NAME=03_subset_panel
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

require bcftools
require_file "$LIFTOVER_DIR/array_sites_grch38_regions.tsv"

phase_log "subsetting panel to union site list (regions: $LIFTOVER_DIR/array_sites_grch38_regions.tsv)"

: > "$LOG_DIR/subset_counts.log"

cd "$PANEL_DIR"

for chr in $CHROMS; do
  bcf_in="$RAW_DIR/hgdp1kgp_chr${chr}.filtered.SNV_INDEL.phased.shapeit5.bcf"
  vcf_out="ref_panel_chr${chr}.vcf.gz"

  require_file "$bcf_in"

  if [ -s "$vcf_out" ] && [ -s "${vcf_out}.tbi" ]; then
    phase_log "chr${chr}: subset already present, skipping"
  else
    phase_log "chr${chr}: subsetting"
    bcftools view \
      -R "$LIFTOVER_DIR/array_sites_grch38_regions.tsv" \
      -m2 -M2 \
      -v snps \
      "$bcf_in" \
      -Oz -o "$vcf_out" \
      --threads "$BCFTOOLS_THREADS"
    bcftools index -t "$vcf_out"
  fi

  count=$(bcftools view -H "$vcf_out" | wc -l)
  printf 'chr%s\t%s\n' "$chr" "$count" >> "$LOG_DIR/subset_counts.log"
done

phase_log "per-chromosome counts:"
cat "$LOG_DIR/subset_counts.log" | tee -a "$LOG_DIR/${PHASE_NAME}.log"
phase_log "subset directory size: $(du -sh "$PANEL_DIR" | awk '{print $1}')"

# Merged file for ADMIXTURE.
merged="ref_panel_all_autosomes.vcf.gz"
if [ ! -s "$merged" ] || [ ! -s "${merged}.tbi" ]; then
  phase_log "merging per-chromosome subsets"
  # shellcheck disable=SC2086
  set -- ; for chr in $CHROMS; do set -- "$@" "ref_panel_chr${chr}.vcf.gz"; done
  bcftools concat "$@" -Oz -o "$merged"
  bcftools index -t "$merged"
fi

phase_log "phase 3 complete"
