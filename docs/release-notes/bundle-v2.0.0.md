# VEP bundle v2.0.0

First bundle built from the union of the 23andMe v5 and AncestryDNA v2.0 site
catalogs.

## What's in the box

- Catalog source: union of 23andMe v5 + AncestryDNA v2.0
- Ensembl version: 112
- Build date: 2026-05-18
- Schema version: 2
- Variant count: see manifest `variant_count`

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
