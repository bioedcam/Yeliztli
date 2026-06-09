"""Alpha-1 antitrypsin deficiency (SERPINA1) risk module — §6 / roadmap #25.

Thin adapter over the shared risk-genotype caller. Pi*Z (rs28929474) + Pi*S
(rs17580) → PiZZ/PiSZ/PiSS/PiMZ/PiMS. No sex/ancestry input.
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

_PANEL_PATH = Path(__file__).resolve().parent.parent / "data" / "panels" / "alpha1_panel.json"

MODULE = "alpha1"


def load_alpha1_panel(panel_path: Path | None = None) -> RiskPanel:
    """Load the curated alpha-1 antitrypsin risk panel."""
    return load_risk_panel(panel_path or _PANEL_PATH)


def assess_alpha1(panel: RiskPanel, sample_engine: sa.Engine) -> RiskAssessment:
    """Read SERPINA1 Z/S genotypes and classify."""
    readouts = read_genotypes(panel, sample_engine)
    dosages = compute_dosages(panel, readouts)
    return classify(panel, dosages, readouts)


def store_alpha1_findings(assessment: RiskAssessment, sample_engine: sa.Engine) -> int:
    """Persist alpha-1 findings to the sample DB (idempotent)."""
    return store_risk_findings(assessment, sample_engine)
