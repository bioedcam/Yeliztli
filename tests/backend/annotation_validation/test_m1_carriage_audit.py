"""M1 — Carriage ground-truth audit (live path).

The single highest-value check from the validation strategy: for a fully
annotated sample, recompute carriage independently (via
``backend.analysis.qc_carriage``) and assert the surfaced pathogenic findings
are actually carried.

Two deliverables exercised here:

* The :func:`audit_carriage` QC function works **today** — it correctly tallies
  carried / hom_ref / undetermined on a known sample (it is a diagnostic, so it
  reports the bad numbers truthfully on the current engine).
* The carriage *gate* (zero hom-ref findings in the pathogenic categories) is
  ``xfail`` until the engine populates zygosity and the rare finder gates on it.
"""

from __future__ import annotations

import pytest

from backend.analysis.qc_carriage import audit_carriage
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


def test_audit_carriage_reproduces_known_table(build_live_run) -> None:
    """The QC function tallies carried/hom_ref/undetermined correctly today.

    On the current genotype-agnostic engine all four variants surface as
    ``clinvar_pathogenic`` findings; the independent audit re-derives that only
    two are carried, one is hom-ref, and the indel is undetermined.
    """
    run = build_live_run(variants=_VARIANTS, clinvar=_CLINVAR)
    report = audit_carriage(run.sample_engine, run.registry.reference_engine)

    path = report.by_category.get("clinvar_pathogenic")
    assert path is not None, "no clinvar_pathogenic findings were surfaced"
    assert path.carried == 2  # het + hom_alt
    assert path.hom_ref == 1  # the hom-ref dump
    assert path.undetermined == 1  # the indel


@pytest.mark.xfail(strict=True, reason="F3/F6: hom-ref variants are surfaced as "
                   "pathogenic findings; fixed by Phase C1 + Phase D1")
def test_no_homref_in_pathogenic_findings_alarm(build_live_run) -> None:
    """The carriage gate: zero hom-ref findings in the pathogenic categories."""
    run = build_live_run(variants=_VARIANTS, clinvar=_CLINVAR)
    report = audit_carriage(run.sample_engine, run.registry.reference_engine)
    hom_ref_total = report.overall().hom_ref
    assert hom_ref_total == 0, (
        f"{hom_ref_total} hom-ref variant(s) surfaced as findings: "
        f"{report.as_dict()}"
    )
