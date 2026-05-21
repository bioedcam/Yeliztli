"""Tests for the individuals API endpoints (Step 47 / IND-03; Plan §9.2, §9.3).

Covers:
- (a) Happy paths for all seven endpoints:
       list, create, detail, patch, delete, link-sample, unlink-sample.
- (b) 409 on double-link — linking a sample already attached to a
       different individual returns the existing link in the body.
- (c) FK SET NULL on individual delete — DELETE on an individual with
       linked samples nulls out each ``samples.individual_id`` but leaves
       the sample rows + per-sample DB files in place. Plan §9.2.

IND-09's remaining test surface (link-elsewhere, nonexistent, aggregated
dedup) lands in step 55; sex-inference + haplogroup parity in step 54;
migration 009 round-trip in step 46.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import annotated_variants, reference_metadata, samples
from backend.db.tables import findings as findings_table

# ── Fixtures ─────────────────────────────────────────────────────────


def _seed_sample(
    ref_engine: sa.Engine,
    data_dir: Path,
    *,
    name: str,
    db_path: str,
    file_format: str,
    file_hash: str,
) -> int:
    """Insert a samples row + create the per-sample DB file. Returns id."""
    with ref_engine.begin() as conn:
        result = conn.execute(
            samples.insert().values(
                name=name,
                db_path=db_path,
                file_format=file_format,
                file_hash=file_hash,
            )
        )
        sample_id = result.inserted_primary_key[0]

    sample_db_path = data_dir / db_path
    sample_db_path.parent.mkdir(parents=True, exist_ok=True)
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)
    sample_engine.dispose()
    return sample_id


def _seed_high_confidence_findings(
    data_dir: Path,
    db_path: str,
    rows: list[dict],
) -> None:
    sample_engine = sa.create_engine(f"sqlite:///{data_dir / db_path}")
    with sample_engine.begin() as conn:
        conn.execute(annotated_variants.insert(), rows)
        conn.execute(
            findings_table.insert(),
            [
                {
                    "module": "cancer",
                    "evidence_level": row.get("evidence_level", 4),
                    "rsid": row["rsid"],
                    "finding_text": f"{row['rsid']} finding",
                }
                for row in rows
            ],
        )
    sample_engine.dispose()


@pytest.fixture
def individuals_client(tmp_data_dir: Path) -> TestClient:
    """FastAPI TestClient with the reference DB initialized + two samples seeded."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_path = settings.reference_db_path
    ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(ref_engine)

    sample1_id = _seed_sample(
        ref_engine,
        tmp_data_dir,
        name="23andMe Export",
        db_path="samples/sample_1.db",
        file_format="23andme_v5",
        file_hash="hash_23andme",
    )
    sample2_id = _seed_sample(
        ref_engine,
        tmp_data_dir,
        name="AncestryDNA Export",
        db_path="samples/sample_2.db",
        file_format="ancestrydna_v2.0",
        file_hash="hash_ancestry",
    )

    # Seed each sample DB with a couple of high-confidence findings;
    # rs12345 is shared so the aggregate count dedupes to 3 rsids.
    _seed_high_confidence_findings(
        tmp_data_dir,
        "samples/sample_1.db",
        [
            {"rsid": "rs12345", "chrom": "1", "pos": 100, "evidence_level": 4},
            {"rsid": "rs67890", "chrom": "2", "pos": 200, "evidence_level": 3},
        ],
    )
    _seed_high_confidence_findings(
        tmp_data_dir,
        "samples/sample_2.db",
        [
            {"rsid": "rs12345", "chrom": "1", "pos": 100, "evidence_level": 4},
            {"rsid": "rs99999", "chrom": "3", "pos": 300, "evidence_level": 4},
        ],
    )

    ref_engine.dispose()

    # Stash sample ids on the settings so tests can read them.
    settings.__dict__["_test_sample1_id"] = sample1_id
    settings.__dict__["_test_sample2_id"] = sample2_id

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()

        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            tc.sample1_id = sample1_id  # type: ignore[attr-defined]
            tc.sample2_id = sample2_id  # type: ignore[attr-defined]
            tc.data_dir = tmp_data_dir  # type: ignore[attr-defined]
            yield tc

        reset_registry()


# ── (a) Happy paths ──────────────────────────────────────────────────


class TestHappyPaths:
    def test_create_then_list_then_get(self, individuals_client: TestClient) -> None:
        # Empty list to start.
        resp = individuals_client.get("/api/individuals")
        assert resp.status_code == 200
        assert resp.json() == []

        # Create.
        resp = individuals_client.post(
            "/api/individuals",
            json={
                "display_name": "Subject A",
                "notes": "primary subject",
                "biological_sex": "XX",
            },
        )
        assert resp.status_code == 201
        created = resp.json()
        assert created["display_name"] == "Subject A"
        assert created["notes"] == "primary subject"
        assert created["biological_sex"] == "XX"
        assert created["linked_samples"] == []
        assert created["aggregated_findings_count"] == 0
        ind_id = created["id"]

        # Get detail.
        resp = individuals_client.get(f"/api/individuals/{ind_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == ind_id
        assert body["display_name"] == "Subject A"

        # List now contains the row with summary fields.
        resp = individuals_client.get("/api/individuals")
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["id"] == ind_id
        assert rows[0]["sample_count"] == 0
        assert rows[0]["vendors"] == []
        assert rows[0]["last_activity"] is None

    def test_patch_updates_fields(self, individuals_client: TestClient) -> None:
        ind_id = individuals_client.post(
            "/api/individuals", json={"display_name": "Original"}
        ).json()["id"]

        resp = individuals_client.patch(
            f"/api/individuals/{ind_id}",
            json={"display_name": "Updated", "biological_sex": "XY"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["display_name"] == "Updated"
        assert body["biological_sex"] == "XY"

        # Notes can be set to a non-empty string after creation.
        resp = individuals_client.patch(
            f"/api/individuals/{ind_id}",
            json={"notes": "follow-up next quarter"},
        )
        assert resp.status_code == 200
        assert resp.json()["notes"] == "follow-up next quarter"

    def test_patch_with_no_fields_returns_422(
        self, individuals_client: TestClient
    ) -> None:
        ind_id = individuals_client.post(
            "/api/individuals", json={"display_name": "Original"}
        ).json()["id"]
        resp = individuals_client.patch(f"/api/individuals/{ind_id}", json={})
        assert resp.status_code == 422

    def test_link_and_unlink_sample(self, individuals_client: TestClient) -> None:
        sample1_id = individuals_client.sample1_id  # type: ignore[attr-defined]
        sample2_id = individuals_client.sample2_id  # type: ignore[attr-defined]

        ind_id = individuals_client.post(
            "/api/individuals", json={"display_name": "Subject"}
        ).json()["id"]

        # Link sample 1.
        resp = individuals_client.post(
            f"/api/individuals/{ind_id}/link-sample",
            json={"sample_id": sample1_id},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert [s["id"] for s in body["linked_samples"]] == [sample1_id]
        assert body["linked_samples"][0]["vendor"] == "23andme"

        # Link sample 2 — both now attached, vendors deduped to two entries.
        resp = individuals_client.post(
            f"/api/individuals/{ind_id}/link-sample",
            json={"sample_id": sample2_id},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert sorted(s["id"] for s in body["linked_samples"]) == sorted(
            [sample1_id, sample2_id]
        )
        # 3 distinct high-confidence rsids across the two samples: rs12345
        # (shared), rs67890, rs99999.
        assert body["aggregated_findings_count"] == 3

        # List summary reflects both vendors.
        rows = individuals_client.get("/api/individuals").json()
        target = next(r for r in rows if r["id"] == ind_id)
        assert target["sample_count"] == 2
        assert sorted(target["vendors"]) == ["23andme", "ancestrydna"]
        assert target["last_activity"] is not None

        # Unlink sample 1.
        resp = individuals_client.post(
            f"/api/individuals/{ind_id}/unlink-sample",
            json={"sample_id": sample1_id},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert [s["id"] for s in body["linked_samples"]] == [sample2_id]

        # Re-linking the same sample is a no-op.
        resp = individuals_client.post(
            f"/api/individuals/{ind_id}/link-sample",
            json={"sample_id": sample2_id},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert [s["id"] for s in body["linked_samples"]] == [sample2_id]

    def test_create_minimal_body(self, individuals_client: TestClient) -> None:
        resp = individuals_client.post(
            "/api/individuals", json={"display_name": "Minimal"}
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["notes"] is None
        assert body["biological_sex"] is None

    def test_create_rejects_empty_display_name(
        self, individuals_client: TestClient
    ) -> None:
        resp = individuals_client.post(
            "/api/individuals", json={"display_name": ""}
        )
        assert resp.status_code == 422

    def test_get_nonexistent_returns_404(self, individuals_client: TestClient) -> None:
        resp = individuals_client.get("/api/individuals/999")
        assert resp.status_code == 404

    def test_patch_nonexistent_returns_404(
        self, individuals_client: TestClient
    ) -> None:
        resp = individuals_client.patch(
            "/api/individuals/999", json={"display_name": "Foo"}
        )
        assert resp.status_code == 404

    def test_delete_nonexistent_returns_404(
        self, individuals_client: TestClient
    ) -> None:
        resp = individuals_client.delete("/api/individuals/999")
        assert resp.status_code == 404


# ── (b) 409 on double-link ───────────────────────────────────────────


class TestDoubleLinkConflict:
    def test_409_returns_existing_link_in_body(
        self, individuals_client: TestClient
    ) -> None:
        sample1_id = individuals_client.sample1_id  # type: ignore[attr-defined]

        ind_a = individuals_client.post(
            "/api/individuals", json={"display_name": "Owner"}
        ).json()
        ind_b = individuals_client.post(
            "/api/individuals", json={"display_name": "Other"}
        ).json()

        # Attach to A first.
        resp = individuals_client.post(
            f"/api/individuals/{ind_a['id']}/link-sample",
            json={"sample_id": sample1_id},
        )
        assert resp.status_code == 200

        # Attempt to attach the same sample to B → 409 with payload.
        resp = individuals_client.post(
            f"/api/individuals/{ind_b['id']}/link-sample",
            json={"sample_id": sample1_id},
        )
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["sample_id"] == sample1_id
        assert detail["individual_id"] == ind_a["id"]
        assert detail["individual_display_name"] == "Owner"
        assert str(ind_a["id"]) in detail["message"]

        # The original link must be untouched.
        resp = individuals_client.get(f"/api/individuals/{ind_a['id']}")
        assert [s["id"] for s in resp.json()["linked_samples"]] == [sample1_id]


# ── (c) FK SET NULL on individual delete ─────────────────────────────


class TestDeleteCascadesToSetNull:
    def test_delete_individual_nulls_linked_samples_without_deleting(
        self, individuals_client: TestClient, tmp_data_dir: Path
    ) -> None:
        sample1_id = individuals_client.sample1_id  # type: ignore[attr-defined]
        sample2_id = individuals_client.sample2_id  # type: ignore[attr-defined]

        # Link both samples to one individual.
        ind = individuals_client.post(
            "/api/individuals", json={"display_name": "Subject"}
        ).json()
        for sid in (sample1_id, sample2_id):
            individuals_client.post(
                f"/api/individuals/{ind['id']}/link-sample",
                json={"sample_id": sid},
            )

        # Per-sample DB files must exist before delete.
        sample1_db = tmp_data_dir / "samples/sample_1.db"
        sample2_db = tmp_data_dir / "samples/sample_2.db"
        assert sample1_db.is_file()
        assert sample2_db.is_file()

        # DELETE the individual.
        resp = individuals_client.delete(f"/api/individuals/{ind['id']}")
        assert resp.status_code == 204

        # Individual is gone.
        resp = individuals_client.get(f"/api/individuals/{ind['id']}")
        assert resp.status_code == 404

        # Both sample rows survive with individual_id == NULL.
        for sid in (sample1_id, sample2_id):
            resp = individuals_client.get(f"/api/samples/{sid}")
            assert resp.status_code == 200

        # Inspect the reference DB directly so the assertion doesn't
        # depend on /api/samples projecting the individual_id field.
        ref_engine = sa.create_engine(
            f"sqlite:///{tmp_data_dir / 'reference.db'}"
        )
        try:
            with ref_engine.connect() as conn:
                rows = conn.execute(
                    sa.select(samples.c.id, samples.c.individual_id).where(
                        samples.c.id.in_([sample1_id, sample2_id])
                    )
                ).fetchall()
        finally:
            ref_engine.dispose()
        assert {row.id for row in rows} == {sample1_id, sample2_id}
        assert all(row.individual_id is None for row in rows)

        # Per-sample DB files are untouched.
        assert sample1_db.is_file()
        assert sample2_db.is_file()
