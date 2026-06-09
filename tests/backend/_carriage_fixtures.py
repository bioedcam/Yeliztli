"""Shared seed rows for carriage / zygosity negative-control tests.

The flagship defect class in this codebase is *genotype-agnostic* annotation:
a 23andMe chip reports a genotype at **every** probe regardless of whether the
individual carries the variant, so a ClinVar "Pathogenic" record at a
homozygous-reference position must NOT surface as a clinical finding.

A ``hom_ref`` Pathogenic variant is therefore the canonical **negative control**:
every analysis module that emits findings should have at least one test that
seeds this row and asserts it is *absent* from the findings (see
``docs/test-suite-audit-and-ci-tiering.md`` §1.5).

These builders return ``annotated_variants`` insert dicts (the column subset that
the per-sample test fixtures use); override any column via keyword arguments.
"""

from __future__ import annotations

# Column subset shared by the per-sample annotated_variants test inserts. Every
# omitted column is nullable and defaults to NULL.
_ANNOTATED_DEFAULTS: dict = {
    "rsid": None,
    "chrom": None,
    "pos": None,
    "ref": None,
    "alt": None,
    "genotype": None,
    "zygosity": None,
    "gene_symbol": None,
    "consequence": None,
    "hgvs_coding": None,
    "hgvs_protein": None,
    "gnomad_af_global": None,
    "clinvar_significance": None,
    "clinvar_review_stars": None,
    "clinvar_accession": None,
    "clinvar_conditions": None,
    "cadd_phred": None,
    "revel": None,
    "ensemble_pathogenic": False,
    "evidence_conflict": False,
    "annotation_coverage": 0,
    "disease_name": None,
    "inheritance_pattern": None,
}


def hom_ref_pathogenic_row(**overrides: object) -> dict:
    """A rare, ClinVar-Pathogenic, **homozygous-reference** annotated_variants row.

    Meets the rare-AF + Pathogenic thresholds but is NOT carried (genotype is the
    reference homozygote), so any carriage-gated finder/module must exclude it.
    """
    row = {
        **_ANNOTATED_DEFAULTS,
        "rsid": "rs_hom_ref_pathogenic",
        "chrom": "17",
        "pos": 43091000,
        "ref": "G",
        "alt": "A",
        "genotype": "GG",  # homozygous reference (both alleles == ref)
        "zygosity": "hom_ref",
        "gene_symbol": "BRCA1",
        "consequence": "missense_variant",
        "gnomad_af_global": 0.0003,
        "clinvar_significance": "Pathogenic",
        "clinvar_review_stars": 3,
        "clinvar_accession": "VCV000099999",
        "clinvar_conditions": "Hereditary breast and ovarian cancer",
        "cadd_phred": 30.0,
        "annotation_coverage": 15,
    }
    row.update(overrides)
    return row


def het_pathogenic_row(**overrides: object) -> dict:
    """A rare, ClinVar-Pathogenic, **heterozygous** (carried) annotated_variants row.

    The positive counterpart to :func:`hom_ref_pathogenic_row`: a carriage-gated
    module must keep this one.
    """
    row = hom_ref_pathogenic_row(
        rsid="rs_het_carrier",
        chrom="13",
        pos=32339000,
        ref="C",
        alt="T",
        genotype="CT",
        zygosity="het",
        gene_symbol="BRCA2",
        consequence="stop_gained",
        gnomad_af_global=0.0002,
        clinvar_accession="VCV000088888",
    )
    row.update(overrides)
    return row
