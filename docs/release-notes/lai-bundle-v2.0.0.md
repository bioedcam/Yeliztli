# LAI Bundle v2.0.0

- **Catalog source**: union of 23andMe v4/v5 + AncestryDNA v2.0 (autosomal sites only — 1,941,023 lifted sites, filtered from the 2,008,131-site catalog by `scripts/lai_bundle_v2/02_prepare_sites.sh`)
- **Catalog SHA-256**: `544295b6813fb5a288e1824f4ab9e29824dd70ebc5027b9d2db8fdbbd3536317` (`metadata.json::source_sites_sha256`, also equal to `union_sites_report.json::sha256_output`)
- **Reference panel**: `gnomAD HGDP+1KG v3.1.2 (phased SHAPEIT5)` — exact string written to `metadata.json::reference_panel` by `scripts/lai_bundle_v2/07_write_metadata.py:80`. Source bucket: `gs://gcp-public-data--gnomad/resources/hgdp_1kg/phased_haplotypes_v2` (per `env.sh::GNOMAD_BUCKET`).
- **Build date**: 2026-06-02
- **Bundle SHA-256**: `96f2fcacd3877b3a9574745e4833ea506312832353f4ec88db052a2ba619d734`
- **Bundle size**: 1,710,542,766 bytes (≈1.59 GiB)
- **min_app_version**: `0.2.0`
- **LAI accuracy (mean per-window)**: `0.97079` (`metadata.json::accuracy_per_window_mean`; 22 chroms, min 0.962 @ chr6, gnomix held-out validation split, seed=42)
- **Phasing switch error**: `0.013133` (`metadata.json::phasing_switch_error`; 330 (child,chrom) pairs, n_het = 3,143,756, leave-one-out Beagle vs Mendelian truth-phasing across 15 trios)
- **Admixture seed**: `42` (locked)
- **Tool versions** (`metadata.json::tool_versions`): `bcftools 1.20`, `beagle` (jar SHA-256 `57226e441f4da7104df139d022ed24ad9804fa72cf754e45d04f5658dcef242b`), `admixture v1.3.0`
- **Site count**: 1,941,023 autosomal lifted sites (`metadata.json::site_count`); window count 17,720 (`metadata.json::window_count`)
- **Ancestries**: 6 continental regions (AFR, EAS, CSA, AMR, OCE, EUR) — MID is unrepresented at the ≥0.95 single-ancestry training bar on the v2 union panel (accepted in the Step 27 bio-validator sign-off)

## Notes

Lifts AncestryDNA from 584,997 overlap sites to full ~1.94M autosomal parity with 23andMe v4/v5. Negative case (locked by test): 23andMe-only samples produce byte-identical LAI output against this bundle vs. `lai-bundle-v1.1.0` (`tests/backend/test_lai_runner_telemetry_parity.py`, 8 passed).

Built by the repaired + parallelized `scripts/lai_bundle_v2` pipeline (PR #286, squash `c81ae9f`) on the GRCh38 panel. The higher mean per-window accuracy (0.97079 vs v1.1's ~0.88) reflects the denser v2 union catalog and the GRCh38 reference panel — corroborated, not contradicted, by the strong 0.0131 phasing switch error (no overfitting; gnomix holds out an internal validation split). Operator / bio-validator sign-off: `step27_biovalidator_signoff.txt` (2026-06-02T18:42Z, APPROVED — all four gates met).

## Compatibility

- Minimum GenomeInsight app version: **0.2.0**
  - The 0.2.0 release ships the bundle-version gate that refuses to ingest
    AncestryDNA raw data against a pre-v2.0.0 bundle. Older app versions can
    still load v2.0.0 for 23andMe samples.

## Verification

Verify the downloaded asset against the manifest:

```bash
sha256sum genomeinsight_lai_bundle_v2.0.0.tar.gz
# Compare against bundles/manifest.json -> lai_bundle.sha256
```

## Rollback

If a regression is found, see `docs/lai-bundle-release-runbook.md` §13 (Rollback).
The rollback target is the `lai-bundle-v1.1.0` release; do not delete the broken
release — roll the `bundles/manifest.json` entry back instead. The manifest can be
rolled back independently of the GitHub Release asset.
