"""Sample QC metrics + reference-bias disclosure — roadmap #9.

Populates the (previously unpopulated) ``qc_metrics`` table from a sample's raw
genotypes and exposes interpretive QC the route layer can surface:

  - **call rate** (fraction of non-missing genotypes) with a ~98% pass line;
  - **autosomal heterozygosity rate** (het / called-biallelic) — a coarse
    contamination / mix-up signal;
  - **Ti/Tv ratio** over heterozygous autosomal SNVs (both alleles observed);
  - **X-het sex check** — *concordance only* (§12.5): the genetically inferred
    sex is compared against the recorded ``individuals.biological_sex`` and
    reported concordant / discordant / indeterminate. This module makes **no
    aneuploidy claims** (that is the separate, gated sex-aneuploidy screen) and
    never overwrites the recorded sex.

The reference-bias disclosure states plainly that call rate, heterozygosity, and
Ti/Tv depend on the array and the population, and that array QC is not a clinical
result.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import sqlalchemy as sa
import structlog

from backend.analysis.zygosity import is_no_call
from backend.db.tables import qc_metrics, raw_variants

logger = structlog.get_logger(__name__)

# A call rate at/above this is the conventional array pass line.
CALL_RATE_PASS = 0.98

_AUTOSOMES = frozenset(str(n) for n in range(1, 23))
_ACGT = frozenset("ACGT")
_TRANSITIONS = (frozenset("AG"), frozenset("CT"))


@dataclass(frozen=True)
class QCMetrics:
    call_rate: float
    heterozygosity_rate: float
    ti_tv_ratio: float | None
    total_variants: int
    called_variants: int
    nocall_variants: int


def _alleles(genotype: str) -> tuple[str, str] | None:
    gt = genotype.strip().upper()
    if len(gt) == 2 and gt[0] in _ACGT and gt[1] in _ACGT:
        return gt[0], gt[1]
    return None


def compute_qc_metrics(sample_engine: sa.Engine) -> QCMetrics:
    """Compute call rate, heterozygosity, and Ti/Tv from raw genotypes."""
    total = nocall = called = 0
    het = hom = transitions = transversions = 0

    with sample_engine.connect() as conn:
        stmt = sa.select(raw_variants.c.chrom, raw_variants.c.genotype)
        for chrom, genotype in conn.execute(stmt):
            total += 1
            if is_no_call(genotype):
                nocall += 1
                continue
            called += 1
            if chrom not in _AUTOSOMES:
                continue
            pair = _alleles(genotype)
            if pair is None:
                continue
            a, b = pair
            if a == b:
                hom += 1
            else:
                het += 1
                if frozenset((a, b)) in _TRANSITIONS:
                    transitions += 1
                else:
                    transversions += 1

    call_rate = called / total if total else 0.0
    het_rate = het / (het + hom) if (het + hom) else 0.0
    ti_tv = transitions / transversions if transversions else None

    return QCMetrics(
        call_rate=round(call_rate, 5),
        heterozygosity_rate=round(het_rate, 5),
        ti_tv_ratio=round(ti_tv, 3) if ti_tv is not None else None,
        total_variants=total,
        called_variants=called,
        nocall_variants=nocall,
    )


def store_qc_metrics(metrics: QCMetrics, sample_engine: sa.Engine) -> None:
    """Persist the latest QC metrics (idempotent — replaces any prior row)."""
    with sample_engine.begin() as conn:
        conn.execute(sa.delete(qc_metrics))
        conn.execute(sa.insert(qc_metrics).values(**asdict(metrics)))
    logger.info(
        "qc_metrics_stored",
        call_rate=metrics.call_rate,
        het_rate=metrics.heterozygosity_rate,
        ti_tv=metrics.ti_tv_ratio,
    )


def sex_check(genetic_sex: str | None, recorded_sex: str | None) -> str:
    """Concordance-only sex check (never an aneuploidy call).

    Returns ``"concordant"`` / ``"discordant"`` / ``"indeterminate"``. It is
    indeterminate when the genetic inference is not a confident XX/XY, or when no
    recorded sex is on file to compare against.
    """
    if genetic_sex not in {"XX", "XY"} or recorded_sex not in {"XX", "XY"}:
        return "indeterminate"
    return "concordant" if genetic_sex == recorded_sex else "discordant"


def het_outlier_zscore(target_rate: float, other_rates: list[float]) -> float | None:
    """Z-score of ``target_rate`` vs the account's other samples' het rates.

    Returns ``None`` when there are too few comparison samples (< 3) or zero
    variance — batch-level outlier detection needs a cohort, so a single-sample
    account yields ``None`` rather than a fabricated flag.
    """
    if len(other_rates) < 3:
        return None
    mean = sum(other_rates) / len(other_rates)
    # Sample variance (Bessel's correction) — the cohort is itself a sample, so
    # dividing by N-1 gives a more conservative SD (fewer false outlier flags).
    var = sum((r - mean) ** 2 for r in other_rates) / (len(other_rates) - 1)
    if var <= 0:
        return None
    return round((target_rate - mean) / (var**0.5), 3)
