"""Step 80 / MRG-08e: stale-source block on the merge commit route.

Plan §15.1 MRG-08e: ``mark one source as stale; call POST
/api/individuals/{id}/merge → assert HTTP 423 with payload naming the
stale source sample.``

Companion to:

* ``tests/backend/test_sample_merge_preview.py::TestStaleSource`` — same
  gate against the dry-run route (Plan §15.1 MRG-03a case (ii)). Locks
  preview's stale-source 423 + no-write contract.
* ``tests/backend/test_merge_routes.py::TestMergeCommitRoute
  ::test_stale_source_returns_423`` — the basic plumbing assertion on
  the commit route's 423 (single stale source). This file is the full
  Plan §15.1 MRG-08e surface and additionally locks:

  * Symmetric coverage on both source positions (``s1`` stale, ``s2``
    stale, both stale) so a future refactor cannot silently regress one
    branch.
  * Full Plan §7.5 payload shape (``error`` / ``stale_sample_ids`` /
    ``message`` / ``reannotate_url``).
  * Side-effect freedom: no ``samples`` row, no per-sample DB file, no
    ``jobs`` row created — the gate must short-circuit before any write
    or enqueue fires.
  * Plan §7.4 missing-state fallback: a source whose per-sample DB has
    no ``annotation_state`` row routes through the same stale path
    (this is the commit-route mirror of test_sample_merge_preview.py's
    case (vii), needed because the commit route's Huey enqueue is a
    distinct side-effect surface from the preview's read-only path).

The Huey enqueue at the tail of ``merge_samples`` is stubbed via
``monkeypatch`` so the test never actually fires the annotation
pipeline. The stub is the same shape ``test_merge_routes.py`` uses so
the contract under test is the route's stale-source 423, not the
enqueue behaviour. Side-effect-freedom assertions then prove the stub
was never called.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    annotation_state,
    database_versions,
    individuals,
    jobs,
    raw_variants,
    reference_metadata,
    samples,
)

# ── Variant batches mirroring test_sample_merge_preview.py ─────────────
#
# Identical seven-locus shape so the stale gate fires the same way it
# does in the preview tests. The variant content is irrelevant to the
# stale-source gate (which short-circuits before _stream_raw_variants
# runs), but matching the preview fixture keeps the file readable as a
# direct commit-route mirror.


def _v(rsid: str, chrom: str, pos: int, genotype: str) -> dict:
    return {"rsid": rsid, "chrom": chrom, "pos": pos, "genotype": genotype}


S1_VARIANTS = [
    _v("rs100", "1", 100, "AG"),
    _v("rs200", "1", 200, "CT"),
    _v("rs300", "1", 300, "--"),
    _v("rs400", "1", 400, "AA"),
    _v("rs500", "2", 500, "GG"),
    _v("rs700_s1", "3", 700, "CT"),
]

S2_VARIANTS = [
    _v("rs100", "1", 100, "AG"),
    _v("rs200", "1", 200, "--"),
    _v("rs300", "1", 300, "GG"),
    _v("rs400", "1", 400, "GG"),
    _v("rs600", "2", 600, "AT"),
    _v("rs700_s2", "3", 700, "CT"),
]


# ── Fixture helpers (parallel to test_sample_merge_preview.py) ────────


def _seed_installed_vep_bundle(reference_engine: sa.Engine, version: str = "v2.0.0") -> None:
    """Seed ``database_versions['vep_bundle']`` so staleness can compare.

    The installed bundle is v2.0.0; sources marked at v1.0.0 below are
    stale by ``is_sample_stale``'s major-version contract.
    """
    with reference_engine.begin() as conn:
        conn.execute(
            sa.delete(database_versions).where(database_versions.c.db_name == "vep_bundle")
        )
        conn.execute(
            database_versions.insert().values(
                db_name="vep_bundle",
                version=version,
                downloaded_at=datetime.now(UTC),
            )
        )


def _seed_source_sample(
    registry,
    *,
    individual_id: int | None,
    name: str,
    file_format: str,
    file_hash: str,
    variants: list[dict],
    bundle_version: str | None = "v2.0.0",
    annotation_status: str = "complete",
) -> int:
    """Create one source sample (samples row + per-sample DB + jobs row).

    ``bundle_version=None`` skips the ``annotation_state`` write, which
    is the case ``TestMissingAnnotationStateFallback`` setup: a per-sample
    DB whose ``annotation_state`` row is absent. Plan §7.4's missing-state
    fallback then treats the sample as v1.0.0 and the stale-source gate
    fires.
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
                job_id=f"job-src-{sample_id}",
                sample_id=sample_id,
                job_type="annotation",
                status=annotation_status,
                progress_pct=100.0 if annotation_status == "complete" else 0.0,
                message="",
                created_at=now,
                updated_at=now,
            )
        )

    sample_db_path = registry.settings.data_dir / db_path
    sample_db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = registry.get_sample_engine(sample_db_path)
    create_sample_tables(engine, is_merged_sample=False)
    with engine.begin() as conn:
        if variants:
            conn.execute(raw_variants.insert(), variants)
        if bundle_version is not None:
            conn.execute(
                annotation_state.insert().values(
                    key="vep_bundle_version",
                    value=bundle_version,
                    updated_at=now,
                )
            )
    return sample_id


def _stub_huey_and_record_calls(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Stub Huey ``create_annotation_job`` and return the call recorder.

    The stale-source gate fires inside ``_validate_samples_and_freshness``,
    well before ``merge_samples`` reaches its enqueue step. The returned
    list is appended to every time the stub fires — every test in this
    file asserts the list is empty, locking the "gate short-circuits
    before any side effect" contract.

    ``run_annotation_task`` is stubbed to a no-op for symmetry but is
    not part of the call-tracking surface; the route never invokes it
    directly (Huey would).
    """
    import backend.tasks.huey_tasks as huey_tasks

    calls: list[int] = []

    def _stub_create_annotation_job(sample_id: int) -> str:
        calls.append(sample_id)
        return f"job-merged-{sample_id}"

    monkeypatch.setattr(huey_tasks, "create_annotation_job", _stub_create_annotation_job)
    monkeypatch.setattr(huey_tasks, "run_annotation_task", lambda *_a, **_k: None)
    return calls


@pytest.fixture
def stale_merge_client(tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """TestClient + pre-seeded individual + two healthy source samples.

    Each test then mutates one (or both) sources to a stale state before
    POSTing to ``/api/individuals/{id}/merge``. Stashed on the client:

    * ``individual_id`` — the owning individual.
    * ``s1_id`` / ``s2_id`` — two source samples; both ``status=complete``
      with ``annotation_state.vep_bundle_version='v2.0.0'`` so neither is
      stale at fixture exit.
    * ``settings`` / ``registry`` — mirror ``test_sample_merge_preview.py``
      so tests can mutate per-sample state without re-deriving them.
    * ``enqueue_calls`` — list captured from the Huey stub; every test
      asserts ``len(enqueue_calls) == 0`` after the 423 to prove the
      gate short-circuited before the enqueue step.

    The installed VEP bundle is seeded at v2.0.0 so the staleness
    comparison has both sides of the inequality to work with.
    """
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)
    ref_engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()
        from backend.db.connection import get_registry
        from backend.main import create_app

        registry = get_registry()
        _seed_installed_vep_bundle(registry.reference_engine, "v2.0.0")
        enqueue_calls = _stub_huey_and_record_calls(monkeypatch)

        with registry.reference_engine.begin() as conn:
            result = conn.execute(
                individuals.insert().values(
                    display_name="Jane Doe",
                    notes="",
                    updated_at=datetime.now(UTC),
                )
            )
            individual_id = int(result.inserted_primary_key[0])

        s1_id = _seed_source_sample(
            registry,
            individual_id=individual_id,
            name="jane_23andme.txt",
            file_format="23andme_v5",
            file_hash="hash_s1",
            variants=S1_VARIANTS,
        )
        s2_id = _seed_source_sample(
            registry,
            individual_id=individual_id,
            name="jane_ancestrydna.txt",
            file_format="ancestrydna_v2.0",
            file_hash="hash_s2",
            variants=S2_VARIANTS,
        )

        app = create_app()
        with TestClient(app) as tc:
            tc.individual_id = individual_id  # type: ignore[attr-defined]
            tc.s1_id = s1_id  # type: ignore[attr-defined]
            tc.s2_id = s2_id  # type: ignore[attr-defined]
            tc.settings = settings  # type: ignore[attr-defined]
            tc.registry = registry  # type: ignore[attr-defined]
            tc.enqueue_calls = enqueue_calls  # type: ignore[attr-defined]
            yield tc

        reset_registry()


# ── Snapshot helpers (the "no rows written" assertion) ────────────────


def _snapshot_samples_row_count(registry) -> int:
    with registry.reference_engine.connect() as conn:
        return conn.execute(sa.select(sa.func.count()).select_from(samples)).scalar() or 0


def _snapshot_per_sample_db_files(registry) -> list[Path]:
    return sorted((registry.settings.data_dir / "samples").glob("sample_*.db"))


def _snapshot_jobs_row_count(registry) -> int:
    with registry.reference_engine.connect() as conn:
        return conn.execute(sa.select(sa.func.count()).select_from(jobs)).scalar() or 0


# ── Mutator helpers ────────────────────────────────────────────────────


def _mark_sample_stale(client: TestClient, sample_id: int) -> None:
    """Rewrite a source sample's ``annotation_state`` to ``v1.0.0``.

    Forces ``is_sample_stale(sample_id)`` to return True against the v2
    installed bundle.
    """
    registry = client.registry  # type: ignore[attr-defined]
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.db_path).where(samples.c.id == sample_id)
        ).fetchone()
    assert row is not None
    engine = registry.get_sample_engine(registry.settings.data_dir / row.db_path)
    with engine.begin() as conn:
        conn.execute(
            sa.update(annotation_state)
            .where(annotation_state.c.key == "vep_bundle_version")
            .values(value="v1.0.0")
        )


def _delete_annotation_state(client: TestClient, sample_id: int) -> None:
    """Drop ``annotation_state`` row — mimics a pre-Phase-0 restored DB.

    Per Plan §7.4 the staleness service treats this as ``v1.0.0`` and
    the merge service routes through the stale-source path.
    """
    registry = client.registry  # type: ignore[attr-defined]
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.db_path).where(samples.c.id == sample_id)
        ).fetchone()
    assert row is not None
    engine = registry.get_sample_engine(registry.settings.data_dir / row.db_path)
    with engine.begin() as conn:
        conn.execute(
            sa.delete(annotation_state).where(annotation_state.c.key == "vep_bundle_version")
        )


def _post_merge(client: TestClient):
    """POST the canonical merge body and return the response."""
    return client.post(
        f"/api/individuals/{client.individual_id}/merge",  # type: ignore[attr-defined]
        json={
            "source_sample_ids": [
                client.s1_id,  # type: ignore[attr-defined]
                client.s2_id,  # type: ignore[attr-defined]
            ],
            "strategy": "flag_only",
            "display_name": "Jane Doe (merged)",
        },
    )


# ══════════════════════════════════════════════════════════════════════
# Stale source — single side (s1, s2)
# ══════════════════════════════════════════════════════════════════════


class TestStaleSourceSingleSide:
    """Plan §15.1 MRG-08e: one stale source short-circuits the commit route.

    Symmetric coverage on both source positions — the validation loop
    walks ``(s1, s2)`` in order and a future refactor that breaks the
    second branch would otherwise go unnoticed.
    """

    def test_s1_stale_returns_423(self, stale_merge_client: TestClient) -> None:
        _mark_sample_stale(stale_merge_client, stale_merge_client.s1_id)  # type: ignore[attr-defined]
        resp = _post_merge(stale_merge_client)
        assert resp.status_code == 423, resp.text

    def test_s2_stale_returns_423(self, stale_merge_client: TestClient) -> None:
        _mark_sample_stale(stale_merge_client, stale_merge_client.s2_id)  # type: ignore[attr-defined]
        resp = _post_merge(stale_merge_client)
        assert resp.status_code == 423, resp.text

    def test_s1_stale_payload_names_only_s1(self, stale_merge_client: TestClient) -> None:
        """Plan §15.1 MRG-08e: payload names the stale source sample.

        The stale list contains exactly s1 — s2 is fresh and must not
        appear, otherwise the wizard would surface an incorrect
        re-annotate suggestion against the wrong sample.
        """
        _mark_sample_stale(stale_merge_client, stale_merge_client.s1_id)  # type: ignore[attr-defined]
        resp = _post_merge(stale_merge_client)
        assert resp.status_code == 423
        detail = resp.json()["detail"]
        assert detail["stale_sample_ids"] == [stale_merge_client.s1_id]  # type: ignore[attr-defined]
        assert stale_merge_client.s2_id not in detail["stale_sample_ids"]  # type: ignore[attr-defined]

    def test_s2_stale_payload_names_only_s2(self, stale_merge_client: TestClient) -> None:
        _mark_sample_stale(stale_merge_client, stale_merge_client.s2_id)  # type: ignore[attr-defined]
        resp = _post_merge(stale_merge_client)
        assert resp.status_code == 423
        detail = resp.json()["detail"]
        assert detail["stale_sample_ids"] == [stale_merge_client.s2_id]  # type: ignore[attr-defined]
        assert stale_merge_client.s1_id not in detail["stale_sample_ids"]  # type: ignore[attr-defined]


# ══════════════════════════════════════════════════════════════════════
# Stale source — both sides
# ══════════════════════════════════════════════════════════════════════


class TestStaleSourceBothSides:
    """Both sources stale: payload lists both, preserving source order.

    Plan §15.1 MRG-08e names "the stale source sample" in the singular,
    but the underlying service collects every stale id (the loop in
    ``_validate_samples_and_freshness`` runs both checks before raising).
    Asserting both ids surface is the only way the wizard can render a
    re-annotate CTA per stale source — losing one would yield a
    half-recovered state that re-fails the gate after the user clicks.
    """

    def test_both_stale_returns_423_with_both_ids(self, stale_merge_client: TestClient) -> None:
        _mark_sample_stale(stale_merge_client, stale_merge_client.s1_id)  # type: ignore[attr-defined]
        _mark_sample_stale(stale_merge_client, stale_merge_client.s2_id)  # type: ignore[attr-defined]
        resp = _post_merge(stale_merge_client)
        assert resp.status_code == 423, resp.text
        detail = resp.json()["detail"]
        assert detail["stale_sample_ids"] == [
            stale_merge_client.s1_id,  # type: ignore[attr-defined]
            stale_merge_client.s2_id,  # type: ignore[attr-defined]
        ]


# ══════════════════════════════════════════════════════════════════════
# Payload shape (Plan §7.5)
# ══════════════════════════════════════════════════════════════════════


class TestStalePayloadShape:
    """Plan §7.5 contract: full structured detail dict.

    The commit route surfaces the same ``StaleSourceError.detail`` payload
    the preview route does (Plan §15.1 MRG-03a case (ii)). The wizard
    renders a single banner for either route, so the keys must match.
    """

    def test_payload_carries_full_plan_7_5_keys(self, stale_merge_client: TestClient) -> None:
        _mark_sample_stale(stale_merge_client, stale_merge_client.s1_id)  # type: ignore[attr-defined]
        resp = _post_merge(stale_merge_client)
        assert resp.status_code == 423
        detail = resp.json()["detail"]
        assert set(detail.keys()) == {
            "error",
            "stale_sample_ids",
            "message",
            "reannotate_url",
        }
        assert detail["error"] == "stale_source_sample"
        assert "Re-annotate" in detail["message"]
        assert detail["reannotate_url"] == "/api/annotation/{sample_id}"


# ══════════════════════════════════════════════════════════════════════
# Side-effect freedom (no merged sample, no enqueue)
# ══════════════════════════════════════════════════════════════════════


class TestStaleSourceWritesNoArtefacts:
    """Stale-source 423 must short-circuit before any write or enqueue.

    The commit route's side-effect surface is larger than the preview's:
    a successful merge writes a new ``samples`` row, a new per-sample DB
    file, a ``merge_provenance`` row, and a ``jobs`` row from the Huey
    enqueue. Locking each of those as untouched on the 423 path means a
    future refactor that re-orders validation cannot quietly leak a
    half-written merge.
    """

    def test_no_new_samples_row(self, stale_merge_client: TestClient) -> None:
        registry = stale_merge_client.registry  # type: ignore[attr-defined]
        pre_count = _snapshot_samples_row_count(registry)
        _mark_sample_stale(stale_merge_client, stale_merge_client.s1_id)  # type: ignore[attr-defined]
        resp = _post_merge(stale_merge_client)
        assert resp.status_code == 423
        assert _snapshot_samples_row_count(registry) == pre_count

    def test_no_new_per_sample_db_file(self, stale_merge_client: TestClient) -> None:
        registry = stale_merge_client.registry  # type: ignore[attr-defined]
        pre_files = _snapshot_per_sample_db_files(registry)
        _mark_sample_stale(stale_merge_client, stale_merge_client.s2_id)  # type: ignore[attr-defined]
        resp = _post_merge(stale_merge_client)
        assert resp.status_code == 423
        assert _snapshot_per_sample_db_files(registry) == pre_files

    def test_no_huey_enqueue(self, stale_merge_client: TestClient) -> None:
        """The Huey enqueue stub records every call; assert it was untouched.

        Plan §10.5 step 8 is the enqueue; the stale-source gate at step 1
        must short-circuit before it. The stub is held in
        ``stale_merge_client.enqueue_calls`` (set up in the fixture).
        """
        _mark_sample_stale(stale_merge_client, stale_merge_client.s1_id)  # type: ignore[attr-defined]
        resp = _post_merge(stale_merge_client)
        assert resp.status_code == 423
        assert stale_merge_client.enqueue_calls == []  # type: ignore[attr-defined]

    def test_no_new_jobs_row(self, stale_merge_client: TestClient) -> None:
        """Cross-check the enqueue-stub assertion against the jobs table.

        The fixture seeds two ``jobs`` rows (one per source sample's
        annotation). A successful merge would add a third on the Huey
        enqueue path; the stale-source gate must leave the count
        untouched.
        """
        registry = stale_merge_client.registry  # type: ignore[attr-defined]
        pre_count = _snapshot_jobs_row_count(registry)
        _mark_sample_stale(stale_merge_client, stale_merge_client.s2_id)  # type: ignore[attr-defined]
        resp = _post_merge(stale_merge_client)
        assert resp.status_code == 423
        assert _snapshot_jobs_row_count(registry) == pre_count


# ══════════════════════════════════════════════════════════════════════
# Plan §7.4 missing-state fallback
# ══════════════════════════════════════════════════════════════════════


class TestMissingAnnotationStateFallback:
    """Plan §7.4: a missing ``annotation_state`` row → v1.0.0 fallback.

    Mirrors test_sample_merge_preview.py's case (vii) against the commit
    route. The fallback is the recovery path for backup-restored
    pre-Phase-0 samples, and the commit route's larger side-effect
    surface makes the no-write assertion meaningful here in a way the
    preview file's already covers separately.
    """

    def test_missing_state_routes_through_stale_path(self, stale_merge_client: TestClient) -> None:
        _delete_annotation_state(
            stale_merge_client,
            stale_merge_client.s1_id,  # type: ignore[attr-defined]
        )
        resp = _post_merge(stale_merge_client)
        assert resp.status_code == 423, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == "stale_source_sample"
        assert stale_merge_client.s1_id in detail["stale_sample_ids"]  # type: ignore[attr-defined]
        assert detail["reannotate_url"] == "/api/annotation/{sample_id}"

    def test_missing_state_writes_no_artefacts(self, stale_merge_client: TestClient) -> None:
        registry = stale_merge_client.registry  # type: ignore[attr-defined]
        pre_samples = _snapshot_samples_row_count(registry)
        pre_files = _snapshot_per_sample_db_files(registry)
        pre_jobs = _snapshot_jobs_row_count(registry)

        _delete_annotation_state(
            stale_merge_client,
            stale_merge_client.s2_id,  # type: ignore[attr-defined]
        )
        resp = _post_merge(stale_merge_client)
        assert resp.status_code == 423

        assert _snapshot_samples_row_count(registry) == pre_samples
        assert _snapshot_per_sample_db_files(registry) == pre_files
        assert _snapshot_jobs_row_count(registry) == pre_jobs
        assert stale_merge_client.enqueue_calls == []  # type: ignore[attr-defined]
