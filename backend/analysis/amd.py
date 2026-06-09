"""Age-related macular degeneration (AMD) risk module — §9 / roadmap #26.

Thin adapter over the shared declarative risk-genotype caller. CFH Y402H
(rs1061170) + ARMS2/HTRA1 (rs10490924) are **common GWAS risk alleles, not
ClinVar P/LP** — the engine writes ``clinvar_significance = NULL`` and the panel
caps evidence stars at 3. No sex/ancestry input.
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

_PANEL_PATH = Path(__file__).resolve().parent.parent / "data" / "panels" / "amd_panel.json"

MODULE = "amd"


def load_amd_panel(panel_path: Path | None = None) -> RiskPanel:
    """Load the curated AMD risk panel."""
    return load_risk_panel(panel_path or _PANEL_PATH)


def assess_amd(panel: RiskPanel, sample_engine: sa.Engine) -> RiskAssessment:
    """Read CFH/ARMS2 genotypes and classify."""
    readouts = read_genotypes(panel, sample_engine)
    dosages = compute_dosages(panel, readouts)
    return classify(panel, dosages, readouts)


def store_amd_findings(assessment: RiskAssessment, sample_engine: sa.Engine) -> int:
    """Persist AMD findings to the sample DB (idempotent)."""
    return store_risk_findings(assessment, sample_engine)
