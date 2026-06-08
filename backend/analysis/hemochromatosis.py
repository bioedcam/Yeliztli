"""Hereditary haemochromatosis (HFE) risk module — EXPANSION_STRATEGY.md §6 / #23.

A thin adapter over the shared declarative risk-genotype caller
(:mod:`backend.analysis.risk_genotype`). Directly-typed C282Y (rs1800562) and
H63D (rs1799945) with genotype-combination-specific calls and sex-stratified
penetrance (biological sex from :func:`backend.services.sex_inference`).
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from backend.analysis.risk_genotype import (
    RiskAssessment,
    RiskPanel,
    classify,
    compute_dosages,
    load_risk_panel,
    read_genotypes,
    store_risk_findings,
)
from backend.services.sex_inference import infer_biological_sex

_PANEL_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "panels" / "hemochromatosis_panel.json"
)

MODULE = "hemochromatosis"


def load_hemochromatosis_panel(panel_path: Path | None = None) -> RiskPanel:
    """Load the curated HFE risk panel."""
    return load_risk_panel(panel_path or _PANEL_PATH)


def assess_hemochromatosis(
    panel: RiskPanel,
    sample_engine: sa.Engine,
) -> RiskAssessment:
    """Read HFE genotypes and classify, injecting inferred biological sex."""
    readouts = read_genotypes(panel, sample_engine)
    dosages = compute_dosages(panel, readouts)
    sex = infer_biological_sex(sample_engine) if panel.sex_stratified else None
    return classify(panel, dosages, readouts, sex=sex)


def store_hemochromatosis_findings(
    assessment: RiskAssessment,
    sample_engine: sa.Engine,
) -> int:
    """Persist HFE findings to the sample DB (idempotent)."""
    return store_risk_findings(assessment, sample_engine)
