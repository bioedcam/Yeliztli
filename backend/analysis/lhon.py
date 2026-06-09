"""Leber hereditary optic neuropathy (LHON) primary-mutation module — roadmap #50.

Thin adapter over the shared declarative risk-genotype caller for the three
primary mtDNA LHON mutations (MT-ND4 m.11778G>A, MT-ND1 m.3460G>A, MT-ND6
m.14484T>C), which together explain >90% of LHON. Like the MT-RNR1 module this
is mitochondrial, so the panel/disclaimer (not the engine) carry the
mtDNA-specific framing:

  - **Maternal inheritance** — shared with maternal relatives, never paternally
    transmitted.
  - **Homoplasmy / heteroplasmy** — arrays give a single binary call and cannot
    measure heteroplasmy; the engine counts a present risk allele
    (``dosage_min: 1``) whether the chip reports it as one or two characters.
  - **Incomplete, sex-biased penetrance** — only ~50% of male and ~10% of female
    carriers ever lose vision, so a positive call is never a diagnosis or a
    prediction (§12.6: positive ≠ penetrance). Stated in every finding.
  - **Probe coverage** — these positions are frequently off-chip; an absent /
    no-call probe is surfaced as indeterminate (never a false-negative), and the
    disclaimer states a negative result does not rule LHON out.

Findings carry ``clinvar_significance=NULL`` (declarative evidence stars, not the
engine's ClinVar P/LP path). No ancestry/sex stratification of the call itself.
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

_PANEL_PATH = Path(__file__).resolve().parent.parent / "data" / "panels" / "lhon_panel.json"

MODULE = "lhon"


def load_lhon_panel(panel_path: Path | None = None) -> RiskPanel:
    """Load the curated LHON primary-mutation panel."""
    return load_risk_panel(panel_path or _PANEL_PATH)


def assess_lhon(panel: RiskPanel, sample_engine: sa.Engine) -> RiskAssessment:
    """Read the three primary LHON genotypes and classify (no stratification)."""
    readouts = read_genotypes(panel, sample_engine)
    dosages = compute_dosages(panel, readouts)
    return classify(panel, dosages, readouts)


def store_lhon_findings(assessment: RiskAssessment, sample_engine: sa.Engine) -> int:
    """Persist LHON findings to the sample DB (idempotent)."""
    return store_risk_findings(assessment, sample_engine)
