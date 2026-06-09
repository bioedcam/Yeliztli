"""Declarative by-rsID risk-genotype caller (EXPANSION_STRATEGY.md §6, §9).

The existing monogenic modules (cancer, cardiovascular, carrier_status) query
``annotated_variants`` for ClinVar Pathogenic/Likely-pathogenic by gene, gated on
``zygosity ∈ CARRIED_ZYGOSITIES``. That pattern does not fit the directly-typed
risk modules in the expansion wave because:

  - several report **common GWAS risk alleles that are not ClinVar P/LP** (AMD
    CFH/ARMS2, APOL1 G1/G2, gout ABCG2); and
  - the finding is **genotype-combination-specific** (HFE C282Y homozygous vs
    compound het; thrombophilia FVL+F2 double-het; alpha-1 PiZZ; APOL1
    two-risk-allele recessive).

This engine generalises the ``apoe.py`` precedent (read named rsIDs straight from
``raw_variants`` and map the genotype combination to a curated call) into a
*declarative* caller: a panel JSON expresses the loci, the risk allele per
locus, and an ordered set of genotype→risk models. The engine reads genotypes,
counts risk-allele dosage (strand-harmonized via
:func:`backend.analysis.allele_match.risk_dosage`, so minus-strand vendors call
correctly), evaluates the models, and stores findings in the unified ``findings``
table — no schema change.

Honesty guardrails (§12) encoded here:

  - **negative ≠ clear**: a probe that is absent / no-call yields ``None``
    (indeterminate), never a false-negative; the disclaimer always states it.
  - **relative-vs-absolute**: :func:`load_risk_panel` rejects any model that sets
    an ``odds_ratio``/relative risk without an ``absolute_risk_context``.
  - **ancestry overstatement**: an optional ``ancestry_gate`` suppresses or
    caveats calls outside the validated ancestry (APOL1).
  - **no P/LP inflation**: risk-genotype findings write ``clinvar_significance =
    NULL`` and carry declarative evidence stars, never auto-upgraded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa
import structlog

from backend.analysis.allele_match import risk_dosage
from backend.analysis.zygosity import is_no_call
from backend.db.tables import findings, raw_variants

logger = structlog.get_logger(__name__)

_PANELS_DIR = Path(__file__).resolve().parent.parent / "data" / "panels"

# Probe-readout status.
PROBE_TYPED = "typed"
PROBE_NO_CALL = "no_call"
PROBE_ABSENT = "absent"

# ── Shared caveat registry ─────────────────────────────────────────────────
# Caveat keys referenced by panel models resolve to standing language so the
# load-bearing honesty statements are written once and reused. Modules extend
# this dict as they are added.
CAVEAT_REGISTRY: dict[str, str] = {
    "negative_not_clear": (
        "A negative or low-risk result does not rule out untyped or rare variants — "
        "the array interrogates only specific named positions, not the whole gene."
    ),
    "reduced_penetrance": (
        "Carrying this genotype does not mean the condition will develop. Penetrance "
        "is reduced and depends on age, sex, and other genetic and environmental factors."
    ),
    "ancestry_european": (
        "Prevalence and penetrance figures are derived from European-ancestry "
        "populations and may not transfer to other genetic backgrounds."
    ),
    "confirm_clinically": (
        "This is an array-derived research/educational result. Confirm with clinical "
        "testing in a CLIA/accredited laboratory before any medical action."
    ),
    "aat_rare_null": (
        "A result other than PiZZ or PiSZ does not exclude alpha-1 antitrypsin "
        "deficiency: rare null and other deficiency alleles (e.g. Pi*null, Pi*Mmalton) "
        "are not interrogated by the array."
    ),
}


# ── Dataclasses ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RiskLocus:
    """One typed position in a risk panel, anchored to its rsID."""

    rsid: str
    gene_symbol: str
    label: str  # e.g. "C282Y"
    risk_allele: str  # rsID-anchored, on the strand named by canonical_strand
    ref_allele: str
    canonical_strand: str = "plus"  # "plus" | "minus" (minus flags cross-vendor pitfalls)
    off_chip_risk: str = "low"  # "high" → may be absent on arrays (e.g. indels)


@dataclass(frozen=True)
class GenotypeModel:
    """A declarative genotype→risk rule."""

    id: str
    match: dict[str, dict[str, int]]  # rsid → {"dosage"|"dosage_min"|"dosage_max": n}
    risk_classification: str
    evidence_stars: int
    finding_text: str  # template; supports {genotype} {penetrance_text} {classification}
    zygosity: str | None = None
    odds_ratio: str | None = None
    penetrance: Any = None  # str, or {"by_sex": {"XX": ..., "XY": ...}}
    absolute_risk_context: str | None = None
    caveats: list[str] = field(default_factory=list)
    pmids: list[str] = field(default_factory=list)
    primary_rsid: str | None = None  # gene/rsid attribution; defaults to first match key


@dataclass(frozen=True)
class RiskPanel:
    """A loaded, validated risk-genotype panel."""

    module: str
    version: str
    description: str
    category: str
    loci: list[RiskLocus]
    genotype_models: list[GenotypeModel]
    evaluation: str = "first_match"  # "first_match" | "collect_all"
    sex_stratified: bool = False
    ancestry_gate: dict[str, Any] | None = None
    disclaimer_key: str | None = None

    def locus(self, rsid: str) -> RiskLocus | None:
        return next((loc for loc in self.loci if loc.rsid == rsid), None)

    @property
    def rsids(self) -> list[str]:
        return [loc.rsid for loc in self.loci]


@dataclass(frozen=True)
class ProbeReadout:
    rsid: str
    genotype: str | None
    status: str  # PROBE_TYPED | PROBE_NO_CALL | PROBE_ABSENT


@dataclass(frozen=True)
class RiskCall:
    """A fired model → one finding."""

    model_id: str
    gene_symbol: str
    rsid: str  # primary or comma-joined
    risk_classification: str
    evidence_stars: int
    finding_text: str
    zygosity: str | None
    detail: dict[str, Any]
    pmids: list[str]


@dataclass
class RiskAssessment:
    """The full result of assessing a sample against a risk panel."""

    module: str
    category: str
    calls: list[RiskCall] = field(default_factory=list)
    dosages: dict[str, int | None] = field(default_factory=dict)
    readouts: dict[str, ProbeReadout] = field(default_factory=dict)
    indeterminate_loci: list[str] = field(default_factory=list)
    sex_used: str | None = None
    inferred_ancestry: str | None = None
    ancestry_suppressed: bool = False


# ── Panel loading + validation ──────────────────────────────────────────────


def load_risk_panel(path: str | Path) -> RiskPanel:
    """Load and validate a risk-genotype panel JSON.

    Raises ``ValueError`` if a model declares an ``odds_ratio``/relative risk
    without an ``absolute_risk_context`` (the relative-vs-absolute guardrail), or
    references a caveat key absent from :data:`CAVEAT_REGISTRY`.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    loci = [
        RiskLocus(
            rsid=loc["rsid"],
            gene_symbol=loc["gene_symbol"],
            label=loc.get("label", loc["rsid"]),
            risk_allele=loc["risk_allele"].upper(),
            ref_allele=loc["ref_allele"].upper(),
            canonical_strand=loc.get("canonical_strand", "plus"),
            off_chip_risk=loc.get("off_chip_risk", "low"),
        )
        for loc in data["loci"]
    ]

    models: list[GenotypeModel] = []
    for m in data["genotype_models"]:
        if not m.get("match"):
            raise ValueError(
                f"Panel '{data['module']}' model '{m['id']}' has an empty 'match' — "
                f"a model must declare at least one genotype condition."
            )
        caveats = m.get("caveats", [])
        for key in caveats:
            if key not in CAVEAT_REGISTRY:
                raise ValueError(
                    f"Panel '{data['module']}' model '{m['id']}' references unknown "
                    f"caveat key '{key}'."
                )
        if m.get("odds_ratio") and not m.get("absolute_risk_context"):
            raise ValueError(
                f"Panel '{data['module']}' model '{m['id']}' sets an odds_ratio without "
                f"an absolute_risk_context — relative risk must always be paired with "
                f"absolute context."
            )
        models.append(
            GenotypeModel(
                id=m["id"],
                match=m["match"],
                risk_classification=m["risk_classification"],
                evidence_stars=m["evidence_stars"],
                finding_text=m["finding_text"],
                zygosity=m.get("zygosity"),
                odds_ratio=m.get("odds_ratio"),
                penetrance=m.get("penetrance"),
                absolute_risk_context=m.get("absolute_risk_context"),
                caveats=caveats,
                pmids=m.get("pmids", []),
                primary_rsid=m.get("primary_rsid"),
            )
        )

    return RiskPanel(
        module=data["module"],
        version=data["version"],
        description=data.get("description", ""),
        category=data.get("category", "risk_genotype"),
        loci=loci,
        genotype_models=models,
        evaluation=data.get("evaluation", "first_match"),
        sex_stratified=data.get("sex_stratified", False),
        ancestry_gate=data.get("ancestry_gate"),
        disclaimer_key=data.get("disclaimer_key"),
    )


# ── Genotype reading + dosage ───────────────────────────────────────────────


def read_genotypes(panel: RiskPanel, sample_engine: sa.Engine) -> dict[str, ProbeReadout]:
    """Read each panel locus's genotype from ``raw_variants``.

    A probe absent from ``raw_variants`` (off-chip) is ``PROBE_ABSENT`` and a
    no-call is ``PROBE_NO_CALL`` — both yield an indeterminate dosage downstream,
    never a false-negative.
    """
    with sample_engine.connect() as conn:
        stmt = sa.select(raw_variants.c.rsid, raw_variants.c.genotype).where(
            raw_variants.c.rsid.in_(panel.rsids)
        )
        rows = {row.rsid: row.genotype for row in conn.execute(stmt)}

    readouts: dict[str, ProbeReadout] = {}
    for rsid in panel.rsids:
        if rsid not in rows:
            readouts[rsid] = ProbeReadout(rsid, None, PROBE_ABSENT)
        elif is_no_call(rows[rsid]):
            readouts[rsid] = ProbeReadout(rsid, rows[rsid], PROBE_NO_CALL)
        else:
            readouts[rsid] = ProbeReadout(rsid, rows[rsid], PROBE_TYPED)
    return readouts


def compute_dosages(panel: RiskPanel, readouts: dict[str, ProbeReadout]) -> dict[str, int | None]:
    """Risk-allele dosage (0/1/2) per locus, or ``None`` when indeterminate."""
    dosages: dict[str, int | None] = {}
    for loc in panel.loci:
        readout = readouts.get(loc.rsid)
        if readout is None or readout.status != PROBE_TYPED:
            dosages[loc.rsid] = None
            continue
        dosages[loc.rsid] = risk_dosage(readout.genotype, loc.risk_allele, loc.ref_allele)
    return dosages


# ── Classification ──────────────────────────────────────────────────────────


def _condition_matches(dosage: int | None, cond: dict[str, int]) -> bool:
    if dosage is None:
        return False
    if "dosage" in cond and dosage != cond["dosage"]:
        return False
    if "dosage_min" in cond and dosage < cond["dosage_min"]:
        return False
    if "dosage_max" in cond and dosage > cond["dosage_max"]:
        return False
    return True


def _model_matches(model: GenotypeModel, dosages: dict[str, int | None]) -> bool:
    return all(_condition_matches(dosages.get(rsid), cond) for rsid, cond in model.match.items())


def _resolve_penetrance_text(penetrance: Any, sex: str | None) -> str:
    if penetrance is None:
        return ""
    if isinstance(penetrance, str):
        return penetrance
    by_sex = penetrance.get("by_sex", {}) if isinstance(penetrance, dict) else {}
    if sex in by_sex:
        return by_sex[sex]
    # Sex indeterminate (manual_review / unknown / None): show both, flagged.
    parts = [f"{s}: {txt}" for s, txt in by_sex.items()]
    if parts:
        return (
            "Biological sex could not be determined from the array, so both estimates "
            "are shown — " + "; ".join(parts) + "."
        )
    return ""


def _render_finding(
    model: GenotypeModel,
    panel: RiskPanel,
    dosages: dict[str, int | None],
    readouts: dict[str, ProbeReadout],
    sex: str | None,
) -> RiskCall:
    match_rsids = list(model.match.keys())
    primary = model.primary_rsid or match_rsids[0]
    primary_locus = panel.locus(primary)
    gene_symbol = primary_locus.gene_symbol if primary_locus else ""

    genotype_calls = {
        rsid: (readouts[rsid].genotype if rsid in readouts else None) for rsid in match_rsids
    }
    genotype_text = "; ".join(
        f"{rsid} {genotype_calls.get(rsid) or 'n/a'}" for rsid in match_rsids
    )
    penetrance_text = _resolve_penetrance_text(model.penetrance, sex)
    resolved_caveats = [CAVEAT_REGISTRY[k] for k in model.caveats]

    context = {
        "genotype": genotype_text,
        "penetrance_text": penetrance_text,
        "classification": model.risk_classification,
        "odds_ratio": model.odds_ratio or "",
        "absolute_risk": model.absolute_risk_context or "",
    }
    finding_text = model.finding_text.format_map(_SafeDict(context))

    detail = {
        "model_id": model.id,
        "classification": model.risk_classification,
        "genotype_calls": genotype_calls,
        "dosages": {rsid: dosages.get(rsid) for rsid in match_rsids},
        "evidence_stars": model.evidence_stars,
        "odds_ratio": model.odds_ratio,
        "penetrance": model.penetrance,
        "penetrance_text": penetrance_text,
        "absolute_risk_context": model.absolute_risk_context,
        "caveats": resolved_caveats,
        "sex_used": sex,
    }

    return RiskCall(
        model_id=model.id,
        gene_symbol=gene_symbol,
        rsid=",".join(match_rsids),
        risk_classification=model.risk_classification,
        evidence_stars=model.evidence_stars,
        finding_text=finding_text,
        zygosity=model.zygosity,
        detail=detail,
        pmids=model.pmids,
    )


class _SafeDict(dict):
    """format_map helper that leaves unknown placeholders untouched."""

    def __missing__(self, key: str) -> str:  # pragma: no cover - defensive
        return "{" + key + "}"


def classify(
    panel: RiskPanel,
    dosages: dict[str, int | None],
    readouts: dict[str, ProbeReadout],
    *,
    sex: str | None = None,
    inferred_ancestry: str | None = None,
    ancestry_fraction: float | None = None,
) -> RiskAssessment:
    """Evaluate the panel's genotype models against the computed dosages.

    Returns a :class:`RiskAssessment`. All-reference / no-model-fires yields an
    empty ``calls`` list (the carriage gate — no positive finding). Loci that are
    absent or no-call are listed in ``indeterminate_loci``.
    """
    assessment = RiskAssessment(
        module=panel.module,
        category=panel.category,
        dosages=dosages,
        readouts=readouts,
        sex_used=sex,
        inferred_ancestry=inferred_ancestry,
    )
    assessment.indeterminate_loci = [
        loc.rsid for loc in panel.loci if dosages.get(loc.rsid) is None
    ]

    matched = [m for m in panel.genotype_models if _model_matches(m, dosages)]
    if panel.evaluation == "first_match":
        matched = matched[:1]

    calls = [_render_finding(m, panel, dosages, readouts, sex) for m in matched]

    # Ancestry gate (e.g. APOL1): suppress or caveat calls outside the validated
    # ancestry so risk is never overstated for non-target populations.
    gate = panel.ancestry_gate
    if gate and calls:
        required = gate.get("required_ancestry")
        min_fraction = gate.get("min_fraction", 0.0)
        meets = inferred_ancestry == required and (
            ancestry_fraction is None or ancestry_fraction >= min_fraction
        )
        if not meets:
            mode = gate.get("mode", "caveat")
            note = gate.get(
                "note",
                f"These variants are validated only in {required} ancestry; this "
                f"result is not reported as actionable for your inferred ancestry "
                f"({inferred_ancestry or 'unknown'}).",
            )
            assessment.ancestry_suppressed = mode == "suppress"
            if mode == "suppress":
                # Drop the actionable finding; keep nothing user-facing as a high
                # risk call, but record the suppression on the assessment.
                calls = []
            else:  # caveat: keep but annotate
                calls = [_with_caveat(c, note) for c in calls]

    assessment.calls = calls
    return assessment


def _with_caveat(call: RiskCall, note: str) -> RiskCall:
    detail = dict(call.detail)
    detail["caveats"] = [*detail.get("caveats", []), note]
    return RiskCall(
        model_id=call.model_id,
        gene_symbol=call.gene_symbol,
        rsid=call.rsid,
        risk_classification=call.risk_classification,
        evidence_stars=call.evidence_stars,
        finding_text=f"{call.finding_text} {note}",
        zygosity=call.zygosity,
        detail=detail,
        pmids=call.pmids,
    )


# ── Findings storage ────────────────────────────────────────────────────────


def store_risk_findings(assessment: RiskAssessment, sample_engine: sa.Engine) -> int:
    """Store an assessment's calls in the ``findings`` table (idempotent).

    Clears existing rows for this (module, category) then inserts the fired
    calls. Risk-genotype findings carry ``clinvar_significance = NULL`` (these are
    risk genotypes, not ClinVar P/LP) and declarative evidence stars.
    """
    rows: list[dict[str, Any]] = []
    for call in assessment.calls:
        detail = dict(call.detail)
        detail["indeterminate_loci"] = assessment.indeterminate_loci
        rows.append(
            {
                "module": assessment.module,
                "category": assessment.category,
                "evidence_level": call.evidence_stars,
                "gene_symbol": call.gene_symbol,
                "rsid": call.rsid,
                "finding_text": call.finding_text,
                "conditions": call.risk_classification,
                "zygosity": call.zygosity,
                "clinvar_significance": None,
                "pmid_citations": json.dumps(call.pmids),
                "detail_json": json.dumps(detail),
            }
        )

    with sample_engine.begin() as conn:
        conn.execute(
            sa.delete(findings).where(
                findings.c.module == assessment.module,
                findings.c.category == assessment.category,
            )
        )
        if rows:
            conn.execute(sa.insert(findings), rows)

    logger.info(
        "risk_findings_stored",
        module=assessment.module,
        count=len(rows),
        indeterminate=len(assessment.indeterminate_loci),
        ancestry_suppressed=assessment.ancestry_suppressed,
    )
    return len(rows)
