"""Step 81 / MRG-08h — ``alt_rsid`` population on rsid collapse.

Plan §10.2 step 2 spells out the tiebreaker for a discordant-rsid collapse:
two source rows at the same ``(chrom, pos)`` collapse to one row in the
merged sample; the chosen rsid prefers (a) the rsid present in the VEP
bundle catalog, else (b) S1's rsid. The discarded rsid lands in the new
``raw_variants.alt_rsid`` column introduced by Step 63 / Plan §10.4(b);
``merge_provenance.concordance_summary.collapsed_rsid`` counts the loci
where this tiebreaker fired.

These tests lock the integration-level contract for that column:

- Exactly one row exists at each ``(chrom, pos)`` even when the two
  sources disagree on rsid.
- ``rsid`` matches the §10.2 step 2 tiebreaker — S1 wins under the
  no-bundle fallback, and the bundle-hit branch flips the winner to
  whichever side has its rsid in ``vep_annotations``.
- ``alt_rsid`` carries the *loser* rsid (and is empty string ``''`` at
  every locus that didn't collapse — same-rsid match / filled_nocall /
  unique-to-one-side).
- ``merge_provenance.concordance_summary.collapsed_rsid`` equals the
  count of rows where ``alt_rsid != ''`` (Plan §10.4(c) additive marker
  invariant — locked end-to-end here on top of the unit-level coverage
  in ``test_sample_merge.py::TestVepBundleTiebreaker``).

The merge orchestration is reused verbatim from Step 65 (helpers mirror
the established merge-test scaffolding) so this file exercises only the
``alt_rsid`` invariant on top of an already-validated merge pass.
"""

from __future__ import annotations

import json
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
    individuals,
    jobs,
    merge_provenance,
    raw_variants,
    reference_metadata,
    samples,
)
from backend.services.sample_merge import MergeStrategy, merge_samples

# ── Test-scoped registry that the singleton-using staleness service sees ──


@pytest.fixture
def merge_registry(tmp_data_dir: Path):
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
    ref_path = settings.reference_db_path
    ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(ref_engine)
    ref_engine.dispose()

    with patch("backend.db.connection.get_settings", return_value=settings):
        reset_registry()
        registry = get_registry()
        try:
            yield registry
        finally:
            registry.dispose_all()
            reset_registry()


# ── Helpers (mirroring test_sample_merge.py for parity) ──────────────


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
    bundle_version: str = "v2.0.0",
) -> int:
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
    with engine.begin() as conn:
        if variants:
            conn.execute(raw_variants.insert(), variants)
        conn.execute(
            annotation_state.insert().values(
                key="vep_bundle_version",
                value=bundle_version,
                updated_at=now,
            )
        )
    return sample_id


def _seed_installed_vep_bundle(registry: DBRegistry, version: str = "v2.0.0") -> None:
    from backend.db.tables import database_versions

    with registry.reference_engine.begin() as conn:
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


def _noop_annotation_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.tasks.huey_tasks as huey_tasks

    monkeypatch.setattr(huey_tasks, "create_annotation_job", lambda _sid: "noop-job")
    monkeypatch.setattr(huey_tasks, "run_annotation_task", lambda *_args, **_kw: None)


def _seed_vep_bundle_rsids(registry: DBRegistry, rsids: list[str]) -> None:
    """Create the on-disk ``vep_bundle.db`` with a ``vep_annotations`` table.

    The merge service's ``_rsids_in_vep_bundle`` only reads the ``rsid``
    column, so the seeded table is intentionally minimal — Plan §10.2
    step 2's bundle-hit branch only cares about membership, not the
    annotation payload. The file is materialised before the
    ``registry.vep_engine`` property is first accessed so the engine
    opens against a real table rather than the empty-DB default that
    triggers the ``merge_vep_bundle_unreachable`` fallback to S1.
    """
    bundle_path = registry.settings.vep_bundle_db_path
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(f"sqlite:///{bundle_path}")
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE TABLE vep_annotations (rsid TEXT)"))
            if rsids:
                conn.execute(
                    sa.text("INSERT INTO vep_annotations (rsid) VALUES (:rsid)"),
                    [{"rsid": r} for r in rsids],
                )
    finally:
        engine.dispose()


def _read_merge_rows(registry: DBRegistry, sample_id: int) -> list[sa.Row]:
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.db_path).where(samples.c.id == sample_id)
        ).fetchone()
    assert row is not None
    engine = registry.get_sample_engine(registry.settings.data_dir / row.db_path)
    with engine.connect() as conn:
        return list(
            conn.execute(
                sa.select(raw_variants).order_by(raw_variants.c.chrom, raw_variants.c.pos)
            )
        )


def _read_concordance_summary(registry: DBRegistry, sample_id: int) -> dict[str, int]:
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.db_path).where(samples.c.id == sample_id)
        ).fetchone()
    assert row is not None
    engine = registry.get_sample_engine(registry.settings.data_dir / row.db_path)
    with engine.connect() as conn:
        prov = conn.execute(sa.select(merge_provenance)).fetchone()
    assert prov is not None
    return json.loads(prov.concordance_summary)


# ── Fixture: dual-source variants with two collapse loci + four non-collapse ──


def _v(rsid: str, chrom: str, pos: int, genotype: str) -> dict:
    return {"rsid": rsid, "chrom": chrom, "pos": pos, "genotype": genotype}


# Six loci total:
#
#   (1, 100)  COLLAPSE — different rsids, same genotype     → match
#   (1, 200)  COLLAPSE — different rsids, different alleles → discordant
#   (2, 300)  same rsid, same genotype                      → match,         alt_rsid=''
#   (2, 400)  same rsid, one no-call                        → filled_nocall, alt_rsid=''
#   (3, 500)  unique to S1                                  → unique,        alt_rsid=''
#   (3, 600)  unique to S2                                  → unique,        alt_rsid=''
#
# Two collapse rows ⇒ ``concordance_summary.collapsed_rsid`` must equal 2,
# and exactly those two rows in merged ``raw_variants`` carry a non-empty
# ``alt_rsid``.
S1_VARIANTS = [
    _v("rs100_s1", "1", 100, "AG"),
    _v("rs200_s1", "1", 200, "AA"),
    _v("rs300", "2", 300, "CT"),
    _v("rs400", "2", 400, "--"),
    _v("rs500_s1", "3", 500, "GG"),
]

S2_VARIANTS = [
    _v("rs100_s2", "1", 100, "AG"),
    _v("rs200_s2", "1", 200, "GG"),
    _v("rs300", "2", 300, "CT"),
    _v("rs400", "2", 400, "GG"),
    _v("rs600_s2", "3", 600, "AT"),
]

COLLAPSE_COORDS = {("1", 100), ("1", 200)}
NON_COLLAPSE_COORDS = {("2", 300), ("2", 400), ("3", 500), ("3", 600)}


@pytest.fixture
def linked_sources(
    merge_registry: DBRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[DBRegistry, int, int, int]:
    _seed_installed_vep_bundle(merge_registry, "v2.0.0")
    _noop_annotation_enqueue(monkeypatch)
    individual_id = _create_individual(merge_registry, "Jane Doe")
    s1_id = _create_source_sample(
        merge_registry,
        individual_id=individual_id,
        name="jane_23andme.txt",
        file_format="23andme_v5",
        file_hash="hash_s1",
        variants=S1_VARIANTS,
    )
    s2_id = _create_source_sample(
        merge_registry,
        individual_id=individual_id,
        name="jane_ancestrydna.txt",
        file_format="ancestrydna_v2.0",
        file_hash="hash_s2",
        variants=S2_VARIANTS,
    )
    return merge_registry, individual_id, s1_id, s2_id


# ── Tests ────────────────────────────────────────────────────────────


class TestCollapseUnderS1FallbackTiebreaker:
    """Plan §10.2 step 2 fallback branch: no VEP bundle seeded → S1's rsid wins.

    Parameterised over all three Plan §10.3 strategies because the
    rsid-collapse tiebreaker is *independent* of the strategy — the
    strategy decides which genotype call wins at a discordant locus, the
    tiebreaker decides which rsid wins at a discordant-rsid coordinate.
    The two are orthogonal axes of the merge contract, and the per-row
    ``alt_rsid`` column reflects only the rsid-collapse side.
    """

    @pytest.mark.parametrize(
        "strategy",
        [
            MergeStrategy.FLAG_ONLY,
            MergeStrategy.PREFER_23ANDME,
            MergeStrategy.PREFER_ANCESTRYDNA,
        ],
    )
    def test_alt_rsid_invariant_holds_across_all_strategies(
        self,
        linked_sources: tuple[DBRegistry, int, int, int],
        strategy: MergeStrategy,
    ) -> None:
        registry, individual_id, s1_id, s2_id = linked_sources

        new_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=strategy,
            display_name=f"Jane Doe ({strategy.value})",
        )

        rows = _read_merge_rows(registry, new_id)
        by_coord = {(r.chrom, r.pos): r for r in rows}

        # (i) Six distinct loci across the union — every collapse landed
        # as exactly one row (PK is ``(chrom, pos)`` per Plan §10.4 a).
        assert len(rows) == 6
        assert set(by_coord) == COLLAPSE_COORDS | NON_COLLAPSE_COORDS

        # (ii) Collapse rows: rsid matches the §10.2 step 2 tiebreaker
        # (no bundle seeded → S1 wins), alt_rsid carries the loser.
        collapse_100 = by_coord[("1", 100)]
        assert collapse_100.rsid == "rs100_s1"
        assert collapse_100.alt_rsid == "rs100_s2"

        collapse_200 = by_coord[("1", 200)]
        assert collapse_200.rsid == "rs200_s1"
        assert collapse_200.alt_rsid == "rs200_s2"

        # (iii) Non-collapsed loci: alt_rsid is the server-default ``''``
        # regardless of concordance bucket — match, filled_nocall, unique.
        for coord in NON_COLLAPSE_COORDS:
            row = by_coord[coord]
            assert row.alt_rsid == "", (
                f"non-collapse locus {coord} carries unexpected alt_rsid "
                f"{row.alt_rsid!r} (rsid={row.rsid!r}, concordance={row.concordance!r})"
            )

        # (iv) ``merge_provenance.concordance_summary.collapsed_rsid``
        # equals the count of rows where ``alt_rsid != ''`` — Plan §10.4(c)
        # additive-marker invariant.
        summary = _read_concordance_summary(registry, new_id)
        non_empty_alt_rsid = sum(1 for r in rows if r.alt_rsid)
        assert summary["collapsed_rsid"] == 2
        assert summary["collapsed_rsid"] == non_empty_alt_rsid

        # Paired invariant from Plan §10.4(c): the primary partition sums
        # to total merged loci regardless of how many collapses fired.
        primary_total = (
            summary["match"]
            + summary["filled_nocall"]
            + summary["discordant"]
            + summary["unique_S1"]
            + summary["unique_S2"]
        )
        assert primary_total == len(rows) == 6


class TestCollapseUnderBundleHitTiebreaker:
    """Plan §10.2 step 2 primary branch: bundle membership flips the winner.

    Seeds a real ``vep_bundle.db`` at ``settings.vep_bundle_db_path``
    containing only S2's rsids at the two collapse coordinates so the
    merge service's ``_rsids_in_vep_bundle`` probe resolves to S2 on
    both. This locks the integration-level wiring between the bundle
    read and the rsid choice end-to-end on top of the unit-level
    coverage in ``test_sample_merge.py::TestVepBundleTiebreaker``.
    """

    def test_bundle_hits_on_s2_rsid_make_s2_win(
        self,
        linked_sources: tuple[DBRegistry, int, int, int],
    ) -> None:
        registry, individual_id, s1_id, s2_id = linked_sources

        _seed_vep_bundle_rsids(registry, ["rs100_s2", "rs200_s2"])

        new_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="Jane Doe (bundle hit)",
        )

        rows = _read_merge_rows(registry, new_id)
        by_coord = {(r.chrom, r.pos): r for r in rows}

        # S2's rsid won the tiebreaker at both collapse coords because it
        # is the side present in ``vep_annotations``; S1's rsid lands in
        # ``alt_rsid``.
        collapse_100 = by_coord[("1", 100)]
        assert collapse_100.rsid == "rs100_s2"
        assert collapse_100.alt_rsid == "rs100_s1"

        collapse_200 = by_coord[("1", 200)]
        assert collapse_200.rsid == "rs200_s2"
        assert collapse_200.alt_rsid == "rs200_s1"

        # Collapse count still equals the number of rows with non-empty
        # alt_rsid — the marker tracks the tiebreaker firing, not which
        # side won.
        summary = _read_concordance_summary(registry, new_id)
        non_empty_alt_rsid = sum(1 for r in rows if r.alt_rsid)
        assert summary["collapsed_rsid"] == 2 == non_empty_alt_rsid
