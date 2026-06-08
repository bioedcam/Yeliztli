"""M1 — Carriage ground-truth audit.

The single highest-value check from the validation strategy: independently
recompute carriage (via ``backend.analysis.qc_carriage``) and assert the
surfaced pathogenic findings are actually carried.

* :func:`test_audit_carriage_classifies_carriage` is a focused unit test of the
  audit logic — it feeds a controlled findings set (carried / hom-ref /
  undetermined) and checks the tally, independent of the pipeline.
* :func:`test_no_homref_in_pathogenic_findings_alarm` is the live-path gate: on
  the carriage-aware engine, zero hom-ref variants reach the pathogenic finding
  surfaces. (This is what fails catastrophically on a genotype-agnostic engine.)
"""

from __future__ import annotations

import sqlalchemy as sa

from backend.analysis.qc_carriage import audit_carriage
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    clinvar_variants,
    findings,
    raw_variants,
    reference_metadata,
)
from tests.backend.annotation_validation.conftest import clinvar_row

# A sample with known carriage at four ClinVar Pathogenic SNVs:
#   het, hom-alt → carried ; hom-ref → not carried ; indel → undetermined.
_VARIANTS = [
    {"rsid": "rs_het", "chrom": "7", "pos": 100, "genotype": "GA"},
    {"rsid": "rs_homalt", "chrom": "7", "pos": 200, "genotype": "AA"},
    {"rsid": "rs_homref", "chrom": "7", "pos": 300, "genotype": "GG"},
    {"rsid": "rs_indel", "chrom": "7", "pos": 400, "genotype": "II"},
]
_CLINVAR = [
    clinvar_row("rs_het", "7", 100, "G", "A", "Pathogenic", 3),
    clinvar_row("rs_homalt", "7", 200, "G", "A", "Pathogenic", 3),
    clinvar_row("rs_homref", "7", 300, "G", "A", "Pathogenic", 3),
    clinvar_row("rs_indel", "7", 400, "ATCT", "A", "Pathogenic", 3),
]


def test_audit_carriage_classifies_carriage() -> None:
    """The audit tallies carried / hom_ref / undetermined from genotype × alleles.

    Feeds a controlled findings set directly (so it tests the audit logic, not
    the pipeline's gating): four ``clinvar_pathogenic`` findings whose genotypes
    are het, hom-alt, hom-ref and an indel.
    """
    sample_engine = sa.create_engine("sqlite://")
    create_sample_tables(sample_engine)
    reference_engine = sa.create_engine("sqlite://")
    reference_metadata.create_all(reference_engine)

    with reference_engine.begin() as conn:
        conn.execute(clinvar_variants.insert(), _CLINVAR)
    with sample_engine.begin() as conn:
        conn.execute(raw_variants.insert(), _VARIANTS)
        conn.execute(
            findings.insert(),
            [
                {
                    "module": "rare_variants",
                    "category": "clinvar_pathogenic",
                    "rsid": v["rsid"],
                    "finding_text": f"{v['rsid']} test finding",
                }
                for v in _VARIANTS
            ],
        )

    report = audit_carriage(sample_engine, reference_engine)
    path = report.by_category["clinvar_pathogenic"]
    assert path.carried == 2  # het + hom_alt
    assert path.hom_ref == 1  # the hom-ref dump
    assert path.undetermined == 1  # the indel
    assert path.total == 4


def test_no_homref_in_pathogenic_findings_alarm(build_live_run) -> None:
    """The live-path carriage gate: zero hom-ref findings in pathogenic categories."""
    run = build_live_run(variants=_VARIANTS, clinvar=_CLINVAR)
    report = audit_carriage(run.sample_engine, run.registry.reference_engine)
    overall = report.overall()
    assert overall.hom_ref == 0, (
        f"{overall.hom_ref} hom-ref variant(s) surfaced as findings: {report.as_dict()}"
    )
    # Lower bound: the het + hom-alt carriers must actually surface. Without
    # this, a genotype-agnostic engine that surfaced *only* undetermined
    # (indel/no-call) findings — carried==0, hom_ref==0 — would pass vacuously.
    assert overall.carried >= 2, (
        f"expected the het + hom-alt carriers to surface; got {report.as_dict()}"
    )
