"""M2 — Synthetic truth-set (live path).

One case per hazard class from ``docs/annotation-validation-strategy.md`` §4-M2.
Each builds a tiny reference set, runs the **real** ``run_annotation`` +
``run_all_analyses`` through :func:`build_live_run`, and asserts the resulting
``annotated_variants`` / ``findings`` match the genotype the sample actually
carries.

Every assertion encodes *post-remediation* behaviour. On today's
genotype-agnostic engine they fail (``zygosity`` is NULL, hom-ref/indels/Y-on-XX
are surfaced, multi-allelic picks the highest-star allele), so each is marked
``xfail(strict=True)`` tagged to its finding id. The marker is removed in the
phase that lands the fix — at which point the test becomes a live gate.

The negative controls (hom-ref ⇒ no pathogenic finding) are the most important:
they would have failed on day one.
"""

from __future__ import annotations

import pytest

from tests.backend.annotation_validation.conftest import with_xx_scaffold


def _clinvar(rsid, chrom, pos, ref, alt, sig, stars, *, gene="GENEX"):
    return {
        "rsid": rsid,
        "chrom": chrom,
        "pos": pos,
        "ref": ref,
        "alt": alt,
        "significance": sig,
        "review_stars": stars,
        "accession": f"VCV_{rsid}",
        "conditions": f"{gene}-related condition",
        "gene_symbol": gene,
        "variation_id": abs(hash(rsid)) % 1_000_000,
    }


# ── F3/F6: hom-ref negative control ───────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="F3: rare-variant finder is ungated; "
                   "fixed by Phase C carriage wiring + Phase D rare gate")
def test_homref_pathogenic_snv_not_surfaced(build_live_run) -> None:
    """A hom-ref call at a ClinVar P/LP SNV must surface NO finding."""
    run = build_live_run(
        variants=[{"rsid": "rs_homref", "chrom": "7", "pos": 100, "genotype": "GG"}],
        clinvar=[_clinvar("rs_homref", "7", 100, "G", "A", "Pathogenic", 3)],
    )
    annotated = run.annotated_by_rsid("rs_homref")
    assert annotated is not None and annotated.zygosity == "hom_ref"
    assert run.findings_for_rsid("rs_homref") == []


# ── F3/F7: het carrier ────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="F1: engine never computes zygosity; "
                   "fixed by Phase C1")
def test_het_carrier_zygosity(build_live_run) -> None:
    run = build_live_run(
        variants=[{"rsid": "rs_het", "chrom": "7", "pos": 200, "genotype": "GA"}],
        clinvar=[_clinvar("rs_het", "7", 200, "G", "A", "Pathogenic", 3)],
        run_analyses=False,
    )
    annotated = run.annotated_by_rsid("rs_het")
    assert annotated is not None
    assert annotated.zygosity == "het"


# ── F3/F7: hom-alt ────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="F1: engine never computes zygosity; "
                   "fixed by Phase C1")
def test_hom_alt_zygosity(build_live_run) -> None:
    run = build_live_run(
        variants=[{"rsid": "rs_homalt", "chrom": "7", "pos": 300, "genotype": "AA"}],
        clinvar=[_clinvar("rs_homalt", "7", 300, "G", "A", "Likely pathogenic", 2)],
        run_analyses=False,
    )
    annotated = run.annotated_by_rsid("rs_homalt")
    assert annotated is not None
    assert annotated.zygosity == "hom_alt"


# ── F17: reverse-strand het ───────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="F1/F17: zygosity (incl. strand) dead "
                   "on the live path; fixed by Phase C1 + Phase D3")
def test_reverse_strand_het(build_live_run) -> None:
    """Genotype on the opposite strand still resolves to the carried zygosity."""
    run = build_live_run(
        # ClinVar G>A; the sample reports the reverse-strand complement C/T.
        variants=[{"rsid": "rs_revstrand", "chrom": "7", "pos": 400, "genotype": "CT"}],
        clinvar=[_clinvar("rs_revstrand", "7", 400, "G", "A", "Pathogenic", 2)],
        run_analyses=False,
    )
    annotated = run.annotated_by_rsid("rs_revstrand")
    assert annotated is not None
    assert annotated.zygosity == "het"


# ── F37: palindromic site, vendor declares + strand ───────────────────────


@pytest.mark.xfail(strict=True, reason="F1/F37: palindrome handling dead on the "
                   "live path; fixed by Phase C1 + Phase D3")
def test_palindromic_homozygous_ancestrydna(build_live_run) -> None:
    """At a palindromic A/T site an AncestryDNA (+ strand) hom call is hom_alt."""
    run = build_live_run(
        file_format="ancestrydna_v2.0",
        variants=[{"rsid": "rs_palin", "chrom": "7", "pos": 500, "genotype": "TT"}],
        clinvar=[_clinvar("rs_palin", "7", 500, "A", "T", "Pathogenic", 2)],
        run_analyses=False,
    )
    annotated = run.annotated_by_rsid("rs_palin")
    assert annotated is not None
    assert annotated.zygosity == "hom_alt"


# ── F10: multi-allelic, carry ALT#2 ───────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="F10: engine picks the highest-star "
                   "ClinVar row, not the carried allele; fixed by Phase C1")
def test_multiallelic_picks_carried_allele(build_live_run) -> None:
    """Genotype carries the lower-star ALT; its significance must win."""
    run = build_live_run(
        variants=[{"rsid": "rs_multi", "chrom": "7", "pos": 600, "genotype": "TT"}],
        clinvar=[
            # C>G Pathogenic (2★) is the highest-star row but NOT carried.
            _clinvar("rs_multi", "7", 600, "C", "G", "Pathogenic", 2),
            # C>T Likely benign (1★) IS the carried allele.
            _clinvar("rs_multi", "7", 600, "C", "T", "Likely benign", 1),
        ],
    )
    annotated = run.annotated_by_rsid("rs_multi")
    assert annotated is not None
    assert annotated.clinvar_significance == "Likely benign"
    assert annotated.zygosity == "hom_alt"
    # ...and therefore it is NOT surfaced as a pathogenic finding.
    assert run.findings_in("clinvar_pathogenic") == [] or all(
        f.rsid != "rs_multi" for f in run.findings_in("clinvar_pathogenic")
    )


# ── F16: indel (I/D) is unscoreable, not confident-Pathogenic ─────────────


@pytest.mark.xfail(strict=True, reason="F16: indel no-call surfaced as confident "
                   "Pathogenic; fixed by Phase D2 unscoreable gate")
def test_indel_not_confident_pathogenic(build_live_run) -> None:
    run = build_live_run(
        variants=[{"rsid": "rs_indel", "chrom": "7", "pos": 700, "genotype": "II"}],
        clinvar=[_clinvar("rs_indel", "7", 700, "ATCT", "A", "Pathogenic", 3)],
    )
    high_conf = [
        f
        for f in run.findings_for_rsid("rs_indel")
        if f.category == "clinvar_pathogenic" and (f.evidence_level or 0) >= 3
    ]
    assert high_conf == []


# ── F18: merged rsid resolved to its current id ───────────────────────────


@pytest.mark.xfail(strict=True, reason="F18: dbSNP merge not reconciled on the "
                   "live path; fixed by Phase C2")
def test_merged_rsid_resolved(build_live_run) -> None:
    """An old rsid whose ClinVar record lives under the current id is annotated."""
    run = build_live_run(
        variants=[{"rsid": "rs_old", "chrom": "1", "pos": 800, "genotype": "AA"}],
        clinvar=[_clinvar("rs_new", "1", 800, "G", "A", "Pathogenic", 2, gene="GBA")],
        dbsnp_merge_rows=[
            {"old_rsid": "rs_old", "current_rsid": "rs_new", "build_id": 151}
        ],
        run_analyses=False,
    )
    annotated = run.annotated_by_rsid("rs_old")
    assert annotated is not None
    assert annotated.clinvar_significance == "Pathogenic"
    assert annotated.zygosity == "hom_alt"


# ── F8: chrY finding on an XX sample is biologically impossible ────────────


@pytest.mark.xfail(strict=True, reason="F8: no sex/chromosome gate on findings; "
                   "fixed by Phase D2")
def test_no_chry_finding_on_xx_sample(build_live_run) -> None:
    run = build_live_run(
        variants=with_xx_scaffold(
            [{"rsid": "rs_y", "chrom": "Y", "pos": 2_700_000, "genotype": "GG"}]
        ),
        clinvar=[_clinvar("rs_y", "Y", 2_700_000, "A", "G", "Pathogenic", 2, gene="SRY")],
    )
    assert run.findings_for_rsid("rs_y") == []
