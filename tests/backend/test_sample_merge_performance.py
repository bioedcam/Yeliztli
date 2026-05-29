"""Step 85 / MRG-09a: merge perf — two 700k-variant samples in <30 s.

Marked ``@pytest.mark.slow`` so it stays dormant on every PR-blocking run
and only fires in the nightly slow-tier workflow (Plan §16.5 / Step 42).
The plan's WSL2-reference budget covers the merge service's measurable
footprint — Plan §10.5 steps 1 – 8 in
:func:`backend.services.sample_merge.merge_samples` — under the
"parse-from-DB + merge + write + annotate" rubric:

* **parse-from-DB** = step 3 (``_stream_raw_variants`` on both source DBs).
* **merge**         = step 4 (``_apply_semantics`` over the coord union,
                     plus step 2's VEP-bundle conflict probe).
* **write**         = steps 5 – 7 (insert ``samples`` row, materialise the
                     per-sample DB via ``create_sample_tables(...,
                     is_merged_sample=True)``, batch-write merged rows,
                     write the single ``merge_provenance`` row).
* **annotate**      = step 8 (enqueue the standard annotation job —
                     ``create_annotation_job`` + ``run_annotation_task``).

The Huey enqueue at step 8 is monkey-patched to a no-op (the same pattern
``test_sample_merge.py`` / ``test_sample_merge_full_pipeline.py`` use)
because the production flow runs the annotation pass *asynchronously* on
the Huey worker pool — the user clicks "Merge", ``merge_samples`` returns,
and the SSE job channel streams annotation progress separately. Treating
the enqueue as the budget's terminal call matches the merge-service
internals: ``_DURATION_BASELINE_SECONDS = 5`` + ``_DURATION_ROWS_PER_SECOND
= 25_000`` in ``backend/services/sample_merge.py`` projects 33 s for a
700k-merged sample, which the wizard renders as ``est_duration_seconds``
and which Step 85 / MRG-09a anchors as the <30 s upper bound on the
in-process work.

Sample design — same-coord overlap + small discordance/no-call drift:
    * S1 carries 700 000 synthetic variants from
      :func:`scripts.benchmark.generate_raw_variants` (seed=42).
    * S2 reuses every S1 coordinate but mutates 1% of genotypes to a
      different call (forces ``discordant`` rows under all three §10.3
      strategies) and another 1% to ``--`` (forces ``filled_nocall`` rows).
    * Result: the merge produces ~700 000 merged rows — the realistic
      AncestryDNA / 23andMe overlap profile (~500 000 shared positions
      with small drift). The remaining 98% land in the ``match`` bucket.
    * Exercises every §10.2 bucket except ``unique`` (same-coord design)
      and the cross-rsid collapse path — both are covered by the
      correctness-focused suites (``test_sample_merge.py`` /
      ``test_alt_rsid_collapse.py``). MRG-09a is purely a timing guard;
      bucket coverage is not in scope here.

Setup (variant generation, source-DB writes, registry construction) is
intentionally excluded from the timed window because none of it runs when
a user clicks "Merge" — the production flow's source DBs already exist by
the time the merge wizard fires.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from backend.config import Settings
from backend.db.connection import DBRegistry, get_registry, reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    annotation_state,
    database_versions,
    individuals,
    jobs,
    merge_provenance,
    raw_variants,
    reference_metadata,
    samples,
)
from backend.services.sample_merge import MergeStrategy, merge_samples
from scripts.benchmark import generate_raw_variants

# Plan §15.3 DoD — merge service end-to-end on the WSL2 reference machine
# for two 700k-variant samples. The threshold is a hard ceiling; the CI
# workflow auto-files a ``slow-tier-regression`` issue on failure but does
# not block the PR (Plan §16.5).
_SAMPLE_SIZE = 700_000
_PERF_BUDGET_SECONDS = 30.0
# Drift rates that drive ~1% discordant + ~1% filled_nocall merge buckets
# against the S1 baseline; the remaining ~98% land in ``match``. Coords are
# always shared between the two samples so the merge never takes the
# ``unique`` path (that's a separate correctness concern covered elsewhere).
_DISCORDANCE_RATE = 100  # one in N
_NOCALL_RATE = 50  # one in N — picked so 1/_NOCALL_RATE - 1/_DISCORDANCE_RATE ≈ 1%


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def perf_registry(tmp_data_dir: Path):
    """Real on-disk DBRegistry pointed at ``tmp_data_dir``.

    Mirrors the ``merge_registry`` fixture in ``test_sample_merge.py`` /
    ``test_sample_merge_full_pipeline.py`` (Step 65 / Step 83): patches
    ``backend.db.connection.get_settings`` so the singleton resolves
    against the temp data dir and the staleness service reads from the
    same registry the merge service writes to.

    ``database_versions['vep_bundle'].version = 'v2.0.0'`` is pre-stamped
    so :func:`backend.services.staleness.is_sample_stale` matches the
    ``annotation_state.vep_bundle_version`` each source seeds — without
    that match the §10.5 step 1 stale-source guard would raise HTTP 423
    and short-circuit the perf measurement.
    """
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)
    with ref_engine.begin() as conn:
        conn.execute(
            database_versions.insert().values(
                db_name="vep_bundle",
                version="v2.0.0",
                downloaded_at=datetime.now(UTC),
            )
        )
    ref_engine.dispose()

    with patch("backend.db.connection.get_settings", return_value=settings):
        reset_registry()
        registry = get_registry()
        try:
            yield registry
        finally:
            registry.dispose_all()
            reset_registry()


def _create_individual(registry: DBRegistry, display_name: str) -> int:
    with registry.reference_engine.begin() as conn:
        result = conn.execute(
            individuals.insert().values(
                display_name=display_name,
                notes="",
                updated_at=datetime.now(UTC),
            )
        )
    return int(result.inserted_primary_key[0])


def _create_source_sample(
    registry: DBRegistry,
    *,
    individual_id: int,
    name: str,
    file_format: str,
    file_hash: str,
    variants: list[dict],
) -> int:
    """Allocate a fresh source-sample row + per-sample DB primed with ``variants``.

    Same pattern as ``test_sample_merge.py::_create_source_sample`` —
    duplicated rather than imported to keep this perf module standalone
    (matches the Step 83 file's choice for the same reason).
    """
    now = datetime.now(UTC)
    with registry.reference_engine.begin() as conn:
        result = conn.execute(
            samples.insert().values(
                name=name,
                db_path="",
                file_format=file_format,
                file_hash=file_hash,
                individual_id=individual_id,
                created_at=now,
                updated_at=now,
            )
        )
        sample_id = int(result.inserted_primary_key[0])
        db_path = f"samples/sample_{sample_id}.db"
        conn.execute(samples.update().where(samples.c.id == sample_id).values(db_path=db_path))
        conn.execute(
            jobs.insert().values(
                job_id=f"job-{sample_id}",
                sample_id=sample_id,
                job_type="annotation",
                status="complete",
                progress_pct=100.0,
                message="",
                created_at=now,
                updated_at=now,
            )
        )

    sample_db_path = registry.settings.data_dir / db_path
    sample_db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = registry.get_sample_engine(sample_db_path)
    create_sample_tables(engine, is_merged_sample=False)

    # Bulk-insert in 50k batches — same chunking as the production ingest
    # path (``backend/api/routes/ingest.py``) so the source-DB write time
    # stays out of the perf measurement window in a representative way.
    with engine.begin() as conn:
        batch = 50_000
        for i in range(0, len(variants), batch):
            conn.execute(raw_variants.insert(), variants[i : i + batch])
        conn.execute(
            annotation_state.insert().values(
                key="vep_bundle_version",
                value="v2.0.0",
                updated_at=now,
            )
        )
    return sample_id


def _drift_for_s2(base: list[dict]) -> list[dict]:
    """Derive S2's variants by mutating S1's genotypes on two interleaved cadences.

    The merge service treats genotype-equal-and-rsid-equal-and-coord-equal
    rows as ``match``, genotype-different as ``discordant``, and one-side
    no-call as ``filled_nocall``. Splitting the drift into a 1% discordance
    cadence + a 1% no-call cadence (with the discordance cadence striding
    first so neither overlaps) hits all three buckets without inflating
    the merged row count (every row is at a shared coord so no
    ``unique_*`` rows are emitted).
    """
    out: list[dict] = []
    for i, v in enumerate(base):
        new = dict(v)
        if i % _DISCORDANCE_RATE == 0:
            # Force a different genotype — flip to a sentinel pair that
            # cannot collide with the random S1 genotype regardless of the
            # generator's choice. ``GG`` is in the GENOTYPES pool but the
            # collision rate is ~1/6 so an additional flip to ``TT`` when
            # S1 is already ``GG`` keeps drift cleanly discordant.
            new["genotype"] = "TT" if v["genotype"] == "GG" else "GG"
        elif i % _NOCALL_RATE == 0:
            new["genotype"] = "--"
        out.append(new)
    return out


def _noop_annotation_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the Huey enqueue at the tail of ``merge_samples`` with a no-op.

    Production runs the annotation pass *asynchronously* on the Huey worker
    pool — ``merge_samples`` returns as soon as the job is enqueued. The
    perf budget tracks the in-process merge call only; the async
    annotation pass is measured separately by Step 42's nightly real-
    bundle test (`test_annotation_engine_ancestrydna_real_bundle.py`) and
    by ``test_benchmark.py::test_annotation_600k_timing``.

    Leaving the real enqueue in place here would either fire a synchronous
    annotation pass (the test environment doesn't run a Huey worker) or
    block on a worker that never picks up the job, neither of which the
    30 s budget accommodates.
    """
    import backend.tasks.huey_tasks as huey_tasks

    monkeypatch.setattr(huey_tasks, "create_annotation_job", lambda _sid: "noop-job")
    monkeypatch.setattr(huey_tasks, "run_annotation_task", lambda *_a, **_kw: None)


# ── Test ─────────────────────────────────────────────────────────────


@pytest.mark.slow
class TestSampleMergePerformance:
    """Plan §15.3 DoD — merge service end-to-end on two 700k-variant samples in <30 s."""

    def test_700k_merge_completes_under_perf_budget(
        self,
        perf_registry: DBRegistry,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Plan §15.3: end-to-end perf guard on the merge service."""
        # ── Setup (excluded from the timed window) ─────────────────────
        base = generate_raw_variants(_SAMPLE_SIZE, seed=42)
        s1_variants = base
        s2_variants = _drift_for_s2(base)

        _noop_annotation_enqueue(monkeypatch)
        individual_id = _create_individual(perf_registry, "Perf Test Individual")
        s1_id = _create_source_sample(
            perf_registry,
            individual_id=individual_id,
            name="perf_23andme.txt",
            file_format="23andme_v5",
            file_hash="hash_s1_perf",
            variants=s1_variants,
        )
        s2_id = _create_source_sample(
            perf_registry,
            individual_id=individual_id,
            name="perf_ancestrydna.txt",
            file_format="ancestrydna_v2.0",
            file_hash="hash_s2_perf",
            variants=s2_variants,
        )

        # ── Timed window — Plan §15.3 budget ───────────────────────────
        t0 = time.perf_counter()
        merged_id = merge_samples(
            perf_registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="Perf Test (merged)",
        )
        elapsed = time.perf_counter() - t0

        # ── Assertions ────────────────────────────────────────────────
        # Merge produced ~_SAMPLE_SIZE rows — every coord is shared
        # between S1 and S2 in the drift design, so no ``unique_*`` rows
        # are emitted.
        with perf_registry.reference_engine.connect() as conn:
            merged_db_path = conn.execute(
                sa.select(samples.c.db_path).where(samples.c.id == merged_id)
            ).scalar_one()
        merged_engine = perf_registry.get_sample_engine(
            perf_registry.settings.data_dir / merged_db_path
        )
        with merged_engine.connect() as conn:
            merged_row_count = conn.execute(
                sa.select(sa.func.count()).select_from(raw_variants)
            ).scalar_one()
            prov_row = conn.execute(sa.select(merge_provenance)).fetchone()
        # Drift-design invariant: every S2 coord is also an S1 coord, so the
        # merged row count tracks the unique-coord cardinality of S1 alone —
        # which is slightly below ``_SAMPLE_SIZE`` because
        # ``generate_raw_variants`` samples ``(chrom, pos)`` from
        # ``(22 chroms) × randint(10_000, 250_000_000)`` and the birthday
        # paradox produces a handful of coord collisions per 700 k draw.
        # The lower bound below (≥99.9% of input) locks "every coord shared"
        # while tolerating the synthetic generator's collision rate; the
        # upper bound (≤_SAMPLE_SIZE) catches an accidental ``unique_*`` row
        # leak from a future regression in ``_apply_semantics``.
        assert _SAMPLE_SIZE * 0.999 <= merged_row_count <= _SAMPLE_SIZE, (
            f"merged sample carries {merged_row_count} rows; "
            f"expected ~{_SAMPLE_SIZE} (drift design — all S2 coords shared "
            "with S1 modulo synthetic-generator coord collisions)"
        )
        # ``merge_provenance`` row exists — locks the Plan §10.5 step 7 write.
        assert prov_row is not None

        # Perf budget — Plan §15.3 DoD.
        with capsys.disabled():
            print(
                f"\n  Merge perf: {elapsed:.2f}s "
                f"({_SAMPLE_SIZE:,} × 2 sources → {merged_row_count:,} merged rows)"
            )
        assert elapsed < _PERF_BUDGET_SECONDS, (
            f"merge_samples took {elapsed:.2f}s, exceeds "
            f"{_PERF_BUDGET_SECONDS:.0f}s budget (Plan §15.3 DoD on WSL2 "
            "reference machine). If the bundle was legitimately re-cut "
            "with materially more rsid coverage, the perf model in "
            "backend/services/sample_merge.py::_estimate_duration_seconds "
            "needs re-tuning alongside this budget."
        )
