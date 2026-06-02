#!/usr/bin/env bash
# Phase 2 — Build the GRCh37 → GRCh38 site map for subsetting.
#
# v1.1 fed this phase from the 23andMe v5 vep_input.vcf (~605k sites).
# v2.0.0 feeds it from the union catalog produced by the VEP rebuild
# (Plan §6.4 phase 2). The union TSV (rsid<TAB>chrom<TAB>pos in GRCh37)
# is the single source of truth that both bundle builds consume.
#
# Inputs:
#   $UNION_CATALOG_TSV — required. Produced by
#     `python scripts/generate_vep_input.py --rsid-catalog union_sites.tsv ...`
#     and stored alongside the rebuild log (Plan §5.3 step 3.1).
#
# Outputs:
#   $SITES_DIR/array_sites_grch37.tsv      — copy of input columns chr<TAB>pos<TAB>rsid
#   $SITES_DIR/array_sites_grch37.bed      — BED for liftover (chr-prefixed, 0-based)
#   $LIFTOVER_DIR/array_sites_grch38.bed   — lifted BED
#   $LIFTOVER_DIR/array_sites_grch38_regions.tsv — bcftools -R regions file
#       (1-based tab-delimited CHROM/BEG/END; MUST NOT use a .bed suffix, or
#        bcftools -R parses it as 0-based half-open and every 'pos pos' line
#        becomes a zero-width interval matching nothing -> 0 variants/chrom)
#   $LIFTOVER_DIR/rsid_to_grch38.tsv       — runtime liftover lookup
#   $LIFTOVER_DIR/hg19ToHg38.over.chain.gz — chain file (cached)

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PHASE_NAME=02_prepare_sites
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

require CrossMap
require wget
require awk
require sort

require_file "$UNION_CATALOG_TSV"

phase_log "preparing union site list for liftover (input: $UNION_CATALOG_TSV)"

cd "$SITES_DIR"

# Expected union catalog format: rsid<TAB>chrom<TAB>pos (GRCh37, autosomal)
# Filter to autosomes only — sex / mito chromosomes are handled separately
# (Plan §6.1 catalog scope is autosomal for LAI).
awk -F'\t' 'BEGIN{OFS="\t"} $2 ~ /^[0-9]+$/ {print $2, $3, $1}' "$UNION_CATALOG_TSV" \
  | sort -k1,1V -k2,2n -u \
  > array_sites_grch37.tsv

phase_log "autosomal union sites: $(wc -l < array_sites_grch37.tsv)"

# BED is 0-based; prepend chr- prefix for the GRCh38 chain.
awk -F'\t' '{print "chr"$1"\t"($2-1)"\t"$2"\t"$3}' array_sites_grch37.tsv \
  > array_sites_grch37.bed

cd "$LIFTOVER_DIR"

if [ ! -s hg19ToHg38.over.chain.gz ]; then
  phase_log "downloading liftover chain"
  wget -q -O hg19ToHg38.over.chain.gz "$CHAIN_URL"
fi

phase_log "running CrossMap GRCh37 → GRCh38"
CrossMap bed hg19ToHg38.over.chain.gz \
  "$SITES_DIR/array_sites_grch37.bed" \
  array_sites_grch38.bed

mapped=$(wc -l < array_sites_grch38.bed)
unmapped=$(wc -l < array_sites_grch38.bed.unmap 2>/dev/null || echo 0)
phase_log "lifted sites: $mapped, unmapped: $unmapped"

# 1-based tab-delimited 'CHROM BEG END' for bcftools -R. The file MUST end in
# .tsv (NOT .bed): a .bed suffix makes bcftools treat coords as 0-based
# half-open, turning each 'pos pos' line into a zero-width interval that
# matches no variants (Phase 03 would emit 0 variants/chrom).
awk -F'\t' '{print $1"\t"$3"\t"$3}' array_sites_grch38.bed \
  | sort -k1,1V -k2,2n \
  > array_sites_grch38_regions.tsv

# rsID → GRCh38 (chr, pos) — feeds runtime liftover and the
# bundle's liftover/array_site_mapping.tsv (Plan §6 phase 7a layout).
paste \
  <(awk '{print $4}' array_sites_grch38.bed) \
  <(awk '{print $1"\t"$3}' array_sites_grch38.bed) \
  > rsid_to_grch38.tsv

phase_log "phase 2 complete"
