"""Carriage ground-truth audit (validation strategy M1).

Recomputes, independently of the annotation engine, whether the genotypes
behind a sample's surfaced findings actually *carry* the allele the finding is
about. A genotyping chip reports a call at every probe regardless of carriage,
so a genotype-agnostic pipeline surfaces vast numbers of homozygous-reference
"findings". This module re-derives carriage from
``raw_variants.genotype`` × the source ref/alt via the project's own
:func:`backend.analysis.zygosity.classify_zygosity`, and tallies, per finding
category, how many surfaced findings are actually carried vs homozygous
reference vs undetermined (indel/no-call/strand-ambiguous).

It is both a test oracle (``tests/backend/annotation_validation/test_m1_*``) and
a runtime QC metric: a healthy chip sample carries on the order of tens of
pathogenic alleles, not tens of thousands, and **zero** hom-ref findings in the
pathogenic categories.

Read-only: opens no transactions and mutates nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlalchemy as sa

from backend.analysis.zygosity import CARRIED_ZYGOSITIES, classify_zygosity
from backend.db.tables import annotated_variants, clinvar_variants, findings, raw_variants

# Finding categories whose carriage we audit. These are the rare-variant-finder
# categories that should only ever surface variants the individual carries.
PATHOGENIC_CATEGORIES: frozenset[str] = frozenset(
    {
        "clinvar_pathogenic",
        "clinvar_pathogenic_low_confidence",  # F20 0-star sub-tier — still carriage-gated
        "ensemble_pathogenic",
        "rare",
        "novel",
    }
)


@dataclass
class CategoryCarriage:
    """Carriage tally for one finding category."""

    carried: int = 0
    hom_ref: int = 0
    undetermined: int = 0

    @property
    def total(self) -> int:
        return self.carried + self.hom_ref + self.undetermined

    @property
    def carried_fraction(self) -> float:
        return self.carried / self.total if self.total else 0.0

    def as_dict(self) -> dict[str, int | float]:
        return {
            "carried": self.carried,
            "hom_ref": self.hom_ref,
            "undetermined": self.undetermined,
            "total": self.total,
            "carried_fraction": round(self.carried_fraction, 4),
        }


@dataclass
class CarriageReport:
    """Per-category carriage tallies for a sample's findings."""

    by_category: dict[str, CategoryCarriage] = field(default_factory=dict)

    def overall(self) -> CategoryCarriage:
        agg = CategoryCarriage()
        for cat in self.by_category.values():
            agg.carried += cat.carried
            agg.hom_ref += cat.hom_ref
            agg.undetermined += cat.undetermined
        return agg

    def as_dict(self) -> dict[str, dict]:
        return {name: cat.as_dict() for name, cat in self.by_category.items()}


def _best_clinvar_alleles(reference_engine: sa.Engine) -> dict[str, tuple[str, str]]:
    """Map rsid → (ref, alt) of its highest-review-star ClinVar record."""
    best: dict[str, tuple[str, str, int]] = {}
    with reference_engine.connect() as conn:
        rows = conn.execute(
            sa.select(
                clinvar_variants.c.rsid,
                clinvar_variants.c.ref,
                clinvar_variants.c.alt,
                clinvar_variants.c.review_stars,
            ).where(clinvar_variants.c.rsid.isnot(None))
        )
        for rsid, ref, alt, stars in rows:
            stars = stars or 0
            if ref is None or alt is None:
                continue
            if rsid not in best or stars > best[rsid][2]:
                best[rsid] = (ref, alt, stars)
    return {rsid: (ref, alt) for rsid, (ref, alt, _stars) in best.items()}


def audit_carriage(
    sample_engine: sa.Engine,
    reference_engine: sa.Engine,
    *,
    categories: frozenset[str] = PATHOGENIC_CATEGORIES,
) -> CarriageReport:
    """Recompute carriage for a sample's surfaced findings.

    For every finding in *categories*, resolve the genotype behind its rsid and
    the source ref/alt (ClinVar best-by-stars, falling back to the annotated
    row), run ``classify_zygosity``, and tally carried / hom_ref / undetermined.

    Args:
        sample_engine: per-sample engine (``findings``, ``raw_variants``,
            ``annotated_variants``).
        reference_engine: reference engine (``clinvar_variants``).
        categories: finding categories to audit.

    Returns:
        A :class:`CarriageReport`.
    """
    with sample_engine.connect() as conn:
        genotypes = {
            r.rsid: r.genotype
            for r in conn.execute(sa.select(raw_variants.c.rsid, raw_variants.c.genotype))
        }
        annotated_alleles = {
            r.rsid: (r.ref, r.alt)
            for r in conn.execute(
                sa.select(
                    annotated_variants.c.rsid,
                    annotated_variants.c.ref,
                    annotated_variants.c.alt,
                )
            )
        }
        finding_rows = conn.execute(
            sa.select(findings.c.rsid, findings.c.category).where(
                findings.c.category.in_(list(categories))
            )
        ).fetchall()

    best_clinvar = _best_clinvar_alleles(reference_engine)

    report = CarriageReport()
    for rsid, category in finding_rows:
        bucket = report.by_category.setdefault(category, CategoryCarriage())
        genotype = genotypes.get(rsid) if rsid else None
        # Prefer the sample's actually-annotated (carried) alleles; fall back to
        # the highest-star ClinVar record only when the annotation lacks ref/alt
        # (e.g. a genotype-agnostic regression leaves them NULL). Auditing
        # best-by-stars first would score a multi-allelic finding against the
        # wrong ALT and misclassify carriage. ``(None, None)`` is truthy, so the
        # NULL check is explicit rather than relying on ``or``.
        annotated = annotated_alleles.get(rsid)
        if annotated and annotated[0] is not None and annotated[1] is not None:
            ref_alt = annotated
        else:
            ref_alt = best_clinvar.get(rsid)
        if genotype is None or not ref_alt or ref_alt[0] is None or ref_alt[1] is None:
            bucket.undetermined += 1
            continue
        zyg = classify_zygosity(genotype, ref_alt[0], ref_alt[1])
        if zyg in CARRIED_ZYGOSITIES:
            bucket.carried += 1
        elif zyg == "hom_ref":
            bucket.hom_ref += 1
        else:
            bucket.undetermined += 1
    return report
