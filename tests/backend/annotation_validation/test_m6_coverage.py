"""M6 — Coverage / recall reconciliation (live path).

Audits the ``annotation_coverage`` bitmask and the raw→annotated reconciliation:
which sources claim coverage, whether a set bit really has a populated column,
whether deprecated rsids were recovered, and whether the bitmask constants are
self-consistent.
"""

from __future__ import annotations

import pytest

from backend.annotation import engine as engine_mod
from tests.backend.annotation_validation.conftest import clinvar_row

# ── F33: bitmask constants must be distinct ───────────────────────────────


@pytest.mark.xfail(strict=True, reason="F33: CPIC_BIT collides with "
                   "GENE_PHENOTYPE_BIT (both 0b10000); fixed by Phase E4")
def test_cpic_bit_distinct_from_gene_phenotype_bit() -> None:
    assert engine_mod.CPIC_BIT != engine_mod.GENE_PHENOTYPE_BIT


# ── inv5 (standing guard): a set coverage bit implies a populated column ───


def test_coverage_bit_implies_source_column(build_live_run) -> None:
    """If a source's coverage bit is set, that source's column is populated."""
    run = build_live_run(
        variants=[{"rsid": "rs_cov", "chrom": "7", "pos": 100, "genotype": "GA"}],
        clinvar=[clinvar_row("rs_cov", "7", 100, "G", "A", "Pathogenic", 3)],
        gnomad=[
            {
                "rsid": "rs_cov",
                "chrom": "7",
                "pos": 100,
                "ref": "G",
                "alt": "A",
                "af_global": 0.002,
            }
        ],
        run_analyses=False,
    )
    for row in run.annotated:
        cov = row.annotation_coverage or 0
        if cov & engine_mod.CLINVAR_BIT:
            assert row.clinvar_significance is not None
        if cov & engine_mod.GNOMAD_BIT:
            assert row.gnomad_af_global is not None


# ── F18: deprecated rsids are recovered (resolution rate > 0) ─────────────


@pytest.mark.xfail(strict=True, reason="F18: dbSNP merges not reconciled on the "
                   "live path; fixed by Phase C2")
def test_merged_rsids_are_recovered(build_live_run) -> None:
    """Both deprecated rsids resolve to their current id and get annotated."""
    run = build_live_run(
        variants=[
            {"rsid": "rs_old1", "chrom": "1", "pos": 100, "genotype": "AA"},
            {"rsid": "rs_old2", "chrom": "2", "pos": 200, "genotype": "GA"},
        ],
        clinvar=[
            clinvar_row("rs_new1", "1", 100, "G", "A", "Pathogenic", 2),
            clinvar_row("rs_new2", "2", 200, "C", "A", "Likely pathogenic", 2),
        ],
        dbsnp_merge_rows=[
            {"old_rsid": "rs_old1", "current_rsid": "rs_new1", "build_id": 151},
            {"old_rsid": "rs_old2", "current_rsid": "rs_new2", "build_id": 151},
        ],
        run_analyses=False,
    )
    recovered = sum(
        1
        for rsid in ("rs_old1", "rs_old2")
        if (row := run.annotated_by_rsid(rsid)) is not None
        and row.clinvar_significance is not None
    )
    assert recovered == 2, f"only {recovered}/2 deprecated rsids were recovered"


# ── F36: raw→annotated reconciliation with an explicit coverage=0 bucket ───


@pytest.mark.xfail(strict=True, reason="F36: unmatched variants dropped with no "
                   "coverage=0 marker; fixed by Phase E1")
def test_unmatched_variant_gets_coverage_zero_marker(build_live_run) -> None:
    run = build_live_run(
        variants=[
            {"rsid": "rs_hit", "chrom": "7", "pos": 100, "genotype": "GA"},
            {"rsid": "rs_miss", "chrom": "7", "pos": 200, "genotype": "CC"},
        ],
        clinvar=[clinvar_row("rs_hit", "7", 100, "G", "A", "Pathogenic", 2)],
        run_analyses=False,
    )
    miss = run.annotated_by_rsid("rs_miss")
    assert miss is not None, "unmatched variant was silently dropped"
    assert (miss.annotation_coverage or 0) == 0
