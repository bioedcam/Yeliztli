# Ancestry Module — Methods & Validation

Yeliztli's ancestry module uses a two-tier system to estimate genetic ancestry from 23andMe raw data.

## Populations

Seven superpopulations in canonical order:

| Code | Population | Reference Samples |
|------|-----------|-------------------|
| AFR | African | ~600 |
| AMR | American (Indigenous/Admixed) | ~350 |
| CSA | Central & South Asian | ~500 |
| EAS | East Asian | ~500 |
| EUR | European | ~700 |
| MID | Middle Eastern | ~200 |
| OCE | Oceanian | ~50 |

**Reference panel:** 3,419 single-ancestry samples from gnomAD HGDP+1KG (672 known-admixed excluded).

## Tier 1: Instant Ancestry (PCA + NNLS)

**Runtime:** < 1 second for 600K SNPs.

### AIM Selection

5,000 ancestry-informative markers (AIMs) selected from 548,818 SNPs using a combined score of Rosenberg's informativeness (Iₙ) and Fst across all population pairs. AIMs are mapped to both GRCh38 coordinates and rsIDs for compatibility with 23andMe GRCh37 data.

### PCA Projection

User genotypes at AIM positions are projected onto 8 principal components derived from the reference panel. Missing AIMs are handled via mean imputation. The number of significant PCs (8) was determined by Tracy-Widom test with pre-computed p-values stored in the PCA bundle.

### Admixture Estimation

Two independent methods provide admixture proportions:

- **NNLS (Non-Negative Least Squares):** Decomposes the user's PC coordinates as a non-negative linear combination of population centroids using `scipy.optimize.nnls`. Proportions are normalized to sum to 1.0. This is the primary method.
- **kNN (k-Nearest Neighbors):** Classifies ancestry based on the k=15 nearest reference samples in PC space. Proportions are derived from neighbor population frequencies.

### Confidence Score

Cosine similarity between NNLS and kNN proportion vectors. Values > 0.9 indicate high agreement between methods; values < 0.5 suggest ambiguous ancestry or data quality issues.

### Bootstrap Confidence Intervals

100 bootstrap iterations over random AIM subsets provide 95% confidence intervals per population, displayed as error bars on the admixture chart.

## Tier 2: Local Ancestry Inference (LAI)

**Runtime:** 15–30 minutes (16 GB RAM, quad-core). Requires LAI bundle (~500 MB) and Java 8+.

### Pipeline

1. **Genotype extraction** — User variants are matched to reference panel SNPs via rsID-to-GRCh38 liftover lookup. Encoded as 0/1/2 dosages per autosome.
2. **Phasing** — Beagle 5.4 phases user genotypes against the reference panel (3,419 samples × 549K SNPs) with recombination maps. Produces phased VCF per chromosome.
3. **Base classification** — Per-chromosome Gnomix models (logistic regression) classify each genomic window into one of 7 populations using phased haplotype features. Windows use M SNPs with context overlap at edges (mirror-reflect padding).
4. **Smoothing** — XGBoost boosters refine base predictions using a sliding window of S=75 neighboring windows' probability vectors (525 features per window).
5. **Aggregation** — Per-window ancestry calls across 22 autosomes produce chromosome-level painting and global ancestry proportions.

### Gnomix Model Format

Re-exported from trained Gnomix models into a portable numpy + XGBoost native format (no pickle files, no scikit-learn dependency):

- `metadata.npz` — SNP positions, alleles, population order, window parameters
- `base_coefs.npz` — Logistic regression weights per window
- `smoother.json` — XGBoost native booster format

### Population Remapping

Internal model order `[CSA, AFR, OCE, EUR, MID, AMR, EAS]` is remapped to canonical order `[AFR, AMR, CSA, EAS, EUR, MID, OCE]` in all outputs.

## Validation

| Metric | Value |
|--------|-------|
| Gnomix mean per-window accuracy | 88% |
| Beagle mean phasing switch error | 5.66% |
| NNLS/kNN concordance (single-ancestry samples) | > 95% |

## Known Limitations

- **MID accuracy:** Middle Eastern population has ~25% per-window accuracy in LAI due to limited reference samples (~200) and genetic similarity to EUR and CSA. MID estimates carry a confidence warning when proportion < 15%.
- **No sub-continental resolution:** The system reports 7 superpopulations only. Sub-continental ancestry (e.g., Northern vs. Southern European) is not estimated.
- **Admixed individuals:** Tier 1 NNLS may underestimate minor ancestry components below ~10%. Tier 2 LAI provides more accurate estimates for admixed individuals.
- **23andMe coverage:** AIM matching depends on rsID overlap with the 23andMe genotyping chip. Missing AIMs are imputed at the mean, which can reduce accuracy.

## Dependencies

| Package | Purpose | Required By |
|---------|---------|-------------|
| numpy | PCA projection, array operations | Tier 1 & 2 |
| scipy | NNLS admixture estimation | Tier 1 |
| pysam | VCF read/write (replaces bcftools/bgzip/tabix) | Tier 2 |
| xgboost | Gnomix smoother step | Tier 2 |
| Java 8+ | Beagle phasing | Tier 2 |

## Configuration

| Setting | Env Variable | Default | Description |
|---------|-------------|---------|-------------|
| `lai_bundle_path` | `YELIZTLI_LAI_BUNDLE_PATH` | `~/.yeliztli/lai_bundle/` | Path to LAI bundle directory |
| `lai_java_mem` | `YELIZTLI_LAI_JAVA_MEM` | `4g` | JVM memory for Beagle phasing |

LAI availability is auto-derived at runtime from bundle presence + Java detection. There is no manual enable/disable toggle.

## Citations

- Koenig, Z. et al. (2024). A harmonized public resource of deeply sequenced diverse human genomes. *Genome Research*.
- Hilmarsson, H. et al. (2021). High resolution ancestry deconvolution for next generation genomic data. *bioRxiv*.
- Browning, B. L. et al. (2018). A one-penny imputed genome from next-generation reference panels. *American Journal of Human Genetics*.
