"""Live-path coverage for the finding-level change diff (SW-A4b / #8).

The unit tests in ``tests/backend/test_finding_diff.py`` exercise the pure diff
and the storage round-trip in isolation. This drives the diff against the
*real* findings produced by the live pipeline (``build_live_run`` →
annotation → ``run_all_analyses`` → provenance stamping), so a regression in how
findings are read/snapshotted from genuine rows is caught — not merely the
function in a vacuum.

The prior snapshot is the same live run with one source-driven field rolled back
(VUS instead of the current Pathogenic), under an older ClinVar release. That
faithfully models a re-annotation where ClinVar advanced and a variant was
reclassified — the exact case the banner exists to surface.
"""

from __future__ import annotations

import copy

from backend.analysis.finding_diff import (
    compute_and_store_finding_diff,
    has_changes,
    read_finding_diff,
    snapshot_findings,
)
from backend.db.tables import database_versions
from tests.backend.annotation_validation.test_m8_golden import (
    _GOLDEN_CLINVAR,
    _GOLDEN_VARIANTS,
)


def _set_clinvar_version(reference_engine, version: str) -> None:
    """Record a ClinVar release in database_versions so the diff can label it."""
    with reference_engine.begin() as conn:
        conn.execute(
            database_versions.insert().values(
                db_name="clinvar", version=version, genome_build="GRCh37"
            )
        )


def test_live_reclassification_surfaces_in_diff(build_live_run) -> None:
    run = build_live_run(variants=_GOLDEN_VARIANTS, clinvar=_GOLDEN_CLINVAR)

    current = snapshot_findings(run.sample_engine)
    assert current, "fixture produced no findings — diff check would be vacuous"

    # A real finding the live pipeline classified from ClinVar.
    pathogenic = sorted(
        (r for r in current if r["clinvar_significance"] == "Pathogenic"),
        key=lambda r: (r["module"], r["rsid"] or ""),
    )
    assert pathogenic, "expected at least one Pathogenic finding in the golden run"
    target = pathogenic[0]

    # Prior run: the same finding was a VUS under an older ClinVar release.
    prior = copy.deepcopy(current)
    for rec in prior:
        if (rec["module"], rec["rsid"]) == (target["module"], target["rsid"]):
            rec["clinvar_significance"] = "Uncertain_significance"
        rec["release_versions"] = {"clinvar": "2024-01"}

    # The current run pins the advanced ClinVar release.
    _set_clinvar_version(run.registry.reference_engine, "2024-06")

    stored = compute_and_store_finding_diff(
        run.sample_engine, run.registry.reference_engine, prior
    )

    assert has_changes(stored)
    matched = [
        c
        for c in stored["changed"]
        if c["module"] == target["module"] and c["rsid"] == target["rsid"]
    ]
    assert len(matched) == 1, "the reclassified finding should appear exactly once"
    (change,) = [c for c in matched[0]["changes"] if c["field"] == "clinvar_significance"]
    assert change == {
        "field": "clinvar_significance",
        "before": "Uncertain_significance",
        "after": "Pathogenic",
    }

    # The diff cites the source-release delta that explains the change.
    assert {"db_name": "clinvar", "before": "2024-01", "after": "2024-06"} in stored[
        "release_deltas"
    ]

    # And it persists for the API to read back.
    loaded = read_finding_diff(run.sample_engine)
    assert loaded is not None
    assert loaded["counts"]["changed"] == stored["counts"]["changed"]
    assert loaded["dismissed"] is False
