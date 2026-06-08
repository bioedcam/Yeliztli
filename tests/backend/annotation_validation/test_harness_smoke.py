"""Smoke test: the live-path harness actually runs the real pipeline.

Asserts only behaviour that is true on the *current* (pre-remediation) code, so
a green result here confirms the harness plumbing — real ``run_annotation`` +
``run_all_analyses`` against synthetic reference DBs — works end to end. The
correctness gates (carriage, sex, F31, …) live in the ``test_m*`` modules.
"""

from __future__ import annotations

from tests.backend.annotation_validation.conftest import with_xx_scaffold


def test_harness_runs_live_pipeline(build_live_run) -> None:
    run = build_live_run(
        variants=with_xx_scaffold(
            [
                # Het carrier of a ClinVar Pathogenic SNV in BRCA2.
                {"rsid": "rs_brca2", "chrom": "13", "pos": 32_900_000, "genotype": "GA"},
                # A common autosomal hom-ref call.
                {"rsid": "rs_common", "chrom": "1", "pos": 1_000_000, "genotype": "CC"},
            ]
        ),
        clinvar=[
            {
                "rsid": "rs_brca2",
                "chrom": "13",
                "pos": 32_900_000,
                "ref": "G",
                "alt": "A",
                "significance": "Pathogenic",
                "review_stars": 3,
                "accession": "VCV000000001",
                "conditions": "Hereditary breast and ovarian cancer syndrome",
                "gene_symbol": "BRCA2",
                "variation_id": 1,
            },
        ],
        vep=[
            {
                "rsid": "rs_brca2",
                "chrom": "13",
                "pos": 32_900_000,
                "ref": "G",
                "alt": "A",
                "gene_symbol": "BRCA2",
                "consequence": "missense_variant",
            },
        ],
        gnomad=[
            {
                "rsid": "rs_brca2",
                "chrom": "13",
                "pos": 32_900_000,
                "ref": "G",
                "alt": "A",
                "af_global": 0.00001,
            },
            {
                "rsid": "rs_common",
                "chrom": "1",
                "pos": 1_000_000,
                "ref": "C",
                "alt": "T",
                "af_global": 0.25,
            },
        ],
    )

    # The engine wrote rows.
    assert run.annot_result.rows_written > 0
    # The ClinVar annotation landed on the live path...
    brca2 = run.annotated_by_rsid("rs_brca2")
    assert brca2 is not None
    assert brca2.clinvar_significance == "Pathogenic"
    # ...with carriage computed from the ClinVar ref/alt (GA vs G>A ⇒ het). A
    # connectivity-only smoke test would miss the genotype-agnostic defect, so
    # assert the zygosity is actually populated, not merely that ClinVar matched.
    assert brca2.zygosity == "het"
    # run_all produced a findings table (a dict of module -> count/err).
    assert isinstance(run.analysis_result, dict)
    assert run.analysis_result  # at least one module ran
    # findings is a list of rows we can inspect.
    assert isinstance(run.findings, list)
