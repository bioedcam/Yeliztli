"""Generic Polygenic Risk Score (PRS) calculator engine.

Implements P3-14: a reusable PRS engine that computes scores from
published weight sets. Designed to be consumed by cancer (P3-15),
traits & personality (P3-63), and any future PRS-based module.

Key design decisions (from PRD):
  - Weight sets tagged with source GWAS ancestry and sample size.
  - Scores expressed as population percentile + z-score (never raw PRS
    value or absolute lifetime risk).
  - Bootstrap CI (1000 iterations, 95% confidence) for uncertainty.
  - Ancestry mismatch warning field on every result (for P3-16).
  - "Research Use Only" tier — PRS findings are never displayed
    alongside monogenic ClinVar findings.

Usage::

    from backend.analysis.prs import (
        PRSWeightSet,
        PRSResult,
        compute_prs,
        compute_prs_percentile,
        compute_prs_bootstrap_ci,
        store_prs_findings,
    )
    from backend.analysis.ancestry import get_inferred_ancestry

    weight_set = PRSWeightSet(
        name="Breast cancer (BCAC)",
        trait="breast_cancer",
        module="cancer",
        source_ancestry="EUR",
        source_study="Mavaddat et al. 2019",
        source_pmid="30554720",
        sample_size=228951,
        weights=[
            PRSSNPWeight(rsid="rs123", effect_allele="A", weight=0.05),
            ...
        ],
        reference_mean=0.0,
        reference_std=1.0,
    )

    result = compute_prs(weight_set, sample_engine)
    result = compute_prs_percentile(result)
    result = compute_prs_bootstrap_ci(result)
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

import numpy as np
import sqlalchemy as sa
import structlog

from backend.analysis.evidence import PRS_EVIDENCE_LEVEL
from backend.analysis.zygosity import is_no_call
from backend.db.tables import annotated_variants, findings

logger = structlog.get_logger(__name__)

# ── Data classes ──────────────────────────────────────────────────────────


@dataclass
class PRSSNPWeight:
    """A single SNP weight entry in a PRS weight set."""

    rsid: str
    effect_allele: str
    weight: float


@dataclass
class PRSWeightSet:
    """A published PRS weight set tagged with ancestry and study metadata.

    Each weight set defines a collection of SNP→weight mappings for a
    specific trait, along with the reference population distribution
    parameters (mean and std) needed for z-score and percentile
    computation.

    Attributes:
        name: Human-readable name (e.g. "Breast cancer (BCAC)").
        trait: Machine-readable trait identifier.
        module: Owning analysis module (e.g. "cancer", "traits").
        source_ancestry: GWAS source population (e.g. "EUR", "EAS").
        source_study: Study citation.
        source_pmid: PubMed ID of the source GWAS.
        sample_size: Total GWAS sample size.
        weights: List of SNP weight entries.
        reference_mean: Mean PRS in the reference population.
        reference_std: Standard deviation of PRS in the reference population.
    """

    name: str
    trait: str
    module: str
    source_ancestry: str
    source_study: str
    source_pmid: str
    sample_size: int
    weights: list[PRSSNPWeight]
    reference_mean: float
    reference_std: float

    @property
    def snp_count(self) -> int:
        """Number of SNPs in the weight set."""
        return len(self.weights)

    def rsid_set(self) -> set[str]:
        """Return the set of rsids in this weight set."""
        return {w.rsid for w in self.weights}


@dataclass
class PRSSNPContribution:
    """Individual SNP contribution to a PRS score."""

    rsid: str
    effect_allele: str
    weight: float
    genotype: str | None
    dosage: int  # 0, 1, or 2 copies of effect allele
    contribution: float  # weight * dosage


@dataclass
class PRSResult:
    """Complete PRS computation result for a single weight set.

    Attributes:
        weight_set_name: Name of the weight set used.
        trait: Trait identifier.
        module: Owning module name.
        source_ancestry: GWAS source population.
        source_study: Study citation.
        source_pmid: PubMed ID.
        sample_size: GWAS sample size.
        raw_score: Sum of weight * dosage.
        z_score: Standardized score ((raw - mean) / std).
        percentile: Population percentile (0–100).
        snps_used: Number of SNPs with available genotype data.
        snps_total: Total SNPs in the weight set.
        coverage_fraction: snps_used / snps_total.
        contributions: Per-SNP contribution breakdown.
        bootstrap_ci_lower: Lower bound of 95% CI (percentile).
        bootstrap_ci_upper: Upper bound of 95% CI (percentile).
        bootstrap_iterations: Number of bootstrap iterations performed.
        ancestry_mismatch: Whether user's ancestry ≠ weight set ancestry.
        ancestry_warning_text: Warning text if ancestry mismatch.
        evidence_level: Star rating (PRS components = ★☆☆☆ = 1).
    """

    weight_set_name: str
    trait: str
    module: str
    source_ancestry: str
    source_study: str
    source_pmid: str
    sample_size: int
    raw_score: float
    z_score: float | None = None
    percentile: float | None = None
    snps_used: int = 0
    snps_total: int = 0
    coverage_fraction: float = 0.0
    contributions: list[PRSSNPContribution] = field(default_factory=list)
    bootstrap_ci_lower: float | None = None
    bootstrap_ci_upper: float | None = None
    bootstrap_iterations: int = 0
    ancestry_mismatch: bool = False
    ancestry_warning_text: str | None = None
    evidence_level: int = PRS_EVIDENCE_LEVEL  # PRS components = ★☆☆☆

    @property
    def is_sufficient(self) -> bool:
        """Whether enough SNPs were genotyped for a meaningful score.

        Requires at least 50% of weight set SNPs to have data.
        """
        return self.coverage_fraction >= 0.5

    @property
    def has_bootstrap_ci(self) -> bool:
        """Whether bootstrap CI has been computed."""
        return self.bootstrap_ci_lower is not None and self.bootstrap_ci_upper is not None


# ── Dosage computation ───────────────────────────────────────────────────


def _count_effect_allele(genotype: str | None, effect_allele: str) -> int:
    """Count copies of the effect allele in a genotype string.

    Genotypes are encoded as two-character strings (e.g. "AG", "AA", "CC").
    For indels or missing data, returns 0.

    Args:
        genotype: Two-character genotype string, or None/empty.
        effect_allele: The effect allele to count.

    Returns:
        0, 1, or 2 — the dosage of the effect allele.
    """
    if is_no_call(genotype):
        return 0
    if len(genotype) < 2:
        return 0

    count = 0
    for allele in genotype:
        if allele.upper() == effect_allele.upper():
            count += 1
    return min(count, 2)


# ── Core PRS computation ────────────────────────────────────────────────


def compute_prs(
    weight_set: PRSWeightSet,
    sample_engine: sa.Engine,
) -> PRSResult:
    """Compute a PRS from a weight set against a sample's annotated variants.

    Queries annotated_variants for each SNP in the weight set, computes
    the dosage of the effect allele, and sums weight * dosage.

    Args:
        weight_set: The PRS weight set with SNP weights.
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        PRSResult with raw_score and per-SNP contributions.
    """
    rsids = list(weight_set.rsid_set())

    # Fetch genotypes for all weight set SNPs in one query
    with sample_engine.connect() as conn:
        stmt = sa.select(
            annotated_variants.c.rsid,
            annotated_variants.c.genotype,
        ).where(annotated_variants.c.rsid.in_(rsids))
        rows = conn.execute(stmt).fetchall()

    genotype_map = {row.rsid: row.genotype for row in rows}

    contributions: list[PRSSNPContribution] = []
    raw_score = 0.0
    snps_used = 0

    for w in weight_set.weights:
        genotype = genotype_map.get(w.rsid)
        dosage = _count_effect_allele(genotype, w.effect_allele)
        contribution = w.weight * dosage

        # Only count as "used" if we found the variant in the sample
        has_data = w.rsid in genotype_map and genotype is not None
        if has_data:
            snps_used += 1
            raw_score += contribution

        contributions.append(
            PRSSNPContribution(
                rsid=w.rsid,
                effect_allele=w.effect_allele,
                weight=w.weight,
                genotype=genotype,
                dosage=dosage if has_data else 0,
                contribution=contribution if has_data else 0.0,
            )
        )

    snps_total = weight_set.snp_count
    coverage_fraction = snps_used / snps_total if snps_total > 0 else 0.0

    logger.info(
        "prs_computed",
        trait=weight_set.trait,
        raw_score=round(raw_score, 6),
        snps_used=snps_used,
        snps_total=snps_total,
        coverage=round(coverage_fraction, 3),
    )

    return PRSResult(
        weight_set_name=weight_set.name,
        trait=weight_set.trait,
        module=weight_set.module,
        source_ancestry=weight_set.source_ancestry,
        source_study=weight_set.source_study,
        source_pmid=weight_set.source_pmid,
        sample_size=weight_set.sample_size,
        raw_score=raw_score,
        snps_used=snps_used,
        snps_total=snps_total,
        coverage_fraction=coverage_fraction,
        contributions=contributions,
    )


# ── Percentile & z-score ────────────────────────────────────────────────


def compute_prs_percentile(
    result: PRSResult,
    reference_mean: float,
    reference_std: float,
) -> PRSResult:
    """Compute z-score and population percentile from raw PRS score.

    Uses the standard normal CDF to convert a z-score to a percentile.
    The reference_mean and reference_std come from the weight set's
    reference population.

    Args:
        result: PRSResult with raw_score computed.
        reference_mean: Mean PRS in the reference population.
        reference_std: Std dev of PRS in the reference population.

    Returns:
        Updated PRSResult with z_score and percentile populated.
    """
    if reference_std <= 0:
        logger.warning(
            "prs_invalid_reference_std",
            trait=result.trait,
            reference_std=reference_std,
        )
        result.z_score = 0.0
        result.percentile = 50.0
        return result

    z = (result.raw_score - reference_mean) / reference_std
    # Standard normal CDF via error function
    percentile = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0))) * 100.0

    result.z_score = round(z, 4)
    result.percentile = round(percentile, 2)

    logger.info(
        "prs_percentile_computed",
        trait=result.trait,
        z_score=result.z_score,
        percentile=result.percentile,
    )

    return result


# ── Bootstrap confidence interval ───────────────────────────────────────


def compute_prs_bootstrap_ci(
    result: PRSResult,
    reference_mean: float,
    reference_std: float,
    n_iterations: int = 1000,
    confidence_level: float = 0.95,
    rng_seed: int | None = None,
) -> PRSResult:
    """Compute bootstrap confidence interval for PRS percentile.

    Resamples the per-SNP contributions (with replacement) to estimate
    the uncertainty in the PRS score, then converts each bootstrap
    replicate to a percentile. The CI bounds are the 2.5th and 97.5th
    percentiles of the bootstrap distribution.

    Args:
        result: PRSResult with contributions populated.
        reference_mean: Mean PRS in the reference population.
        reference_std: Std dev of PRS in the reference population.
        n_iterations: Number of bootstrap iterations (default 1000).
        confidence_level: CI confidence level (default 0.95).
        rng_seed: Optional RNG seed for reproducibility.

    Returns:
        Updated PRSResult with bootstrap_ci_lower/upper populated.
    """
    if reference_std <= 0 or not result.contributions:
        result.bootstrap_ci_lower = result.percentile
        result.bootstrap_ci_upper = result.percentile
        result.bootstrap_iterations = 0
        return result

    # Extract contributions from SNPs that had data
    used_contributions = [c for c in result.contributions if c.genotype is not None]
    if not used_contributions:
        result.bootstrap_ci_lower = result.percentile
        result.bootstrap_ci_upper = result.percentile
        result.bootstrap_iterations = 0
        return result

    contribution_values = np.array([c.contribution for c in used_contributions], dtype=np.float64)
    n_snps = len(contribution_values)

    rng = np.random.default_rng(rng_seed)

    # Bootstrap: resample SNP contributions and compute percentile
    bootstrap_percentiles = np.empty(n_iterations, dtype=np.float64)
    sqrt2 = math.sqrt(2.0)

    for i in range(n_iterations):
        indices = rng.integers(0, n_snps, size=n_snps)
        boot_score = contribution_values[indices].sum()
        z = (boot_score - reference_mean) / reference_std
        boot_pct = 0.5 * (1.0 + math.erf(z / sqrt2)) * 100.0
        bootstrap_percentiles[i] = boot_pct

    alpha = 1.0 - confidence_level
    lower = float(np.percentile(bootstrap_percentiles, 100 * alpha / 2))
    upper = float(np.percentile(bootstrap_percentiles, 100 * (1 - alpha / 2)))

    result.bootstrap_ci_lower = round(lower, 2)
    result.bootstrap_ci_upper = round(upper, 2)
    result.bootstrap_iterations = n_iterations

    logger.info(
        "prs_bootstrap_ci_computed",
        trait=result.trait,
        ci_lower=result.bootstrap_ci_lower,
        ci_upper=result.bootstrap_ci_upper,
        iterations=n_iterations,
    )

    return result


# ── Ancestry lookup ────────────────────────────────────────────────────

# NOTE: get_inferred_ancestry was moved to backend.analysis.ancestry.
# Callers must import from there directly.

# ── Ancestry mismatch warning ───────────────────────────────────────────


def check_ancestry_mismatch(
    result: PRSResult,
    inferred_ancestry: str | None,
    top_ancestry_fraction: float | None = None,
) -> PRSResult:
    """Check and flag ancestry mismatch between PRS weights and user ancestry.

    If the user's inferred top ancestry does not match the weight set's
    source population, an amber warning is attached to the result.

    Additionally, if the top ancestry fraction is below 70%, an admixture
    warning is added regardless of whether the populations match — admixed
    individuals may see reduced PRS accuracy even when the top population
    matches the weight set source.

    Args:
        result: PRSResult to check.
        inferred_ancestry: User's inferred top ancestry (e.g. "EUR", "EAS"),
            or None if ancestry inference hasn't been run.
        top_ancestry_fraction: Fraction (0.0–1.0) of the top ancestry, or
            None if unavailable.

    Returns:
        Updated PRSResult with ancestry_mismatch and ancestry_warning_text.
    """
    if inferred_ancestry is None:
        result.ancestry_mismatch = False
        result.ancestry_warning_text = (
            "Ancestry inference has not been run. PRS accuracy depends on "
            "the match between your ancestry and the study population "
            f"({result.source_ancestry})."
        )
        return result

    source = result.source_ancestry.upper()
    inferred = inferred_ancestry.upper()

    if source != inferred:
        result.ancestry_mismatch = True
        result.ancestry_warning_text = (
            f"This PRS was derived from a {result.source_ancestry} population study. "
            f"Your inferred ancestry ({inferred_ancestry}) differs from the source "
            f"population. Percentile estimates may be less accurate for your "
            f"genetic background."
        )
    else:
        result.ancestry_mismatch = False
        result.ancestry_warning_text = None

    # Admixture-aware threshold: warn if top ancestry < 70%
    if top_ancestry_fraction is not None and top_ancestry_fraction < 0.70:
        admixture_warning = (
            "Your ancestry composition is admixed "
            f"(top ancestry {top_ancestry_fraction:.0%}). "
            "PRS accuracy may be reduced for admixed genetic backgrounds."
        )
        if result.ancestry_warning_text:
            result.ancestry_warning_text += f" {admixture_warning}"
        else:
            result.ancestry_mismatch = True
            result.ancestry_warning_text = admixture_warning

    return result


# ── Full PRS pipeline ───────────────────────────────────────────────────


def run_prs(
    weight_set: PRSWeightSet,
    sample_engine: sa.Engine,
    inferred_ancestry: str | None = None,
    top_ancestry_fraction: float | None = None,
    n_bootstrap: int = 1000,
    rng_seed: int | None = None,
) -> PRSResult:
    """Run the complete PRS pipeline: compute → percentile → bootstrap → ancestry check.

    Convenience function that chains all PRS steps.

    Args:
        weight_set: PRS weight set.
        sample_engine: Sample database engine.
        inferred_ancestry: User's inferred ancestry, or None.
        top_ancestry_fraction: Fraction (0.0–1.0) of the top ancestry, or
            None if unavailable.
        n_bootstrap: Bootstrap iterations (default 1000).
        rng_seed: Optional RNG seed for reproducibility.

    Returns:
        Complete PRSResult.
    """
    result = compute_prs(weight_set, sample_engine)
    result = compute_prs_percentile(result, weight_set.reference_mean, weight_set.reference_std)
    result = compute_prs_bootstrap_ci(
        result,
        weight_set.reference_mean,
        weight_set.reference_std,
        n_iterations=n_bootstrap,
        rng_seed=rng_seed,
    )
    result = check_ancestry_mismatch(result, inferred_ancestry, top_ancestry_fraction)
    return result


# ── Findings storage ────────────────────────────────────────────────────


def store_prs_findings(
    results: list[PRSResult],
    sample_engine: sa.Engine,
    module: str,
) -> int:
    """Store PRS findings in the sample database.

    Creates one finding per PRS result with the appropriate module tag.
    Findings include the "prs" category, z-score, percentile, bootstrap CI,
    ancestry source tag, and mismatch warning.

    Args:
        results: List of PRSResult objects to store.
        sample_engine: SQLAlchemy engine for the sample database.
        module: Module name for clearing/storing (e.g. "cancer", "traits").

    Returns:
        Number of findings inserted.
    """
    rows: list[dict] = []

    for r in results:
        if not r.is_sufficient:
            logger.info(
                "prs_finding_skipped_insufficient",
                trait=r.trait,
                coverage=r.coverage_fraction,
            )
            continue

        percentile_text = f"{r.percentile:.0f}th" if r.percentile is not None else "N/A"
        z_text = f"z = {r.z_score:.2f}" if r.z_score is not None else ""
        ci_text = ""
        if r.has_bootstrap_ci:
            ci_text = f" (95% CI: {r.bootstrap_ci_lower:.0f}th–{r.bootstrap_ci_upper:.0f}th)"

        finding_text = (
            f"{r.weight_set_name}: {percentile_text} percentile{ci_text}"
            f" [{z_text}] — Research Use Only"
        )

        detail = {
            "trait": r.trait,
            "name": r.weight_set_name,
            "is_sufficient": r.is_sufficient,
            "source_ancestry": r.source_ancestry,
            "source_study": r.source_study,
            "source_pmid": r.source_pmid,
            "sample_size": r.sample_size,
            "snps_used": r.snps_used,
            "snps_total": r.snps_total,
            "coverage_fraction": r.coverage_fraction,
            "z_score": r.z_score,
            "bootstrap_ci_lower": r.bootstrap_ci_lower,
            "bootstrap_ci_upper": r.bootstrap_ci_upper,
            "bootstrap_iterations": r.bootstrap_iterations,
            "ancestry_mismatch": r.ancestry_mismatch,
            "ancestry_warning_text": r.ancestry_warning_text,
            "research_use_only": True,
        }

        rows.append(
            {
                "module": module,
                "category": "prs",
                "evidence_level": r.evidence_level,
                "finding_text": finding_text,
                "prs_score": r.raw_score,
                "prs_percentile": r.percentile,
                "pmid_citations": json.dumps([r.source_pmid]),
                "detail_json": json.dumps(detail),
            }
        )

    if not rows:
        logger.info("no_prs_findings_to_store", module=module)
        return 0

    with sample_engine.begin() as conn:
        # Clear previous PRS findings for this module
        conn.execute(
            sa.delete(findings).where(
                findings.c.module == module,
                findings.c.category == "prs",
            )
        )
        conn.execute(sa.insert(findings), rows)

    logger.info("prs_findings_stored", module=module, count=len(rows))
    return len(rows)
