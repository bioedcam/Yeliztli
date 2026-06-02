# VEP Bundle v2.0.0

First bundle built from the union of the 23andMe v5 and AncestryDNA v2.0 site
catalogs.

- **Catalog source**: union of 23andMe v5 + AncestryDNA v2.0 (~2.0M sites, GRCh37)
- **Catalog SHA-256**: `544295b6813fb5a288e1824f4ab9e29824dd70ebc5027b9d2db8fdbbd3536317` (`union_sites_report.json::sha256_output`)
- **Site count**: 2,008,131 (`union_sites_report.json::union_count`; rs-only slice = `rs_count` = 1,941,528)
- **Ensembl version**: 112
- **Build date**: 2026-06-01
- **Schema version**: 1
- **Bundle SHA-256**: `9f645b2c6963e2a83e69c0b1e5bea777cb1bf20566d7c051cfda9b0fef6393bc`
- **Bundle size**: 358,752,256 bytes (342.1 MB)
- **Variant count**: 2,983,291 (`bundle_metadata.variant_count`; one row per `(rsid, alt)`)
- **min_app_version**: `0.2.0`

## Notes

This release rebuilds the VEP bundle against the union 23andMe v5 ∪ AncestryDNA v2.0 catalog so AncestryDNA uploads achieve ≥95% rsID-bundle coverage at annotation time. The remaining ≤5% falls back to the coordinate-based lookup in `backend/annotation/engine.py` (defense-in-depth for `kgp*` proxies and other non-`rs*` IDs).

`bundle_metadata.bundle_version = "v2.0.0"` is recorded inside the SQLite for audit; the manifest's `version` field is the contract consulted by the runtime staleness gate.

## Compatibility

- Minimum GenomeInsight app version: **0.2.0**
  - The 0.2.0 release ships the bundle-version gate that refuses to ingest
    AncestryDNA raw data against a pre-v2.0.0 bundle. Older app versions can
    still load v2.0.0 for 23andMe samples.

## Verification

Verify the downloaded asset against the manifest:

```bash
sha256sum vep_bundle.db
# Compare against bundles/manifest.json -> vep_bundle.sha256
```

## Rollback

If a regression is found, see `docs/bundle-release-runbook.md` §9 (Rollback).
The manifest can be rolled back independently of the GitHub Release asset.
