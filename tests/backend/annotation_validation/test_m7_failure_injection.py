"""M7 — Failure-injection & integrity (live path).

Drives the engine into failure modes the happy-path suite never reaches:
a crash mid-re-annotation (must not destroy the prior good result) and an
unreadable reference DB (must be recorded as a failure, not silently treated as
absent). The large-input streaming check (F39) is Phase-H / roadmap and is
skipped with a pointer.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from backend.annotation import engine as engine_mod
from backend.annotation.engine import run_annotation
from backend.db.tables import annotated_variants
from tests.backend.annotation_validation.conftest import clinvar_row

_VARIANTS = [
    {"rsid": "rs_a", "chrom": "7", "pos": 100, "genotype": "GA"},
    {"rsid": "rs_b", "chrom": "7", "pos": 200, "genotype": "AA"},
]
_CLINVAR = [
    clinvar_row("rs_a", "7", 100, "G", "A", "Pathogenic", 3),
    clinvar_row("rs_b", "7", 200, "G", "A", "Likely pathogenic", 2),
]


def _annotated_count(sample_engine: sa.Engine) -> int:
    with sample_engine.connect() as conn:
        return (
            conn.execute(sa.select(sa.func.count()).select_from(annotated_variants)).scalar() or 0
        )


# ── F28: a crash mid-re-annotation must not destroy the prior result ──────


def test_crash_during_reannotation_preserves_prior(build_live_run, monkeypatch) -> None:
    run = build_live_run(variants=_VARIANTS, clinvar=_CLINVAR)
    before = _annotated_count(run.sample_engine)
    assert before > 0, "precondition: first annotation populated the table"

    # Simulate a worker crash partway through the second (re-)annotation.
    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated worker crash mid-batch")

    monkeypatch.setattr(engine_mod, "_bulk_upsert", _boom)
    with pytest.raises(Exception):  # noqa: B017 - any failure path is fine
        run_annotation(run.sample_engine, run.registry)

    # The prior good annotation must survive the crash.
    assert _annotated_count(run.sample_engine) == before


# ── F29: an unreadable source DB must be recorded, not silently dropped ───


def test_unreadable_source_is_recorded(build_live_run, monkeypatch) -> None:
    run = build_live_run(variants=_VARIANTS, clinvar=_CLINVAR)

    def _raise(_self):
        raise RuntimeError("database is locked")

    # Make the dbNSFP engine unreadable on the next run.
    monkeypatch.setattr(type(run.registry), "dbnsfp_engine", property(_raise))
    result = run_annotation(run.sample_engine, run.registry)

    # The unreadable source is recorded as a failure, not silently treated as
    # absent (the F29 defect: a locked/corrupt DB indistinguishable from a
    # genuinely-uninstalled one, with the run still reported complete).
    assert "dbnsfp" in result.source_failures, (
        f"unreadable dbNSFP not recorded; source_failures={result.source_failures}"
    )
    # ...and the rest of the annotation still completes from the readable sources.
    assert result.rows_written > 0
    assert result.clinvar_matched > 0


# ── F39: large-input streaming / memory ceiling — Phase H (roadmap) ───────


@pytest.mark.skip(
    reason="F39: yield_per/streaming is Phase H (WGS/WES roadmap); "
    "chip-scale inputs are not memory-bound"
)
def test_large_input_uses_streaming() -> None:  # pragma: no cover
    raise NotImplementedError
