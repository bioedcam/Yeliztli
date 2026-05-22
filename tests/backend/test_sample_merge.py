"""Tests for the sample-merge service core (Step 65 / MRG-02; Plan §10.2, §10.5).

These tests exercise :func:`backend.services.sample_merge.merge_samples`
end-to-end against a real on-disk ``DBRegistry`` (the ``merge_registry``
fixture defined below), driving the four §10.2
concordance buckets, the three §10.3 strategies, the deterministic
``file_hash`` recipe from §10.5 step 5, validation failures (membership /
status / staleness), and the §10.4 (a) ``(chrom, pos)`` PK divergence on
the freshly-created merged sample DB.

The Huey enqueue at the tail of :func:`merge_samples` is no-op'd via a
``monkeypatch`` on ``backend.tasks.huey_tasks.create_annotation_job`` so
the test never actually fires the annotation pipeline (covered by
``test_annotation_engine_merged_pk.py`` in Step 78). The merge service's
``except Exception`` around the enqueue would otherwise swallow whatever
error a fully-isolated test environment surfaces; explicit no-op'ing
keeps the seam visible.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from backend.config import Settings
from backend.db.connection import DBRegistry, get_registry, reset_registry
from backend.db.sample_schema import SAMPLE_SCHEMA_VERSION, create_sample_tables
from backend.db.tables import (
    annotation_state,
    individuals,
    jobs,
    merge_provenance,
    raw_variants,
    reference_metadata,
    samples,
)
from backend.services import sample_merge as sample_merge_module
from backend.services.sample_merge import (
    InvalidMergeRequestError,
    MergeStrategy,
    StaleSourceError,
    _compute_file_hash,
    merge_samples,
)

# ── Test-scoped registry that the singleton-using ``staleness`` service sees ──
#
# ``is_sample_stale`` (and through it ``merge_samples``'s stale-source guard)
# calls ``backend.db.connection.get_registry()`` — the module-level singleton.
# The conftest ``db_registry`` fixture only builds an isolated registry; it
# does not redirect the singleton. We mimic the ``staleness_env`` setup from
# ``tests/backend/test_staleness.py``: patch ``get_settings`` so the singleton
# materialises against ``tmp_data_dir``, and yield it directly.


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


# ── Fixture helpers ──────────────────────────────────────────────────


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
    individual_id: int | None,
    name: str,
    file_format: str,
    file_hash: str,
    variants: list[dict],
    bundle_version: str = "v2.0.0",
    annotation_status: str = "complete",
) -> int:
    """Create one source sample (reference row + per-sample DB + jobs row).

    ``bundle_version`` lands in the new sample's ``annotation_state`` so the
    Step-11 staleness service treats it as fresh by default. Tests covering
    the stale-source branch pass ``bundle_version="v1.0.0"`` to drop it
    below the installed major.
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
        conn.execute(
            samples.update()
            .where(samples.c.id == sample_id)
            .values(db_path=db_path)
        )
        conn.execute(
            jobs.insert().values(
                job_id=f"job-{sample_id}",
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
        conn.execute(
            annotation_state.insert().values(
                key="vep_bundle_version",
                value=bundle_version,
                updated_at=now,
            )
        )
    return sample_id


def _seed_installed_vep_bundle(registry: DBRegistry, version: str = "v2.0.0") -> None:
    """Seed ``database_versions['vep_bundle']`` so the staleness service can compare."""
    from backend.db.tables import database_versions

    with registry.reference_engine.begin() as conn:
        conn.execute(
            sa.delete(database_versions).where(
                database_versions.c.db_name == "vep_bundle"
            )
        )
        conn.execute(
            database_versions.insert().values(
                db_name="vep_bundle",
                version=version,
                downloaded_at=datetime.now(UTC),
            )
        )


def _noop_annotation_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the Huey enqueue used at the tail of merge_samples with a no-op."""
    import backend.tasks.huey_tasks as huey_tasks

    monkeypatch.setattr(huey_tasks, "create_annotation_job", lambda _sid: "noop-job")
    monkeypatch.setattr(huey_tasks, "run_annotation_task", lambda *_args, **_kw: None)


def _read_merge_rows(registry: DBRegistry, sample_id: int) -> list[sa.Row]:
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.db_path).where(samples.c.id == sample_id)
        ).fetchone()
    assert row is not None
    sample_db_path = registry.settings.data_dir / row.db_path
    engine = registry.get_sample_engine(sample_db_path)
    with engine.connect() as conn:
        return list(
            conn.execute(
                sa.select(raw_variants).order_by(
                    raw_variants.c.chrom, raw_variants.c.pos
                )
            )
        )


def _read_merge_provenance(registry: DBRegistry, sample_id: int) -> sa.Row:
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.db_path).where(samples.c.id == sample_id)
        ).fetchone()
    assert row is not None
    engine = registry.get_sample_engine(
        registry.settings.data_dir / row.db_path
    )
    with engine.connect() as conn:
        prov = conn.execute(sa.select(merge_provenance)).fetchone()
    assert prov is not None
    return prov


# ── Pre-canned variant batches the buckets are constructed from ──────


def _v(rsid: str, chrom: str, pos: int, genotype: str) -> dict:
    return {"rsid": rsid, "chrom": chrom, "pos": pos, "genotype": genotype}


# Two source samples whose loci exercise every §10.2 bucket.
#
#   coord (1, 100)  same rsid, same call → match (source='both')
#   coord (1, 200)  same rsid, S1 called, S2 no-call → filled_nocall (S1 wins)
#   coord (1, 300)  same rsid, S2 called, S1 no-call → filled_nocall (S2 wins)
#   coord (1, 400)  same rsid, different alleles → discordant (strategy decides)
#   coord (2, 500)  rsid only in S1 → unique (S1)
#   coord (2, 600)  rsid only in S2 → unique (S2)
#   coord (3, 700)  different rsids, both callers agree on genotype → match + collapsed_rsid

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


# ── Test classes ─────────────────────────────────────────────────────


class TestHappyPath:
    """Plan §10.2 / §10.5: end-to-end against the dual-fixture buckets."""

    @pytest.fixture
    def merged_setup(
        self,
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

    def test_flag_only_strategy_writes_sentinel_at_discordant(
        self, merged_setup: tuple[DBRegistry, int, int, int]
    ) -> None:
        registry, individual_id, s1_id, s2_id = merged_setup
        new_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="Jane Doe (merged)",
        )
        assert new_id not in (s1_id, s2_id)

        rows = _read_merge_rows(registry, new_id)
        by_coord = {(r.chrom, r.pos): r for r in rows}

        # Seven distinct loci across the union.
        assert len(rows) == 7

        # Match: identical call at (1, 100).
        match = by_coord[("1", 100)]
        assert match.source == "both"
        assert match.concordance == "match"
        assert match.genotype == "AG"
        assert match.discordant_alt_genotype == ""
        assert match.alt_rsid == ""

        # filled_nocall (S1 wins) at (1, 200).
        fn_s1 = by_coord[("1", 200)]
        assert fn_s1.source == "S1"
        assert fn_s1.concordance == "filled_nocall"
        assert fn_s1.genotype == "CT"
        assert fn_s1.discordant_alt_genotype == ""

        # filled_nocall (S2 wins) at (1, 300).
        fn_s2 = by_coord[("1", 300)]
        assert fn_s2.source == "S2"
        assert fn_s2.concordance == "filled_nocall"
        assert fn_s2.genotype == "GG"

        # Discordant at (1, 400) — flag_only emits ??.
        disc = by_coord[("1", 400)]
        assert disc.source == "both"
        assert disc.concordance == "discordant"
        assert disc.genotype == "??"
        assert disc.discordant_alt_genotype == "S1=AA;S2=GG"

        # Unique to S1 at (2, 500).
        unique_s1 = by_coord[("2", 500)]
        assert unique_s1.source == "S1"
        assert unique_s1.concordance == "unique"
        assert unique_s1.genotype == "GG"
        assert unique_s1.rsid == "rs500"

        # Unique to S2 at (2, 600).
        unique_s2 = by_coord[("2", 600)]
        assert unique_s2.source == "S2"
        assert unique_s2.concordance == "unique"
        assert unique_s2.genotype == "AT"
        assert unique_s2.rsid == "rs600"

        # Different-rsid-same-coordinate at (3, 700) → collapsed; S1's rsid
        # wins by §10.2 step 2 tiebreaker (bundle not seeded, so neither hit).
        collapsed = by_coord[("3", 700)]
        assert collapsed.source == "both"
        assert collapsed.concordance == "match"
        assert collapsed.genotype == "CT"
        assert collapsed.rsid == "rs700_s1"
        assert collapsed.alt_rsid == "rs700_s2"

    def test_concordance_summary_counts_match_buckets(
        self, merged_setup: tuple[DBRegistry, int, int, int]
    ) -> None:
        registry, individual_id, s1_id, s2_id = merged_setup
        new_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="Jane Doe (merged)",
        )
        prov = _read_merge_provenance(registry, new_id)
        summary = json.loads(prov.concordance_summary)
        # match (1,100) + match-via-collapse (3,700) = 2
        assert summary == {
            "match": 2,
            "filled_nocall": 2,
            "discordant": 1,
            "unique_S1": 1,
            "unique_S2": 1,
            "collapsed_rsid": 1,
        }
        # Row-count invariant locked by Plan §10.4 (c): the primary partition
        # sums to total merged loci (collapsed_rsid is an additive marker).
        total = (
            summary["match"]
            + summary["filled_nocall"]
            + summary["discordant"]
            + summary["unique_S1"]
            + summary["unique_S2"]
        )
        rows = _read_merge_rows(registry, new_id)
        assert total == len(rows) == 7

    def test_prefer_23andme_keeps_s1_call_at_discordant(
        self, merged_setup: tuple[DBRegistry, int, int, int]
    ) -> None:
        registry, individual_id, s1_id, s2_id = merged_setup
        new_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.PREFER_23ANDME,
            display_name="Jane Doe (prefer 23andMe)",
        )
        rows = {(r.chrom, r.pos): r for r in _read_merge_rows(registry, new_id)}
        disc = rows[("1", 400)]
        assert disc.concordance == "discordant"
        assert disc.genotype == "AA"  # S1 (23andMe) wins
        assert disc.discordant_alt_genotype == "S2=GG"

    def test_prefer_ancestrydna_keeps_s2_call_at_discordant(
        self, merged_setup: tuple[DBRegistry, int, int, int]
    ) -> None:
        registry, individual_id, s1_id, s2_id = merged_setup
        new_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.PREFER_ANCESTRYDNA,
            display_name="Jane Doe (prefer AncestryDNA)",
        )
        rows = {(r.chrom, r.pos): r for r in _read_merge_rows(registry, new_id)}
        disc = rows[("1", 400)]
        assert disc.concordance == "discordant"
        assert disc.genotype == "GG"  # S2 (AncestryDNA) wins
        assert disc.discordant_alt_genotype == "S1=AA"

    def test_new_sample_row_carries_individual_and_format(
        self, merged_setup: tuple[DBRegistry, int, int, int]
    ) -> None:
        registry, individual_id, s1_id, s2_id = merged_setup
        new_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="Jane Doe (merged)",
        )
        with registry.reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(samples).where(samples.c.id == new_id)
            ).fetchone()
        assert row is not None
        assert row.individual_id == individual_id
        assert row.file_format == "merged_v1"
        assert row.db_path == f"samples/sample_{new_id}.db"
        assert (registry.settings.data_dir / row.db_path).exists()

    def test_merged_db_uses_chrom_pos_pk(
        self, merged_setup: tuple[DBRegistry, int, int, int]
    ) -> None:
        registry, individual_id, s1_id, s2_id = merged_setup
        new_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="Jane Doe (merged)",
        )
        with registry.reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(samples.c.db_path).where(samples.c.id == new_id)
            ).fetchone()
        assert row is not None
        engine = registry.get_sample_engine(registry.settings.data_dir / row.db_path)
        pk = sa.inspect(engine).get_pk_constraint("raw_variants")
        assert pk["constrained_columns"] == ["chrom", "pos"]

    def test_merge_provenance_payload_shape(
        self, merged_setup: tuple[DBRegistry, int, int, int]
    ) -> None:
        registry, individual_id, s1_id, s2_id = merged_setup
        new_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="Jane Doe (merged)",
        )
        prov = _read_merge_provenance(registry, new_id)
        assert prov.id == 1
        assert prov.strategy == "flag_only"
        assert json.loads(prov.source_sample_ids) == [s1_id, s2_id]
        assert json.loads(prov.source_file_hashes) == ["hash_s1", "hash_s2"]


class TestFileHashRecipe:
    """Plan §10.5 step 5: ``SHA-256(S1 ‖ S2 ‖ strategy ‖ SAMPLE_SCHEMA_VERSION)``."""

    def test_compute_file_hash_is_deterministic(self) -> None:
        first = _compute_file_hash("a", "b", MergeStrategy.FLAG_ONLY)
        second = _compute_file_hash("a", "b", MergeStrategy.FLAG_ONLY)
        assert first == second

    def test_compute_file_hash_is_order_sensitive(self) -> None:
        forward = _compute_file_hash("a", "b", MergeStrategy.FLAG_ONLY)
        reverse = _compute_file_hash("b", "a", MergeStrategy.FLAG_ONLY)
        assert forward != reverse

    def test_compute_file_hash_diverges_per_strategy(self) -> None:
        flag = _compute_file_hash("a", "b", MergeStrategy.FLAG_ONLY)
        prefer_a = _compute_file_hash("a", "b", MergeStrategy.PREFER_23ANDME)
        prefer_b = _compute_file_hash("a", "b", MergeStrategy.PREFER_ANCESTRYDNA)
        assert len({flag, prefer_a, prefer_b}) == 3

    def test_compute_file_hash_includes_schema_version(self) -> None:
        # Recreates the SHA-256 payload contract so a future bump trips the
        # test before it silently produces colliding hashes across versions.
        expected = hashlib.sha256(
            f"a|b|flag_only|{SAMPLE_SCHEMA_VERSION}".encode()
        ).hexdigest()
        assert _compute_file_hash("a", "b", MergeStrategy.FLAG_ONLY) == expected


class TestValidation:
    """Plan §10.5 step 1: membership / status / shape validation surface."""

    def _setup(
        self, merge_registry: DBRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[DBRegistry, int]:
        _seed_installed_vep_bundle(merge_registry, "v2.0.0")
        _noop_annotation_enqueue(monkeypatch)
        individual_id = _create_individual(merge_registry, "Owner")
        return merge_registry, individual_id

    def test_wrong_source_count_raises(
        self, merge_registry: DBRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry, individual_id = self._setup(merge_registry, monkeypatch)
        with pytest.raises(InvalidMergeRequestError, match="exactly 2"):
            merge_samples(
                registry,
                source_sample_ids=[1],
                individual_id=individual_id,
                strategy=MergeStrategy.FLAG_ONLY,
                display_name="x",
            )

    def test_duplicate_source_ids_raises(
        self, merge_registry: DBRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry, individual_id = self._setup(merge_registry, monkeypatch)
        with pytest.raises(InvalidMergeRequestError, match="distinct"):
            merge_samples(
                registry,
                source_sample_ids=[7, 7],
                individual_id=individual_id,
                strategy=MergeStrategy.FLAG_ONLY,
                display_name="x",
            )

    def test_missing_display_name_raises(
        self, merge_registry: DBRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry, individual_id = self._setup(merge_registry, monkeypatch)
        with pytest.raises(InvalidMergeRequestError, match="display_name"):
            merge_samples(
                registry,
                source_sample_ids=[1, 2],
                individual_id=individual_id,
                strategy=MergeStrategy.FLAG_ONLY,
                display_name="   ",
            )

    def test_sample_not_found_raises(
        self, merge_registry: DBRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry, individual_id = self._setup(merge_registry, monkeypatch)
        s1_id = _create_source_sample(
            registry,
            individual_id=individual_id,
            name="a.txt",
            file_format="23andme_v5",
            file_hash="h1",
            variants=S1_VARIANTS,
        )
        with pytest.raises(InvalidMergeRequestError, match="not found"):
            merge_samples(
                registry,
                source_sample_ids=[s1_id, 9999],
                individual_id=individual_id,
                strategy=MergeStrategy.FLAG_ONLY,
                display_name="x",
            )

    def test_sample_linked_elsewhere_raises(
        self, merge_registry: DBRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry, individual_id = self._setup(merge_registry, monkeypatch)
        other_id = _create_individual(registry, "Other")
        s1_id = _create_source_sample(
            registry,
            individual_id=individual_id,
            name="a.txt",
            file_format="23andme_v5",
            file_hash="h1",
            variants=S1_VARIANTS,
        )
        s2_id = _create_source_sample(
            registry,
            individual_id=other_id,
            name="b.txt",
            file_format="ancestrydna_v2.0",
            file_hash="h2",
            variants=S2_VARIANTS,
        )
        with pytest.raises(InvalidMergeRequestError, match="not linked to individual"):
            merge_samples(
                registry,
                source_sample_ids=[s1_id, s2_id],
                individual_id=individual_id,
                strategy=MergeStrategy.FLAG_ONLY,
                display_name="x",
            )

    def test_source_annotation_not_complete_raises(
        self, merge_registry: DBRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry, individual_id = self._setup(merge_registry, monkeypatch)
        s1_id = _create_source_sample(
            registry,
            individual_id=individual_id,
            name="a.txt",
            file_format="23andme_v5",
            file_hash="h1",
            variants=S1_VARIANTS,
        )
        s2_id = _create_source_sample(
            registry,
            individual_id=individual_id,
            name="b.txt",
            file_format="ancestrydna_v2.0",
            file_hash="h2",
            variants=S2_VARIANTS,
            annotation_status="running",
        )
        with pytest.raises(InvalidMergeRequestError, match="not complete"):
            merge_samples(
                registry,
                source_sample_ids=[s1_id, s2_id],
                individual_id=individual_id,
                strategy=MergeStrategy.FLAG_ONLY,
                display_name="x",
            )


class TestStaleSource:
    """Plan §10.5 step 1 → §7.4: stale source → HTTP 423 payload."""

    def test_stale_source_raises_with_detail(
        self, merge_registry: DBRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_installed_vep_bundle(merge_registry, "v2.0.0")
        _noop_annotation_enqueue(monkeypatch)
        individual_id = _create_individual(merge_registry, "Stale Owner")
        s1_id = _create_source_sample(
            merge_registry,
            individual_id=individual_id,
            name="a.txt",
            file_format="23andme_v5",
            file_hash="h1",
            variants=S1_VARIANTS,
        )
        s2_id = _create_source_sample(
            merge_registry,
            individual_id=individual_id,
            name="b.txt",
            file_format="ancestrydna_v2.0",
            file_hash="h2",
            variants=S2_VARIANTS,
            bundle_version="v1.0.0",
        )
        with pytest.raises(StaleSourceError) as exc_info:
            merge_samples(
                merge_registry,
                source_sample_ids=[s1_id, s2_id],
                individual_id=individual_id,
                strategy=MergeStrategy.FLAG_ONLY,
                display_name="x",
            )
        err = exc_info.value
        assert err.stale_sample_ids == [s2_id]
        assert err.detail["error"] == "stale_source_sample"
        assert err.detail["stale_sample_ids"] == [s2_id]
        assert "Re-annotate" in err.detail["message"]


class TestNoProductionSideEffects:
    """A failed validation never creates a samples row or a per-sample DB."""

    def test_invalid_request_creates_no_artefacts(
        self, merge_registry: DBRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_installed_vep_bundle(merge_registry, "v2.0.0")
        _noop_annotation_enqueue(monkeypatch)
        individual_id = _create_individual(merge_registry, "Owner")
        with pytest.raises(InvalidMergeRequestError):
            merge_samples(
                merge_registry,
                source_sample_ids=[1, 2],
                individual_id=individual_id,
                strategy=MergeStrategy.FLAG_ONLY,
                display_name="x",
            )

        # No samples row, no sample_*.db on disk.
        with merge_registry.reference_engine.connect() as conn:
            count = conn.execute(sa.select(sa.func.count()).select_from(samples)).scalar()
        assert count == 0
        samples_dir = merge_registry.settings.data_dir / "samples"
        assert not any(samples_dir.glob("sample_*.db"))


class TestVepBundleTiebreaker:
    """Plan §10.2 step 2: bundle-membership tiebreaker at rsid collapses.

    Exercises ``_apply_semantics`` directly so the test doesn't need a real
    ~600 MB VEP bundle on disk — the merge service's ``_rsids_in_vep_bundle``
    is replaced inline via the ``rsids_in_bundle`` parameter that the helper
    consumes.
    """

    def test_bundle_hit_wins_against_non_hit(self) -> None:
        from backend.services.sample_merge import _apply_semantics

        s1 = {("1", 100): {"rsid": "rs_in_bundle", "genotype": "AG"}}
        s2 = {("1", 100): {"rsid": "rs_missing", "genotype": "AG"}}
        rows, summary = _apply_semantics(
            s1,
            s2,
            strategy=MergeStrategy.FLAG_ONLY,
            rsids_in_bundle={"rs_in_bundle"},
            s1_vendor="23andme",
            s2_vendor="ancestrydna",
        )
        assert summary.collapsed_rsid == 1
        assert rows[0].rsid == "rs_in_bundle"
        assert rows[0].alt_rsid == "rs_missing"

    def test_s2_hit_wins_over_s1(self) -> None:
        from backend.services.sample_merge import _apply_semantics

        s1 = {("1", 100): {"rsid": "rs_s1_only", "genotype": "AG"}}
        s2 = {("1", 100): {"rsid": "rs_bundle", "genotype": "AG"}}
        rows, _ = _apply_semantics(
            s1,
            s2,
            strategy=MergeStrategy.FLAG_ONLY,
            rsids_in_bundle={"rs_bundle"},
            s1_vendor="23andme",
            s2_vendor="ancestrydna",
        )
        assert rows[0].rsid == "rs_bundle"
        assert rows[0].alt_rsid == "rs_s1_only"

    def test_neither_in_bundle_falls_back_to_s1(self) -> None:
        from backend.services.sample_merge import _apply_semantics

        s1 = {("1", 100): {"rsid": "rs_a", "genotype": "AG"}}
        s2 = {("1", 100): {"rsid": "rs_b", "genotype": "AG"}}
        rows, _ = _apply_semantics(
            s1,
            s2,
            strategy=MergeStrategy.FLAG_ONLY,
            rsids_in_bundle=set(),
            s1_vendor="23andme",
            s2_vendor="ancestrydna",
        )
        assert rows[0].rsid == "rs_a"
        assert rows[0].alt_rsid == "rs_b"


class TestAnnotationEnqueue:
    """Plan §10.5 step 8: merge_samples enqueues the standard annotation job."""

    def test_enqueue_is_invoked_with_new_sample_id(
        self, merge_registry: DBRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_installed_vep_bundle(merge_registry, "v2.0.0")
        calls: dict[str, list[int]] = {"create": [], "run": []}
        import backend.tasks.huey_tasks as huey_tasks

        def fake_create(sample_id: int) -> str:
            calls["create"].append(sample_id)
            return f"job-{sample_id}"

        def fake_run(sample_id: int, job_id: str) -> None:  # noqa: ARG001
            calls["run"].append(sample_id)

        monkeypatch.setattr(huey_tasks, "create_annotation_job", fake_create)
        monkeypatch.setattr(huey_tasks, "run_annotation_task", fake_run)

        individual_id = _create_individual(merge_registry, "Owner")
        s1_id = _create_source_sample(
            merge_registry,
            individual_id=individual_id,
            name="a.txt",
            file_format="23andme_v5",
            file_hash="h1",
            variants=S1_VARIANTS,
        )
        s2_id = _create_source_sample(
            merge_registry,
            individual_id=individual_id,
            name="b.txt",
            file_format="ancestrydna_v2.0",
            file_hash="h2",
            variants=S2_VARIANTS,
        )
        new_id = merge_samples(
            merge_registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="Jane Doe (merged)",
        )
        assert calls["create"] == [new_id]
        assert calls["run"] == [new_id]

    def test_enqueue_failure_is_swallowed(
        self,
        merge_registry: DBRegistry,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A failure to enqueue annotation must not roll back the merge."""
        _seed_installed_vep_bundle(merge_registry, "v2.0.0")
        import backend.tasks.huey_tasks as huey_tasks

        def raise_value_error(_sid: int) -> str:
            raise ValueError("annotation already running")

        monkeypatch.setattr(huey_tasks, "create_annotation_job", raise_value_error)
        monkeypatch.setattr(huey_tasks, "run_annotation_task", lambda *_a, **_k: None)

        individual_id = _create_individual(merge_registry, "Owner")
        s1_id = _create_source_sample(
            merge_registry,
            individual_id=individual_id,
            name="a.txt",
            file_format="23andme_v5",
            file_hash="h1",
            variants=S1_VARIANTS,
        )
        s2_id = _create_source_sample(
            merge_registry,
            individual_id=individual_id,
            name="b.txt",
            file_format="ancestrydna_v2.0",
            file_hash="h2",
            variants=S2_VARIANTS,
        )
        with caplog.at_level("WARNING", logger=sample_merge_module.__name__):
            new_id = merge_samples(
                merge_registry,
                source_sample_ids=[s1_id, s2_id],
                individual_id=individual_id,
                strategy=MergeStrategy.FLAG_ONLY,
                display_name="Jane Doe (merged)",
            )

        # The merged sample still exists on disk.
        with merge_registry.reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(samples.c.db_path).where(samples.c.id == new_id)
            ).fetchone()
        assert row is not None
        assert (merge_registry.settings.data_dir / row.db_path).exists()
        assert any(
            "merge_annotation_enqueue_failed" in record.message
            for record in caplog.records
        )


class TestEmptySourcesEdgeCase:
    """Defensive shape: zero overlapping coordinates → empty merge with provenance."""

    def test_empty_sources_create_merged_sample_with_zero_rows(
        self, merge_registry: DBRegistry, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _seed_installed_vep_bundle(merge_registry, "v2.0.0")
        _noop_annotation_enqueue(monkeypatch)
        individual_id = _create_individual(merge_registry, "Empty Owner")
        s1_id = _create_source_sample(
            merge_registry,
            individual_id=individual_id,
            name="empty1.txt",
            file_format="23andme_v5",
            file_hash="he1",
            variants=[],
        )
        s2_id = _create_source_sample(
            merge_registry,
            individual_id=individual_id,
            name="empty2.txt",
            file_format="ancestrydna_v2.0",
            file_hash="he2",
            variants=[],
        )
        new_id = merge_samples(
            merge_registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="Empty Merge",
        )
        assert _read_merge_rows(merge_registry, new_id) == []
        prov = _read_merge_provenance(merge_registry, new_id)
        summary = json.loads(prov.concordance_summary)
        assert summary == {
            "match": 0,
            "filled_nocall": 0,
            "discordant": 0,
            "unique_S1": 0,
            "unique_S2": 0,
            "collapsed_rsid": 0,
        }
