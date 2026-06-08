"""M5 — Reference-data quality gates (live path).

Catches the reference-data and column-mapping defects that internal-consistency
checks miss. The headline is the **F31 column-mapping regression**: dbNSFP rows
are loaded from a genuine-header TSV through the production
``parse_dbnsfp_tsv_line``, so a wrong ``_FIELD_MAP`` key (``MutPred_score`` vs the
real ``MutPred2_score``) shows up as a 100 %-NULL column — exactly the bug a
pre-normalised CSV fixture hides.
"""

from __future__ import annotations

import pytest

from backend.annotation.gnomad import compute_rare_flags
from tests.backend.annotation_validation.conftest import clinvar_row

# A dbNSFP row keyed by the *real* dbNSFP TSV column names.
_DBNSFP_ROW = {
    "#chr": "7",
    "pos(1-based)": "100",
    "ref": "G",
    "alt": "A",
    "rs_dbSNP": "rs_scored",
    "CADD_phred": "31.0",
    "SIFT4G_score": "0.01",
    "SIFT4G_pred": "D",
    "Polyphen2_HVAR_score": "0.95",
    "Polyphen2_HVAR_pred": "D",
    "REVEL_score": "0.9",
    "MutPred2_score": "0.88",  # real dbNSFP header — loader currently reads MutPred_score
    "VEST4_score": "0.8",
    "MetaSVM_score": "0.7",
    "MetaLR_score": "0.7",
    "GERP++_RS": "5.0",
    "phyloP100way_vertebrate": "7.0",
    "MPC_score": "1.5",
    "PrimateAI_score": "0.8",
}


def _scored_run(build_live_run):
    return build_live_run(
        variants=[{"rsid": "rs_scored", "chrom": "7", "pos": 100, "genotype": "GA"}],
        clinvar=[clinvar_row("rs_scored", "7", 100, "G", "A", "Likely pathogenic", 2)],
        dbnsfp_rows=[_DBNSFP_ROW],
        run_analyses=False,
    )


def test_correctly_mapped_dbnsfp_scores_populate(build_live_run) -> None:
    """Sanity: correctly-mapped in-silico scores load via the real TSV path.

    Proves the harness exercises the production loader — if these were NULL the
    F31 assertion below would be meaningless.
    """
    run = _scored_run(build_live_run)
    row = run.annotated_by_rsid("rs_scored")
    assert row is not None
    assert row.cadd_phred == pytest.approx(31.0)
    assert row.revel == pytest.approx(0.9)


@pytest.mark.xfail(strict=True, reason="F31: dbNSFP loader maps MutPred_score, not "
                   "the real MutPred2_score → 100% NULL; fixed by Phase F4")
def test_mutpred2_column_has_coverage(build_live_run) -> None:
    """MutPred2 must populate from the genuine ``MutPred2_score`` header."""
    run = _scored_run(build_live_run)
    row = run.annotated_by_rsid("rs_scored")
    assert row is not None
    assert row.mutpred2 is not None, "mutpred2 is NULL — wrong dbNSFP field-map key"


# ── F21: obsolete MONDO terms must not reach the user ─────────────────────


@pytest.mark.xfail(strict=True, reason="F21: no obsolete-term filter; fixed by Phase F3")
def test_obsolete_mondo_terms_filtered(build_live_run) -> None:
    run = build_live_run(
        variants=[{"rsid": "rs_tp53", "chrom": "17", "pos": 7_676_000, "genotype": "GA"}],
        clinvar=[clinvar_row("rs_tp53", "17", 7_676_000, "G", "A", "Pathogenic", 3, gene="TP53")],
        vep=[
            {
                "rsid": "rs_tp53",
                "chrom": "17",
                "pos": 7_676_000,
                "ref": "G",
                "alt": "A",
                "gene_symbol": "TP53",
                "consequence": "missense_variant",
            }
        ],
        gene_phenotype_rows=[
            {
                "gene_symbol": "TP53",
                "disease_name": "obsolete Li-Fraumeni syndrome 1",
                "disease_id": "MONDO:0018875",
                "hpo_terms": "[]",
                "source": "mondo_hpo",
                "inheritance": "Autosomal dominant",
            }
        ],
        run_analyses=False,
    )
    row = run.annotated_by_rsid("rs_tp53")
    assert row is not None
    label = (row.disease_name or "").lower()
    assert not label.startswith("obsolete"), f"obsolete term surfaced: {row.disease_name!r}"


# ── F14: dominant genes must not be mislabelled recessive ─────────────────


@pytest.mark.xfail(strict=True, reason="F14: gene-wide inheritance stamped from "
                   "first-in-file; fixed by Phase F3 curated override")
def test_dominant_gene_inheritance(build_live_run) -> None:
    run = build_live_run(
        variants=[{"rsid": "rs_brca1", "chrom": "17", "pos": 43_094_000, "genotype": "GA"}],
        clinvar=[
            clinvar_row("rs_brca1", "17", 43_094_000, "G", "A", "Pathogenic", 3, gene="BRCA1")
        ],
        vep=[
            {
                "rsid": "rs_brca1",
                "chrom": "17",
                "pos": 43_094_000,
                "ref": "G",
                "alt": "A",
                "gene_symbol": "BRCA1",
                "consequence": "stop_gained",
            }
        ],
        gene_phenotype_rows=[
            {
                "gene_symbol": "BRCA1",
                "disease_name": "Hereditary breast and ovarian cancer syndrome",
                "disease_id": "MONDO:0011450",
                "hpo_terms": "[]",
                "source": "mondo_hpo",
                # The wrong current value the curated override must fix.
                "inheritance": "Autosomal recessive",
            }
        ],
        run_analyses=False,
    )
    row = run.annotated_by_rsid("rs_brca1")
    assert row is not None
    assert row.inheritance_pattern == "Autosomal dominant"


# ── F26: AF=0 (monomorphic) is not the same as observed-ultra-rare ────────


@pytest.mark.xfail(strict=True, reason="F26: AF=0 treated as ultra-rare; fixed by Phase F1")
def test_af_zero_is_not_ultra_rare() -> None:
    """A variant never observed in gnomAD (AF=0) must not be flagged ultra-rare."""
    _rare, ultra_rare = compute_rare_flags(0.0)
    assert ultra_rare is False
