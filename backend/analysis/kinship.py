"""Within-account KING-robust kinship / relatedness QC — roadmap #49.

Estimates pairwise relatedness between the local samples using the KING-robust
kinship estimator (Manichaikul 2010), which needs no population allele
frequencies and is robust to population structure:

    phi_ij = (N_hethet - 2 * N_ibs0) / (N_het_i + N_het_j)

over the autosomal SNPs both samples type (biallelic ACGT calls), where
N_hethet = #both-heterozygous, N_ibs0 = #opposite-homozygotes, and N_het_* =
#heterozygous sites per sample. A duplicate/MZ pair scores ~0.5.

Relationship is read from KING's standard kinship cutoffs:

    > 0.354          duplicate / MZ twin (or the same sample loaded twice)
    [0.177, 0.354]   1st-degree (parent-offspring or full sibling)
    [0.0884, 0.177]  2nd-degree
    [0.0442, 0.0884] 3rd-degree
    < 0.0442         unrelated

Within 1st-degree, parent-offspring is distinguished from full siblings by the
IBS0 (opposite-homozygote) proportion: a parent and child share one allele at
every locus, so opposite homozygotes are essentially impossible (IBS0 ≈ 0),
whereas full siblings carry a meaningful IBS0 fraction. This is a heuristic, and
the finding says so.

Scope & honesty guardrails (§12): this runs **strictly within one local
account's own samples** and never across users. It reports the SNP count each
estimate used; cross-vendor pairs share fewer SNPs and can disagree on strand
(an A/T or C/G call may look like an opposite homozygote when it is actually the
same genotype on the other strand), so cross-vendor estimates carry an explicit
reliability caveat. The result is QC / informational (sample-swap and
duplicate detection, relatedness context), not a clinical or legal test.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
import structlog

from backend.analysis.zygosity import is_no_call
from backend.db.tables import findings, raw_variants

logger = structlog.get_logger(__name__)

MODULE = "kinship"
CATEGORY = "relatedness"

# KING kinship-coefficient cutoffs (Manichaikul 2010).
DUP_MIN = 0.354
FIRST_DEGREE_MIN = 0.177
SECOND_DEGREE_MIN = 0.0884
THIRD_DEGREE_MIN = 0.0442

# Within 1st-degree, parent-offspring vs full-sibling split on IBS0 proportion.
# PO share one allele at every locus → opposite homozygotes are near-impossible.
PO_IBS0_MAX = 0.0012

# Below this many shared informative SNPs, an estimate is too thin to report.
MIN_SHARED_SNPS = 2000

_AUTOSOMES = frozenset(str(n) for n in range(1, 23))
_ACGT = frozenset("ACGT")


@dataclass(frozen=True)
class KinshipStats:
    phi: float
    ibs0: int
    ibs0_proportion: float
    n_shared: int
    het_i: int
    het_j: int
    hethet: int
    relationship: str


@dataclass
class KinshipPair:
    other_sample_id: int
    other_sample_name: str
    same_vendor: bool
    stats: KinshipStats


@dataclass
class KinshipResult:
    target_sample_id: int
    pairs: list[KinshipPair] = field(default_factory=list)
    samples_compared: int = 0


def _hom_allele(genotype: str) -> str | None:
    """Return the shared base of a homozygous ACGT call, else None."""
    gt = genotype.strip().upper()
    if len(gt) == 2 and gt[0] in _ACGT and gt[0] == gt[1]:
        return gt[0]
    return None


def _is_het(genotype: str) -> bool:
    gt = genotype.strip().upper()
    return len(gt) == 2 and gt[0] in _ACGT and gt[1] in _ACGT and gt[0] != gt[1]


def read_autosomal_genotypes(sample_engine: sa.Engine) -> dict[str, str]:
    """Return ``{rsid: genotype}`` for autosomal biallelic ACGT calls.

    Keyed by rsID so two samples can be intersected; no-calls / haploid / indel
    genotypes are skipped (uninformative for KING).
    """
    out: dict[str, str] = {}
    with sample_engine.connect() as conn:
        stmt = sa.select(raw_variants.c.rsid, raw_variants.c.genotype).where(
            raw_variants.c.chrom.in_(_AUTOSOMES)
        )
        for rsid, genotype in conn.execute(stmt):
            if not rsid or is_no_call(genotype):
                continue
            gt = genotype.strip().upper()
            if len(gt) == 2 and gt[0] in _ACGT and gt[1] in _ACGT:
                out[rsid] = gt
    return out


def _classify(phi: float, ibs0_proportion: float) -> str:
    if phi > DUP_MIN:
        return "duplicate_or_mz_twin"
    if phi >= FIRST_DEGREE_MIN:
        return "parent_offspring" if ibs0_proportion < PO_IBS0_MAX else "full_sibling"
    if phi >= SECOND_DEGREE_MIN:
        return "second_degree"
    if phi >= THIRD_DEGREE_MIN:
        return "third_degree"
    return "unrelated"


def king_kinship(genos_i: dict[str, str], genos_j: dict[str, str]) -> KinshipStats:
    """Compute KING-robust kinship between two rsID→genotype maps.

    Both maps should map rsID → an uppercase biallelic ACGT genotype (``"AA"``,
    ``"AG"``, ``"CT"``); :func:`read_autosomal_genotypes` produces exactly that.
    A non-biallelic / malformed call contributes neither a het nor an IBS0 (it is
    skipped), so it can never inflate the opposite-homozygote count.
    """
    # Iterate the smaller map for speed.
    if len(genos_j) < len(genos_i):
        genos_i, genos_j = genos_j, genos_i
    het_i = het_j = hethet = ibs0 = n_shared = 0
    for rsid, gi in genos_i.items():
        gj = genos_j.get(rsid)
        if gj is None:
            continue
        n_shared += 1
        i_het = _is_het(gi)
        j_het = _is_het(gj)
        if i_het:
            het_i += 1
        if j_het:
            het_j += 1
        if i_het and j_het:
            hethet += 1
        elif not i_het and not j_het:
            # both homozygous → IBS0 when the (valid) homozygous alleles differ.
            # A malformed call yields a None allele and is skipped, never a false IBS0.
            hom_i, hom_j = _hom_allele(gi), _hom_allele(gj)
            if hom_i is not None and hom_j is not None and hom_i != hom_j:
                ibs0 += 1
    denom = het_i + het_j
    phi = (hethet - 2 * ibs0) / denom if denom > 0 else 0.0
    ibs0_proportion = ibs0 / n_shared if n_shared else 0.0
    relationship = (
        _classify(phi, ibs0_proportion) if n_shared >= MIN_SHARED_SNPS else "indeterminate"
    )
    return KinshipStats(
        phi=round(phi, 4),
        ibs0=ibs0,
        ibs0_proportion=round(ibs0_proportion, 5),
        n_shared=n_shared,
        het_i=het_i,
        het_j=het_j,
        hethet=hethet,
        relationship=relationship,
    )


_RELATIONSHIP_LABEL = {
    "duplicate_or_mz_twin": "duplicate sample or identical (MZ) twin",
    "parent_offspring": "parent-offspring",
    "full_sibling": "full sibling",
    "second_degree": "2nd-degree relative (e.g. grandparent, aunt/uncle, half-sibling)",
    "third_degree": "3rd-degree relative (e.g. first cousin)",
    "unrelated": "unrelated",
    "indeterminate": "indeterminate (too few shared SNPs)",
}


def _pair_text(pair: KinshipPair) -> str:
    s = pair.stats
    label = _RELATIONSHIP_LABEL[s.relationship]
    base = (
        f"Estimated relationship to '{pair.other_sample_name}': {label} "
        f"(KING kinship φ={s.phi:.3f}, IBS0 proportion {s.ibs0_proportion:.4f}, "
        f"{s.n_shared:,} shared autosomal SNPs)."
    )
    if s.relationship == "duplicate_or_mz_twin":
        base += (
            " A kinship near 0.5 means these two files are either the same person "
            "loaded twice or identical twins — most often a duplicate upload or a "
            "sample mix-up to check."
        )
    elif s.relationship in {"parent_offspring", "full_sibling"}:
        base += (
            " Parent-offspring vs full-sibling is inferred from the IBS0 proportion "
            "(a heuristic), so treat the specific 1st-degree label as provisional."
        )
    if not pair.same_vendor:
        base += (
            " These samples are from different vendors; they share fewer SNPs and may "
            "differ in strand convention, so this cross-vendor estimate is less reliable."
        )
    return base


def assess_kinship(
    target_sample_id: int,
    target_genos: dict[str, str],
    others: list[tuple[int, str, bool, dict[str, str]]],
) -> KinshipResult:
    """Compare the target against each other sample (id, name, same_vendor, genos)."""
    pairs: list[KinshipPair] = []
    for other_id, other_name, same_vendor, other_genos in others:
        stats = king_kinship(target_genos, other_genos)
        pairs.append(KinshipPair(other_id, other_name, same_vendor, stats))
    # Most-related first.
    pairs.sort(key=lambda p: p.stats.phi, reverse=True)
    return KinshipResult(
        target_sample_id=target_sample_id,
        pairs=pairs,
        samples_compared=len(others),
    )


def store_kinship_findings(result: KinshipResult, sample_engine: sa.Engine) -> int:
    """Persist one finding per non-unrelated pair (idempotent).

    Unrelated / indeterminate pairs are not stored as findings (only related
    pairs and duplicates are actionable); when nothing is related, a single
    informational summary is stored instead.
    """
    related = [
        p for p in result.pairs if p.stats.relationship not in {"unrelated", "indeterminate"}
    ]
    rows: list[dict[str, Any]] = []
    if related:
        for pair in related:
            s = pair.stats
            rows.append(
                {
                    "module": MODULE,
                    "category": CATEGORY,
                    "evidence_level": 1,
                    "finding_text": _pair_text(pair),
                    "conditions": f"Relatedness: {_RELATIONSHIP_LABEL[s.relationship]}",
                    "clinvar_significance": None,
                    "detail_json": json.dumps(
                        {
                            "other_sample_id": pair.other_sample_id,
                            "other_sample_name": pair.other_sample_name,
                            "same_vendor": pair.same_vendor,
                            "phi": s.phi,
                            "relationship": s.relationship,
                            "ibs0": s.ibs0,
                            "ibs0_proportion": s.ibs0_proportion,
                            "n_shared_snps": s.n_shared,
                            "het_i": s.het_i,
                            "het_j": s.het_j,
                            "hethet": s.hethet,
                            "samples_compared": result.samples_compared,
                        }
                    ),
                }
            )
    elif result.samples_compared == 0:
        rows.append(
            {
                "module": MODULE,
                "category": CATEGORY,
                "evidence_level": 1,
                "finding_text": (
                    "No other local samples to compare against. Add a second sample to "
                    "run a within-account relatedness / duplicate (sample-swap) check. "
                    "This is a QC tool, not a clinical or legal relationship test."
                ),
                "conditions": "Relatedness: no comparison samples",
                "clinvar_significance": None,
                "detail_json": json.dumps({"samples_compared": 0}),
            }
        )
    else:
        rows.append(
            {
                "module": MODULE,
                "category": CATEGORY,
                "evidence_level": 1,
                "finding_text": (
                    f"No related samples detected among your {result.samples_compared} "
                    f"other local sample(s). This is a within-account QC estimate "
                    f"(duplicate / sample-swap and relatedness check), not a clinical or "
                    f"legal relationship test."
                ),
                "conditions": "Relatedness: none detected",
                "clinvar_significance": None,
                "detail_json": json.dumps({"samples_compared": result.samples_compared}),
            }
        )

    with sample_engine.begin() as conn:
        conn.execute(
            sa.delete(findings).where(findings.c.module == MODULE, findings.c.category == CATEGORY)
        )
        conn.execute(sa.insert(findings), rows)
    logger.info(
        "kinship_stored",
        target=result.target_sample_id,
        related=len(related),
        compared=result.samples_compared,
    )
    return len(rows)
