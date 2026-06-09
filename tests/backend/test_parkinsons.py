"""Tests for the Parkinson's (LRRK2 G2019S) risk module.

LRRK2 G2019S is rs34637584 (ref G, risk A on the GRCh37 plus strand). Guardrails
under test: a present G2019S fires a single risk-factor finding stored at
evidence_level 2 (so it stays out of the ungated dashboard high-confidence top-5,
behind the ethical gate); the finding frames reduced penetrance and no preventive
treatment ("not a diagnosis and not a prediction"); GBA1 is absent from the panel
(deliberately suppressed); and findings write clinvar_significance=NULL.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from backend.analysis.parkinsons import (
    assess_parkinsons,
    load_parkinsons_panel,
    store_parkinsons_findings,
)
from backend.db.tables import findings, raw_variants


@pytest.fixture()
def panel():
    return load_parkinsons_panel()


def _seed(engine: sa.Engine, rows: list[dict]) -> None:
    if rows:
        with engine.begin() as conn:
            conn.execute(sa.insert(raw_variants), rows)


def _lrrk2(genotype: str) -> dict:  # ref G / risk A
    return {"rsid": "rs34637584", "chrom": "12", "pos": 40734202, "genotype": genotype}


class TestPanelScope:
    def test_panel_only_has_lrrk2_no_gba1(self, panel) -> None:
        assert panel.rsids == ["rs34637584"]
        genes = {loc.gene_symbol for loc in panel.loci}
        assert genes == {"LRRK2"}
        assert "GBA1" not in genes and "GBA" not in genes


class TestDetection:
    def test_heterozygous_fires(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_lrrk2("GA")])
        a = assess_parkinsons(panel, sample_engine)
        assert len(a.calls) == 1
        assert a.calls[0].evidence_stars == 2
        assert "G2019S" in a.calls[0].finding_text

    def test_homozygous_fires(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_lrrk2("AA")])
        a = assess_parkinsons(panel, sample_engine)
        assert len(a.calls) == 1

    def test_minus_strand_equivalent(self, panel, sample_engine: sa.Engine) -> None:
        # "CT" is the reverse-strand complement of plus-strand "GA".
        _seed(sample_engine, [_lrrk2("CT")])
        a = assess_parkinsons(panel, sample_engine)
        assert len(a.calls) == 1

    def test_reference_no_finding(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_lrrk2("GG")])
        a = assess_parkinsons(panel, sample_engine)
        assert a.calls == []

    def test_no_call_indeterminate(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_lrrk2("--")])
        a = assess_parkinsons(panel, sample_engine)
        assert "rs34637584" in a.indeterminate_loci
        assert a.calls == []


class TestEthicalFraming:
    def test_no_prevention_and_not_a_prediction(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_lrrk2("GA")])
        a = assess_parkinsons(panel, sample_engine)
        corpus = a.calls[0].finding_text.lower()
        corpus += " " + " ".join(a.calls[0].detail["caveats"]).lower()
        assert "not a diagnosis and not a prediction" in corpus
        assert "no proven way to prevent" in corpus
        assert "penetrance" in corpus


class TestStorage:
    def test_clinvar_null_and_evidence_two(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_lrrk2("GA")])
        a = assess_parkinsons(panel, sample_engine)
        assert store_parkinsons_findings(a, sample_engine) == 1
        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.module == "parkinsons")
            ).fetchone()
        assert row.clinvar_significance is None
        assert row.gene_symbol == "LRRK2"
        # evidence_level 2 keeps it out of the ungated high-confidence top-5 (>=3).
        assert row.evidence_level == 2
