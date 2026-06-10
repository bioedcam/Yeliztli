"""Live-path coverage for per-finding provenance stamping (SW-A4 / #8).

The unit tests in ``tests/backend/test_provenance.py`` exercise
``stamp_findings_provenance`` in isolation. This drives it through the real
pipeline via ``build_live_run`` (annotation → run_all_analyses → stamping, the
same order as the production Huey task), so a regression that silently breaks the
best-effort stamping step is caught — not merely the function in a vacuum.
"""

from __future__ import annotations

import json

from tests.backend.annotation_validation.test_m8_golden import (
    _GOLDEN_CLINVAR,
    _GOLDEN_VARIANTS,
)

_PROVENANCE_KEYS = {
    "pipeline_version",
    "pipeline_genome_build",
    "sources",
    "variation_ids",
    "annotation_coverage",
    "annotation_coverage_sources",
}


def test_live_findings_carry_provenance(build_live_run) -> None:
    """Every finding produced by a full live run is stamped with valid provenance."""
    run = build_live_run(variants=_GOLDEN_VARIANTS, clinvar=_GOLDEN_CLINVAR)

    assert run.findings, "fixture produced no findings — provenance check would be vacuous"
    for finding in run.findings:
        assert finding.provenance is not None, f"finding {finding.id} has NULL provenance"
        prov = json.loads(finding.provenance)
        assert set(prov) == _PROVENANCE_KEYS
        assert prov["pipeline_version"]
        # A finding linked to a variant pins that variant's rsid.
        if finding.rsid:
            assert prov["variation_ids"].get("rsid") == finding.rsid
