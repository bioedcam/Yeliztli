# LAI Bundle v2.0.0

- **Catalog source**: union of 23andMe v5 + AncestryDNA v2.0 (autosomal sites only — ~820k, filtered from the ~840k catalog by `scripts/lai_bundle_v2/02_prepare_sites.sh`)
- **Catalog SHA-256**: `<filled by Phase D>` (`metadata.json::source_sites_sha256`, also equal to `union_sites_report.json::sha256_output`)
- **Reference panel**: `gnomAD HGDP+1KG v3.1.2 (phased SHAPEIT5)` — exact string written to `metadata.json::reference_panel` by `scripts/lai_bundle_v2/07_write_metadata.py:80`. Source bucket: `gs://gcp-public-data--gnomad/resources/hgdp_1kg/phased_haplotypes_v2` (per `env.sh::GNOMAD_BUCKET`).
- **Build date**: `<YYYY-MM-DD>`
- **Bundle SHA-256**: `<filled by Phase D>`
- **Bundle size**: `<bytes>`
- **min_app_version**: `0.2.0`
- **LAI accuracy (mean per-window)**: `<filled by Phase C bio-validator from 06e_lai_accuracy.py>`
- **Phasing switch error**: `<filled by Phase C from 06d_phasing_accuracy.py>`
- **Admixture seed**: `42` (locked)
- **Tool versions**: `<filled by Phase C from metadata.json::tool_versions>`

## Notes

Lifts AncestryDNA from ~500k overlap sites to full ~820k autosomal parity with 23andMe v5. Negative case (locked by test): 23andMe-only samples produce byte-identical LAI output against this bundle vs. `lai-bundle-v1.1.0`.
