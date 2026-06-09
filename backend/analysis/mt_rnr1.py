"""MT-RNR1 aminoglycoside-ototoxicity module (m.1555A>G / m.1494C>T / m.1095T>C) — roadmap #55.

Thin adapter over the shared declarative risk-genotype caller. Unlike the
odds-ratio risk modules (gout, APOL1), this is a pharmacogenomic *avoidance*
panel: the 2021 CPIC guideline recommends that carriers of these mitochondrial
12S-rRNA variants avoid aminoglycoside antibiotics unless a severe infection and
the lack of a safe alternative outweigh the high risk of permanent hearing loss.

Mitochondrial specifics encoded in the panel/disclaimer rather than the engine:

  - **Maternal inheritance.** mtDNA is inherited only from the mother, so a
    variant is shared with maternal relatives but never passed on by fathers.
  - **Homoplasmy / heteroplasmy.** Arrays report a single mitochondrial call and
    cannot measure heteroplasmy; the engine counts a present risk allele
    (``dosage_min: 1``) regardless of whether the chip reports it as one or two
    characters.
  - **Probe coverage.** These positions are frequently off-chip on consumer
    arrays. An absent / no-call probe is surfaced as indeterminate (never a
    false-negative), and the disclaimer states that a negative result does not
    rule the variants out.

These are not nuclear ClinVar P/LP calls, so findings carry
``clinvar_significance=NULL``. No ancestry gate (CPIC guidance is the same across
ancestries; m.1555A>G is simply more common in East Asian ancestry).
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

_PANEL_PATH = Path(__file__).resolve().parent.parent / "data" / "panels" / "mt_rnr1_panel.json"

MODULE = "mt_rnr1"


def load_mt_rnr1_panel(panel_path: Path | None = None) -> RiskPanel:
    """Load the curated MT-RNR1 aminoglycoside-ototoxicity panel."""
    return load_risk_panel(panel_path or _PANEL_PATH)


def assess_mt_rnr1(panel: RiskPanel, sample_engine: sa.Engine) -> RiskAssessment:
    """Read MT-RNR1 genotypes and classify (no ancestry/sex stratification)."""
    readouts = read_genotypes(panel, sample_engine)
    dosages = compute_dosages(panel, readouts)
    return classify(panel, dosages, readouts)


def store_mt_rnr1_findings(assessment: RiskAssessment, sample_engine: sa.Engine) -> int:
    """Persist MT-RNR1 findings to the sample DB (idempotent)."""
    return store_risk_findings(assessment, sample_engine)
