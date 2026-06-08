"""Tests for the declarative by-rsID risk-genotype engine.

Engine-level behaviour independent of any specific module: probe-readout status,
dosage computation, model evaluation (first_match), the carriage gate, the
relative-vs-absolute and unknown-caveat loader guards, the ancestry gate, and
idempotent storage that isolates other modules' findings.
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from backend.analysis.risk_genotype import (
    PROBE_ABSENT,
    PROBE_NO_CALL,
    PROBE_TYPED,
    GenotypeModel,
    RiskLocus,
    RiskPanel,
    classify,
    compute_dosages,
    load_risk_panel,
    read_genotypes,
    store_risk_findings,
)
from backend.db.tables import findings, raw_variants


def _panel(models: list[GenotypeModel], **kwargs) -> RiskPanel:
    return RiskPanel(
        module="testmod",
        version="1.0.0",
        description="",
        category="risk_genotype",
        loci=[
            RiskLocus(rsid="rsA", gene_symbol="GENEA", label="A", risk_allele="A", ref_allele="G"),
            RiskLocus(rsid="rsB", gene_symbol="GENEB", label="B", risk_allele="T", ref_allele="C"),
        ],
        genotype_models=models,
        **kwargs,
    )


def _seed(engine: sa.Engine, rows: list[dict]) -> None:
    with engine.begin() as conn:
        conn.execute(sa.insert(raw_variants), rows)


class TestReadGenotypes:
    def test_typed_no_call_absent(self, sample_engine: sa.Engine) -> None:
        _seed(
            sample_engine,
            [
                {"rsid": "rsA", "chrom": "6", "pos": 1, "genotype": "AG"},
                {"rsid": "rsB", "chrom": "6", "pos": 2, "genotype": "--"},
                # rsC absent
            ],
        )
        panel = _panel([])
        readouts = read_genotypes(panel, sample_engine)
        assert readouts["rsA"].status == PROBE_TYPED
        assert readouts["rsB"].status == PROBE_NO_CALL
        assert readouts["rsB"].rsid == "rsB"

    def test_absent_probe(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [{"rsid": "rsA", "chrom": "6", "pos": 1, "genotype": "AG"}])
        readouts = read_genotypes(_panel([]), sample_engine)
        assert readouts["rsB"].status == PROBE_ABSENT


class TestComputeDosages:
    def test_dosage_and_indeterminate(self, sample_engine: sa.Engine) -> None:
        _seed(
            sample_engine,
            [
                {"rsid": "rsA", "chrom": "6", "pos": 1, "genotype": "AA"},  # risk A homozygous
                {"rsid": "rsB", "chrom": "6", "pos": 2, "genotype": "--"},  # no-call
            ],
        )
        panel = _panel([])
        dosages = compute_dosages(panel, read_genotypes(panel, sample_engine))
        assert dosages["rsA"] == 2
        assert dosages["rsB"] is None  # indeterminate, never a false 0


class TestClassify:
    def test_all_reference_no_calls(self, sample_engine: sa.Engine) -> None:
        """All-reference dosages fire no model — the carriage gate."""
        _seed(
            sample_engine,
            [
                {"rsid": "rsA", "chrom": "6", "pos": 1, "genotype": "GG"},  # ref/ref
                {"rsid": "rsB", "chrom": "6", "pos": 2, "genotype": "CC"},
            ],
        )
        model = GenotypeModel(
            id="hom_a",
            match={"rsA": {"dosage": 2}},
            risk_classification="A homozygous",
            evidence_stars=3,
            finding_text="A hom {genotype}",
        )
        panel = _panel([model])
        readouts = read_genotypes(panel, sample_engine)
        dosages = compute_dosages(panel, readouts)
        assessment = classify(panel, dosages, readouts)
        assert assessment.calls == []

    def test_first_match_ordering(self, sample_engine: sa.Engine) -> None:
        """A hom-risk genotype matches the homozygous model before the het one."""
        _seed(sample_engine, [{"rsid": "rsA", "chrom": "6", "pos": 1, "genotype": "AA"}])
        hom = GenotypeModel(
            id="hom_a",
            match={"rsA": {"dosage": 2}},
            risk_classification="A homozygous",
            evidence_stars=3,
            finding_text="A hom {genotype}",
        )
        het = GenotypeModel(
            id="het_a",
            match={"rsA": {"dosage_min": 1}},
            risk_classification="A carrier",
            evidence_stars=1,
            finding_text="A het {genotype}",
        )
        panel = _panel([hom, het], evaluation="first_match")
        readouts = read_genotypes(panel, sample_engine)
        dosages = compute_dosages(panel, readouts)
        assessment = classify(panel, dosages, readouts)
        assert len(assessment.calls) == 1
        assert assessment.calls[0].model_id == "hom_a"

    def test_indeterminate_loci_reported(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [{"rsid": "rsA", "chrom": "6", "pos": 1, "genotype": "AA"}])
        panel = _panel([])  # rsB absent
        readouts = read_genotypes(panel, sample_engine)
        dosages = compute_dosages(panel, readouts)
        assessment = classify(panel, dosages, readouts)
        assert "rsB" in assessment.indeterminate_loci
        assert "rsA" not in assessment.indeterminate_loci


class TestAncestryGate:
    def _gated_panel(self) -> RiskPanel:
        model = GenotypeModel(
            id="high_risk",
            match={"rsA": {"dosage": 2}},
            risk_classification="High risk",
            evidence_stars=3,
            finding_text="High risk {genotype}",
        )
        return _panel(
            [model],
            ancestry_gate={"required_ancestry": "AFR", "mode": "suppress", "min_fraction": 0.5},
        )

    def test_suppressed_for_non_target_ancestry(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [{"rsid": "rsA", "chrom": "6", "pos": 1, "genotype": "AA"}])
        panel = self._gated_panel()
        readouts = read_genotypes(panel, sample_engine)
        dosages = compute_dosages(panel, readouts)
        assessment = classify(panel, dosages, readouts, inferred_ancestry="EUR")
        assert assessment.calls == []
        assert assessment.ancestry_suppressed is True

    def test_fires_for_target_ancestry(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [{"rsid": "rsA", "chrom": "6", "pos": 1, "genotype": "AA"}])
        panel = self._gated_panel()
        readouts = read_genotypes(panel, sample_engine)
        dosages = compute_dosages(panel, readouts)
        assessment = classify(
            panel, dosages, readouts, inferred_ancestry="AFR", ancestry_fraction=0.9
        )
        assert len(assessment.calls) == 1
        assert assessment.ancestry_suppressed is False


class TestLoadRiskPanelGuards:
    def test_rejects_empty_match(self, tmp_path) -> None:
        bad = {
            "module": "x",
            "version": "1.0.0",
            "loci": [{"rsid": "rsA", "gene_symbol": "G", "risk_allele": "A", "ref_allele": "G"}],
            "genotype_models": [
                {
                    "id": "m",
                    "match": {},
                    "risk_classification": "carrier",
                    "evidence_stars": 2,
                    "finding_text": "x",
                }
            ],
        }
        path = tmp_path / "bad_empty.json"
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError, match="empty 'match'"):
            load_risk_panel(path)

    def test_rejects_odds_ratio_without_absolute(self, tmp_path) -> None:
        bad = {
            "module": "x",
            "version": "1.0.0",
            "loci": [{"rsid": "rsA", "gene_symbol": "G", "risk_allele": "A", "ref_allele": "G"}],
            "genotype_models": [
                {
                    "id": "m",
                    "match": {"rsA": {"dosage": 1}},
                    "risk_classification": "carrier",
                    "evidence_stars": 2,
                    "finding_text": "x",
                    "odds_ratio": "OR 3.0",
                }
            ],
        }
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError, match="absolute_risk_context"):
            load_risk_panel(path)

    def test_rejects_unknown_caveat(self, tmp_path) -> None:
        bad = {
            "module": "x",
            "version": "1.0.0",
            "loci": [{"rsid": "rsA", "gene_symbol": "G", "risk_allele": "A", "ref_allele": "G"}],
            "genotype_models": [
                {
                    "id": "m",
                    "match": {"rsA": {"dosage": 1}},
                    "risk_classification": "carrier",
                    "evidence_stars": 2,
                    "finding_text": "x",
                    "caveats": ["not_a_real_caveat"],
                }
            ],
        }
        path = tmp_path / "bad2.json"
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError, match="caveat"):
            load_risk_panel(path)


class TestStoreRiskFindings:
    def _assessment_with_call(self, sample_engine: sa.Engine):
        _seed(sample_engine, [{"rsid": "rsA", "chrom": "6", "pos": 1, "genotype": "AA"}])
        model = GenotypeModel(
            id="hom_a",
            match={"rsA": {"dosage": 2}},
            risk_classification="A homozygous",
            evidence_stars=3,
            finding_text="A hom {genotype}",
            zygosity="hom_alt",
        )
        panel = _panel([model])
        readouts = read_genotypes(panel, sample_engine)
        dosages = compute_dosages(panel, readouts)
        return classify(panel, dosages, readouts)

    def test_idempotent(self, sample_engine: sa.Engine) -> None:
        assessment = self._assessment_with_call(sample_engine)
        store_risk_findings(assessment, sample_engine)
        store_risk_findings(assessment, sample_engine)
        with sample_engine.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(findings.c.module == "testmod")
            ).scalar()
        assert count == 1

    def test_isolates_other_modules(self, sample_engine: sa.Engine) -> None:
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(findings),
                [
                    {
                        "module": "cardiovascular",
                        "category": "monogenic_variant",
                        "evidence_level": 4,
                        "finding_text": "LDLR — Pathogenic",
                    }
                ],
            )
        assessment = self._assessment_with_call(sample_engine)
        store_risk_findings(assessment, sample_engine)
        with sample_engine.connect() as conn:
            cv = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(findings.c.module == "cardiovascular")
            ).scalar()
        assert cv == 1  # untouched

    def test_clinvar_significance_null(self, sample_engine: sa.Engine) -> None:
        """Risk-genotype findings never carry a ClinVar significance."""
        assessment = self._assessment_with_call(sample_engine)
        store_risk_findings(assessment, sample_engine)
        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.module == "testmod")
            ).fetchone()
        assert row.clinvar_significance is None
        detail = json.loads(row.detail_json)
        assert detail["model_id"] == "hom_a"
