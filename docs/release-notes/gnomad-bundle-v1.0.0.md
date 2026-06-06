# gnomAD Bundle v1.0.0

> **Status:** the code, build script (`scripts/build_gnomad_bundle.py`), docs, and
> tests for the bundled-gnomAD path have landed. The ~2 GB release asset is a heavy
> maintainer step that is published in a follow-up commit (GNOMAD_BUNDLE_PLAN.md §4,
> §7). The `<…>` values below are filled in at asset-publish time from the real
> `sha256sum` / `stat -c %s` of the built `gnomad_af.db`, and the
> `bundles["gnomad"]` manifest entry is added then (until then gnomAD remains in
> `pipeline_pins` so the manifest stays valid and auto-install/auto-update is inert).

- **Dataset source**: [gnomAD (Genome Aggregation Database)](https://gnomad.broadinstitute.org/)
- **Version**: gnomAD v2.1.1 exomes (GRCh37) — `release/2.1.1` sites VCF
- **Individuals**: ~141,456 (gnomAD v2.1.1)
- **Scope**: allele frequencies + homozygous counts only (table `gnomad_af`:
  `rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, af_eas, af_eur, af_fin,
  af_sas, homozygous_count`). No SpliceAI / CADD / REVEL / SIFT / PolyPhen or any
  academic-license-restricted predictor columns — those live in dbNSFP, which is
  NOT redistributed.
- **Build date**: `<YYYY-MM-DD at asset publish>`
- **Bundle SHA-256**: `<64-hex sha256 of gnomad_af.db at asset publish>`
- **Bundle size**: `<size_bytes of gnomad_af.db at asset publish>`
- **min_app_version**: `0.2.0`
- **Built by**: `scripts/build_gnomad_bundle.py` (downloads the r2.1.1 exomes sites
  VCF and loads AF data via `backend.annotation.gnomad.load_gnomad_from_vcf`; table
  created, bulk insert, indexes built post-load, WAL checkpointed). Shipped
  **uncompressed** (no gzip — there is no decompress `post_download` hook for
  standalone `.db` files).

## Attribution

gnomAD primary allele-frequency data is released under **CC0 1.0** (public domain
dedication), so redistributing this derived SQLite file is permitted. The gnomAD
project requests citation, and the gnomAD name is a Broad Institute trademark used
here solely for source attribution.

Cite:

> Karczewski, K.J., Francioli, L.C., Tiao, G. et al. "The mutational constraint
> spectrum quantified from variation in 141,456 humans." *Nature* 581, 434–443
> (2020). doi:10.1038/s41586-020-2308-7

See the repo-root `NOTICE` file for the full third-party data attribution list.

## Compatibility

- Minimum GenomeInsight app version: **0.2.0**
- GRCh37 throughout (matching the rest of the app's reference data).

## Verification

Verify the downloaded asset against the manifest:

```bash
sha256sum gnomad_af.db
# Compare against bundles/manifest.json -> bundles.gnomad.sha256

stat -c %s gnomad_af.db
# Compare against bundles/manifest.json -> bundles.gnomad.size_bytes
```

`gnomad_af.db` has no embedded version/metadata table (its schema is AF-only), so
the SHA-256 + size_bytes match IS the integrity gate (the verify-and-publish
workflow skips the internal-version check for `gnomad`).

## Rollback

If a regression is found, roll the `bundles/manifest.json -> bundles.gnomad` entry
back to the prior version (the manifest can be rolled back independently of the
GitHub Release asset); do not delete the published release. Reverting the manifest
entry entirely (and restoring `pipeline_pins.gnomad`) returns gnomAD to the inert
deferred state without breaking installs.
