#!/usr/bin/env bash
# Phase 6c — Leave-one-out Beagle phasing for each trio child.
# Source the env, then for each child:
#   1. Strip phasing from child genotypes
#   2. Build a reference panel without the child's family
#   3. Run Beagle
#
# Plan §6.4 phase 6c — logic unchanged from v1.1.

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PHASE_NAME=06c_beagle_loo
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

require bcftools
require java
require_file "$BEAGLE_JAR"
require_file "$VALIDATION_DIR/trio_pedigree.tsv"

cd "$VALIDATION_DIR"

# Family exclusion files
awk -F'\t' 'NR>1 {print $1 > "trio_family_"$1".txt"; print $2 >> "trio_family_"$1".txt"; print $3 >> "trio_family_"$1".txt"}' \
  trio_pedigree.tsv

while IFS=$'\t' read -r child father mother pop; do
  [ "$child" = "child" ] && continue
  exclude_file="trio_family_${child}.txt"
  for chr in $CHROMS; do
    beagle_out="child_beagle_phased_${child}_chr${chr}.vcf.gz"
    [ -s "$beagle_out" ] && continue

    panel_in="$PANEL_DIR/ref_panel_chr${chr}.vcf.gz"
    require_file "$panel_in"

    # Extract child as unphased single-sample.
    bcftools view -s "$child" "$panel_in" \
      | sed 's/|/\//g' \
      | bcftools view -Oz -o "child_unphased_${child}_chr${chr}.vcf.gz"
    bcftools index -t "child_unphased_${child}_chr${chr}.vcf.gz"

    # Reference panel without child's family.
    ref_loo="ref_without_family_${child}_chr${chr}.vcf.gz"
    if [ ! -s "$ref_loo" ]; then
      bcftools view -S "^${exclude_file}" "$panel_in" -Oz -o "$ref_loo"
      bcftools index -t "$ref_loo"
    fi

    # Beagle wants the 4-col plink map whose chrom field matches the panel's
    # chr-prefixed contigs (gnomAD HGDP+1KG shapeit5 = 'chr22'), i.e. the
    # chr_in_chrom_field variant 'plink.chrchrN.GRCh38.map' (filename carries a
    # literal double 'chr'). This is the SAME file the runtime loads from the
    # shipped bundle (backend/analysis/lai_runner.py: genetic_maps/plink.chrchrN.GRCh38.map).
    # The old flat path genetic_maps_grch38/plink.chrN.GRCh38.map never existed.
    genetic_map="$RAW_DIR/genetic_maps_grch38/chr_in_chrom_field/plink.chrchr${chr}.GRCh38.map"
    require_file "$genetic_map"

    phase_log "Beagle: ${child} chr${chr}"
    java -Xmx"$BEAGLE_XMX" -jar "$BEAGLE_JAR" \
      gt="child_unphased_${child}_chr${chr}.vcf.gz" \
      ref="$ref_loo" \
      map="$genetic_map" \
      out="child_beagle_phased_${child}_chr${chr}" \
      impute=false \
      2>&1 | tee -a "$LOG_DIR/beagle_${child}_chr${chr}.log"
  done
done < <(tail -n +2 trio_pedigree.tsv)

phase_log "phase 6c complete"
