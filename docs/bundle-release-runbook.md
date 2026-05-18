# VEP Bundle Release Runbook

This runbook describes how to rebuild and publish the VEP annotation bundle
(`vep_bundle.db`) as a GitHub Release asset, and how to wire the release into
`bundles/manifest.json` so a running app can resolve it.

Scope: this runbook covers the `vep_bundle` stream only. The LAI bundle has its
own release runbook (`docs/lai-bundle-release-runbook.md`), shipped separately
in PR-0c. The two streams ship under independent semver tags and are released
independently (Plan §2.1).

Conventions used in shell snippets: `<org>` stands for the GitHub owner that
hosts the release (e.g., `bioedcam`); substitute the real org/user when running
the commands.

The rebuild itself is out-of-repo cluster work (offline Ensembl VEP run against
the union site list); this repo carries the input-generation script, the
bundle-builder script, the manifest, and the verify-and-publish workflow that
ships them.

---

## 1. Overview

The `vep_bundle` stream pins, per release, a SQLite file containing
per-rsid VEP annotations for every site in the GenomeInsight rsid catalog.
Each release rebuilds against an updated catalog (e.g., union of 23andMe v5
and AncestryDNA v2.0) and bumps the bundle semver.

Tag prefix: `bundle-v<semver>` (e.g., `bundle-v2.0.0`).
Asset filename (stable per tag): `vep_bundle.db`.
Asset URL (stable per tag, never expires):
`https://github.com/<org>/GenomeInsight/releases/download/bundle-v<semver>/vep_bundle.db`

The bundle is ≥100 MB on every release ≥ v2.0.0 (~600 MB for the union catalog
at v2.0.0), so it cannot live on `raw.githubusercontent.com`. Every release ≥
v2.0.0 ships as a GitHub Release asset.

The manifest's `version` field is the authoritative semver consulted by the
staleness gate, the upload gate, and the update flow. The bundle file's
internal `bundle_metadata.bundle_version` is informational/audit only — a
mismatch with the manifest logs a structured warning but never fails the load
(Plan §5.5).

---

## 2. Prerequisites

Run on the cluster reachable via `ssh two` (or any host with Ensembl VEP, a
GRCh37/38 reference, and enough disk for the union site VCF + VEP output):

- `conda activate GI` (or a host environment carrying the same Python/Node
  pins as `pyproject.toml`).
- Ensembl VEP offline installation matching the `--ensembl-version` you intend
  to write to `bundle_metadata.ensembl_version`. The v2.0.0 release uses
  Ensembl 112.
- GRCh37 reference FASTA + VEP cache (Ensembl 112).
- ≥10 GB free disk in the working directory (raw input + intermediate VCFs +
  output bundle).
- A working `sha256sum` (Linux/WSL2) or `shasum -a 256` (macOS).
- `gh` CLI authenticated against the GenomeInsight repo with `repo` scope, for
  drafting releases.

---

## 3. Rebuild — end-to-end sequence

The rebuild produces two artifacts that ship together: the bundle DB and a
manifest entry pointing at it.

### 3.1 Assemble the union rsid catalog

The catalog is a bare TSV with columns `rsid`, `chrom`, `pos` (GRCh37
coordinates), one row per site, sorted by `(chrom, pos)`. For v2.0.0 the
catalog is the union of:

- the 23andMe v5 autosomal + sex + MT sites,
- the AncestryDNA v2.0 autosomal + sex + MT sites (chr25 PAR collapsed onto
  chrX before the union),

with duplicates removed by `(chrom, pos)` and rsid chosen deterministically
when the two vendors call the same coordinate by different ids (`alt_rsid` is
recorded inside the bundle's `bundle_metadata` for audit).

Persist the file as `union_sites.tsv` in the cluster working directory.

### 3.2 Generate the sites-only VCF for VEP

```bash
python scripts/generate_vep_input.py \
  --rsid-catalog union_sites.tsv \
  -o vep_input.vcf
```

This emits a sites-only VCF (REF=N, ALT=`.`) suitable for offline VEP rsid
lookup. The `--rsid-catalog` flag tells the script to read the bare TSV
instead of a vendor raw-data file; the resulting VCF carries one record per
catalog row.

### 3.3 Run Ensembl VEP offline

```bash
vep \
  --input_file vep_input.vcf \
  --output_file vep_output.vcf \
  --vcf \
  --offline \
  --cache --dir_cache "$VEP_CACHE_DIR" \
  --fasta "$GRCH37_FASTA" \
  --assembly GRCh37 \
  --cache_version 112 \
  --everything \
  --no_stats \
  --force_overwrite
```

Wall-clock on the v2.0.0 catalog (~840k sites) is ~30 minutes on the
`mondragonlab` cluster nodes; tune `--fork` if running on a beefier host.
Compress the output with `bgzip vep_output.vcf` if you want a smaller
intermediate; both plain and gzipped VCF are accepted by step 3.4.

### 3.4 Build the bundle DB

```bash
python scripts/build_vep_bundle.py \
  --vep-vcf vep_output.vcf.gz \
  --output vep_bundle.db \
  --ensembl-version 112 \
  --bundle-version v2.0.0 \
  --rsid-catalog union_sites.tsv \
  --write-stats build_stats.json
```

The `--bundle-version` arg is written into `bundle_metadata.bundle_version`
alongside `ensembl_version`, `build_date`, `variant_count`, and
`schema_version`. Pre-v2.0.0 bundles omit the key (Plan §5.5 contract);
v2.0.0+ bundles always carry it.

`--rsid-catalog union_sites.tsv` lets the builder emit a coverage report
asserting the catalog is fully covered by the VEP output. Inspect
`build_stats.json` afterwards — `coverage_percent` should be ≥ 99.5%. Sites
below that bar are usually catalog rows VEP couldn't resolve (e.g., legacy
`kgp*` ids); audit them before publishing.

### 3.5 Generate SHA-256

```bash
sha256sum vep_bundle.db
# or, on macOS:
shasum -a 256 vep_bundle.db
```

Record the hash, the file size in bytes (`stat -c %s vep_bundle.db` on Linux,
`stat -f %z vep_bundle.db` on macOS), and the build date — these go into the
manifest update in step 4.

### 3.6 Bio-validator sign-off

Bio-validator runs the curated AncestryDNA fixture through the rebuilt bundle
and confirms catalog coverage on the rsid set in `sample_ancestrydna_v2.txt`
matches expectations (Plan §12.1 Validation gates). Sign-off blocks publication
— do not draft the release tag until this clears.

---

## 4. Cut the GitHub Release

### 4.1 Draft the release

```bash
gh release create bundle-v2.0.0 \
  --repo <org>/GenomeInsight \
  --title "VEP bundle v2.0.0" \
  --notes-file docs/release-notes/bundle-v2.0.0.md \
  --draft \
  vep_bundle.db
```

The release is drafted (not published) so the manifest PR can land first and
the verify-and-publish workflow has something to attach to. Release notes
should call out: catalog source (union of 23andMe v5 + AncestryDNA v2.0),
site count, Ensembl version, build date, SHA-256, and the `min_app_version`
this bundle requires (`0.2.0` for v2.0.0).

### 4.2 Verify the asset URL is stable

The asset URL is
`https://github.com/<org>/GenomeInsight/releases/download/bundle-v2.0.0/vep_bundle.db`
and is reachable as soon as the draft is created. Hit it once with `curl -I`
to confirm the redirect returns `200` on the underlying object.

---

## 5. Update `bundles/manifest.json`

Open `bundles/manifest.json` and update the `vep_bundle` entry:

```json
"vep_bundle": {
  "version": "v2.0.0",
  "build_date": "YYYY-MM-DD",
  "url": "https://github.com/<org>/GenomeInsight/releases/download/bundle-v2.0.0/vep_bundle.db",
  "sha256": "<64-hex from step 3.5>",
  "size_bytes": <bytes from step 3.5>,
  "min_app_version": "0.2.0"
}
```

In the same commit, normalize any prior pre-semver fields for clean
`packaging.version.Version` compares — e.g., `"v1.0"` → `"v1.0.0"` on
historical bundles. The manifest's `version` is the contract; the bundle
file's internal `bundle_metadata.bundle_version` is informational only.

Open the PR (PR-0a per Plan §18.1). The verify-and-publish workflow
(`.github/workflows/bundle-release.yml`) runs against the new manifest entry
to confirm the release asset matches the SHA-256, size, and version recorded
in the manifest before the release is flipped from draft to public.

---

## 6. Verify-and-publish workflow

`.github/workflows/bundle-release.yml` is a manual `workflow_dispatch` workflow
that takes a release tag (e.g., `bundle-v2.0.0`) as input and verifies the
attached asset matches the manifest entry:

1. Checks out the repo at the commit carrying the manifest update.
2. Resolves the `vep_bundle` entry from `bundles/manifest.json`.
3. Downloads the asset from the resolved `url`.
4. Computes its SHA-256 + size and compares against the manifest values.
5. Opens the SQLite, reads `bundle_metadata.bundle_version`, and compares
   against `manifest.vep_bundle.version`. A mismatch fails the workflow
   (the in-app gate logs a warning rather than failing, but the publish
   gate is stricter — the values must match before the release is flipped
   from draft to public).
6. Reports the verification outcome as a workflow summary.

The workflow does not flip the release from draft to public automatically.
After it reports green, the maintainer runs `gh release edit bundle-v2.0.0
--draft=false` to publish.

---

## 7. PR sequence (Plan §18.1)

PR-0a — `[bundle-v2.0.0] manifest + runbook + workflow`
- Land `docs/bundle-release-runbook.md` (this file).
- Land `.github/workflows/bundle-release.yml` (manual trigger).
- Update `bundles/manifest.json::vep_bundle` with the v2.0.0 entry (after the
  draft release exists, so the URL resolves).
- Bio-validator sign-off attached as a PR comment.

PR-0b — `[gate + staleness] AncestryDNA upload 409 + StaleSampleGate`
- Depends on PR-0a (manifest carries v2.0.0).
- Adds the upload-time gate that returns HTTP 409 when an AncestryDNA upload
  arrives and the installed bundle version is `< v2.0.0`.
- Adds the staleness service + frontend banner.

PR-0c — `[lai-bundle-v2.0.0]`
- Independent of PR-0a/PR-0b — has its own runbook.

Across PRs, ship in numeric order: PR-0a → PR-0b → (PR-0c in parallel).

---

## 8. Post-release smoke test

After the release is published, run the post-release smoke from any clean
checkout:

```bash
conda activate GI
python -c "
from backend.db.manifest import fetch_manifest
m = fetch_manifest()
entry = m.bundles['vep_bundle']
print(entry.version, entry.url, entry.sha256, entry.size_bytes)
assert entry.version == 'v2.0.0'
"
# Step 5 adds `min_app_version` to `BundleManifestEntry`; once it lands,
# extend this smoke with: `assert entry.min_app_version == '0.2.0'`.
```

The output should print the v2.0.0 entry exactly as recorded in the
manifest. If the manifest fetch fails (network, JSON parse), investigate
before announcing the release.

---

## 9. Rollback

Releases on the `bundle-v*` stream are immutable — older tags stay alive
indefinitely so older app versions can keep downloading them (Plan §2.1).
Rollback is performed by reverting the `bundles/manifest.json` change in a
new PR, which repoints the manifest at the prior release tag. Do not delete
the broken release; instead, edit the release notes to mark it superseded.

If the broken release is already in the wild on installed apps, the next
manifest update repoints them on the manifest's 1 h TTL refresh
(`backend/db/manifest.py::fetch_manifest`).
