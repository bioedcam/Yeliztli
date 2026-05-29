"""Step 79 / MRG-08d — tag & watch non-propagation across a sample merge.

Plan §10.4(a) locks four rsid-bearing per-sample tables (`annotated_variants`,
`variant_tags`, `panel_coverage`, `watched_variants`) and §10.4(a) commentary
spells out: "A user who tagged or watched the discarded rsid in a source
sample will not see those tags/watches transferred to the merged sample —
merging produces an independent sample DB; source samples (with their
tags/watches intact) remain."

The Step-65 merge service writes only `raw_variants` + `merge_provenance`
into the freshly created merged sample DB. ``variant_tags`` and
``watched_variants`` are *never* copied across — the post-merge re-watch
modal (MRG-13 / Step 72) surfaces the gap to the user.

These tests lock the contract for two cases:

1. **Private-rsid case.** A rsid present only on one source sample (the
   "AncestryDNA companion does not carry" wording from MRG-08d) ends up on
   the merged sample as ``source='S1' | 'S2'`` / ``concordance='unique'``,
   but its `variant_tags` / `watched_variants` rows do NOT migrate.

2. **rsid-collapse case.** Two different rsids at the same `(chrom, pos)`
   collapse to one chosen rsid in the merged sample, with the discarded
   rsid recorded in `alt_rsid`. Tag/watch rows on either side of the
   collapse — including the *losing* rsid — do NOT migrate.

The merge orchestration is reused verbatim from
``tests/backend/test_sample_merge.py`` (registry fixture, source-sample
factory, Huey no-op patches, predetermined dual-fixture buckets) so this
file exercises only the no-propagation invariant on top of an
already-validated merge pass.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from backend.config import Settings
from backend.db.connection import DBRegistry, get_registry, reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    PREDEFINED_TAGS,
    annotation_state,
    individuals,
    jobs,
    raw_variants,
    reference_metadata,
    samples,
    tags,
    variant_tags,
    watched_variants,
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


def _sample_engine(registry: DBRegistry, sample_id: int) -> sa.Engine:
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.db_path).where(samples.c.id == sample_id)
        ).fetchone()
    assert row is not None
    return registry.get_sample_engine(registry.settings.data_dir / row.db_path)


def _tag_id_for_name(engine: sa.Engine, tag_name: str) -> int:
    with engine.connect() as conn:
        row = conn.execute(sa.select(tags.c.id).where(tags.c.name == tag_name)).fetchone()
    assert row is not None, f"predefined tag {tag_name!r} not seeded"
    return int(row.id)


def _tag_variant(engine: sa.Engine, *, rsid: str, tag_name: str) -> None:
    """Attach a predefined tag to a single rsid (mirrors POST /api/tags/variant)."""
    tag_id = _tag_id_for_name(engine, tag_name)
    with engine.begin() as conn:
        conn.execute(
            variant_tags.insert().values(
                rsid=rsid,
                tag_id=tag_id,
            )
        )


def _watch_variant(engine: sa.Engine, *, rsid: str, notes: str = "") -> None:
    """Insert a watched_variants row (mirrors POST /api/watches)."""
    with engine.begin() as conn:
        conn.execute(
            watched_variants.insert().values(
                rsid=rsid,
                clinvar_significance_at_watch="uncertain_significance",
                notes=notes,
            )
        )


def _variant_tag_rsids(engine: sa.Engine) -> list[str]:
    with engine.connect() as conn:
        return [
            r.rsid
            for r in conn.execute(sa.select(variant_tags.c.rsid).order_by(variant_tags.c.rsid))
        ]


def _watched_rsids(engine: sa.Engine) -> list[str]:
    with engine.connect() as conn:
        stmt = sa.select(watched_variants.c.rsid).order_by(watched_variants.c.rsid)
        return [r.rsid for r in conn.execute(stmt)]


# ── Fixtures: dual-source variants ───────────────────────────────────


def _v(rsid: str, chrom: str, pos: int, genotype: str) -> dict:
    return {"rsid": rsid, "chrom": chrom, "pos": pos, "genotype": genotype}


# S1 carries rs500 unique to itself (chr 2, pos 500) plus rs700_s1 at the
# (3, 700) rsid-collapse locus shared with S2. S2 carries rs600 unique to
# itself (chr 2, pos 600) plus rs700_s2 at the (3, 700) collapse. The
# (1, 100) row is the trivial concordant locus so the merge has something
# stable to anchor against.
S1_VARIANTS = [
    _v("rs100", "1", 100, "AG"),
    _v("rs500", "2", 500, "GG"),
    _v("rs700_s1", "3", 700, "CT"),
]

S2_VARIANTS = [
    _v("rs100", "1", 100, "AG"),
    _v("rs600", "2", 600, "AT"),
    _v("rs700_s2", "3", 700, "CT"),
]


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


class TestPrivateRsidNonPropagation:
    """Tags/watches on rsids unique to a single source don't migrate."""

    def test_unique_s1_rsid_tag_and_watch_stay_on_source(
        self, linked_sources: tuple[DBRegistry, int, int, int]
    ) -> None:
        registry, individual_id, s1_id, s2_id = linked_sources

        s1_engine = _sample_engine(registry, s1_id)
        _tag_variant(s1_engine, rsid="rs500", tag_name="Review later")
        _watch_variant(s1_engine, rsid="rs500", notes="watching unique S1 locus")

        # Pre-merge sanity: tag/watch live on S1's DB.
        assert _variant_tag_rsids(s1_engine) == ["rs500"]
        assert _watched_rsids(s1_engine) == ["rs500"]

        new_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="Jane Doe (merged)",
        )

        merged_engine = _sample_engine(registry, new_id)

        # The merged sample carries the rs500 row in raw_variants (unique to
        # S1) but NOT the variant_tags / watched_variants rows. Tags/watches
        # are independent rsid-PK tables (Plan §10.4(a) invariant).
        with merged_engine.connect() as conn:
            merged_rsids = [
                r.rsid
                for r in conn.execute(
                    sa.select(raw_variants.c.rsid).where(raw_variants.c.rsid == "rs500")
                )
            ]
        assert merged_rsids == ["rs500"]
        assert _variant_tag_rsids(merged_engine) == []
        assert _watched_rsids(merged_engine) == []

        # And the source's tags/watches remain intact post-merge.
        assert _variant_tag_rsids(s1_engine) == ["rs500"]
        assert _watched_rsids(s1_engine) == ["rs500"]

    def test_unique_s2_rsid_tag_and_watch_stay_on_source(
        self, linked_sources: tuple[DBRegistry, int, int, int]
    ) -> None:
        registry, individual_id, s1_id, s2_id = linked_sources

        s2_engine = _sample_engine(registry, s2_id)
        _tag_variant(s2_engine, rsid="rs600", tag_name="Actionable")
        _watch_variant(s2_engine, rsid="rs600")

        new_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="Jane Doe (merged)",
        )

        merged_engine = _sample_engine(registry, new_id)
        assert _variant_tag_rsids(merged_engine) == []
        assert _watched_rsids(merged_engine) == []
        assert _variant_tag_rsids(s2_engine) == ["rs600"]
        assert _watched_rsids(s2_engine) == ["rs600"]

    def test_merged_predefined_tags_seeded_but_no_variant_links(
        self, linked_sources: tuple[DBRegistry, int, int, int]
    ) -> None:
        """Predefined `tags` rows seed on every fresh sample DB (including
        merged samples) but `variant_tags` joins start empty."""
        registry, individual_id, s1_id, s2_id = linked_sources
        s1_engine = _sample_engine(registry, s1_id)
        _tag_variant(s1_engine, rsid="rs500", tag_name="Review later")

        new_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="Jane Doe (merged)",
        )

        merged_engine = _sample_engine(registry, new_id)
        with merged_engine.connect() as conn:
            merged_tag_names = {
                r.name
                for r in conn.execute(sa.select(tags.c.name).where(tags.c.is_predefined == True))  # noqa: E712
            }
        assert merged_tag_names == set(PREDEFINED_TAGS)
        # But no variant ↔ tag rows.
        assert _variant_tag_rsids(merged_engine) == []


class TestRsidCollapseNonPropagation:
    """Plan §10.4(a): even when the (chrom, pos) locus appears on the merged
    sample, tags/watches keyed on the *discarded* rsid do not migrate. The
    chosen rsid is also not silently retagged."""

    def test_loser_side_tag_watch_does_not_migrate(
        self, linked_sources: tuple[DBRegistry, int, int, int]
    ) -> None:
        registry, individual_id, s1_id, s2_id = linked_sources

        # At (3, 700), S1 carries rs700_s1 and S2 carries rs700_s2. With no
        # VEP bundle seeded the §10.2 tiebreaker falls back to S1, so the
        # merged sample's chosen rsid is rs700_s1 and rs700_s2 lands in
        # `alt_rsid` — making rs700_s2 the discarded "loser" rsid.
        s2_engine = _sample_engine(registry, s2_id)
        _tag_variant(s2_engine, rsid="rs700_s2", tag_name="Discuss with clinician")
        _watch_variant(
            s2_engine,
            rsid="rs700_s2",
            notes="user tagged the AncestryDNA rsid",
        )

        new_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="Jane Doe (merged)",
        )

        merged_engine = _sample_engine(registry, new_id)

        # Confirm the collapse happened the way the test assumes: merged
        # sample has rs700_s1 at (3, 700) with rs700_s2 in alt_rsid.
        with merged_engine.connect() as conn:
            collapsed = conn.execute(
                sa.select(
                    raw_variants.c.rsid,
                    raw_variants.c.alt_rsid,
                ).where((raw_variants.c.chrom == "3") & (raw_variants.c.pos == 700))
            ).fetchone()
        assert collapsed is not None
        assert collapsed.rsid == "rs700_s1"
        assert collapsed.alt_rsid == "rs700_s2"

        # Tag/watch on the discarded rsid does NOT migrate.
        assert _variant_tag_rsids(merged_engine) == []
        assert _watched_rsids(merged_engine) == []

        # Source's own tag/watch rows survive intact on S2.
        assert _variant_tag_rsids(s2_engine) == ["rs700_s2"]
        assert _watched_rsids(s2_engine) == ["rs700_s2"]

    def test_winner_side_tag_watch_also_does_not_migrate(
        self, linked_sources: tuple[DBRegistry, int, int, int]
    ) -> None:
        """The chosen rsid does appear on the merged sample's raw_variants,
        but its source-side variant_tags / watched_variants rows still don't
        migrate — the four rsid-PK tables remain independent across the
        merge per Plan §10.4(a)."""
        registry, individual_id, s1_id, s2_id = linked_sources

        s1_engine = _sample_engine(registry, s1_id)
        _tag_variant(s1_engine, rsid="rs700_s1", tag_name="Actionable")
        _watch_variant(s1_engine, rsid="rs700_s1")

        new_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="Jane Doe (merged)",
        )

        merged_engine = _sample_engine(registry, new_id)

        # rs700_s1 IS present in merged raw_variants (it won the collapse).
        with merged_engine.connect() as conn:
            present = conn.execute(
                sa.select(raw_variants.c.rsid).where(raw_variants.c.rsid == "rs700_s1")
            ).fetchone()
        assert present is not None

        # But variant_tags / watched_variants in the merged DB are empty.
        assert _variant_tag_rsids(merged_engine) == []
        assert _watched_rsids(merged_engine) == []
