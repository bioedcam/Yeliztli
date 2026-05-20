"""HTTP 409 vep_bundle gate on AncestryDNA uploads (Plan §5.4, Step 7).

Three cases lock the gate:
- AncestryDNA + bundle < v2.0.0  → 409 with the §5.4 payload shape.
- AncestryDNA + bundle >= v2.0.0 → 202 (gate falls through to the parser).
- 23andMe       + bundle < v2.0.0 → 202 (gate is vendor-scoped).

The "AncestryDNA + v2 → 202" case patches ``parse_23andme`` because the
real AncestryDNA parser does not land until Phase 1 (steps 26–31); the
patch isolates the test to the gate's branching, which is the only
behavior step 7 introduces.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.tables import database_versions, reference_metadata
from backend.ingestion.base import ParsedVariant, ParseResult, SourceVendor

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
V5_FILE = FIXTURES / "sample_23andme_v5.txt"
ANCESTRY_FILE = FIXTURES / "sample_ancestrydna.txt"
REPO_MANIFEST = Path(__file__).resolve().parents[2] / "bundles" / "manifest.json"


def _seed_vep_bundle_version(ref_path: Path, version: str) -> None:
    engine = sa.create_engine(f"sqlite:///{ref_path}")
    with engine.begin() as conn:
        conn.execute(
            database_versions.insert().values(
                db_name="vep_bundle",
                version=version,
                file_path=None,
                file_size_bytes=None,
                downloaded_at=datetime.now(UTC),
                checksum_sha256=None,
            )
        )
    engine.dispose()


@pytest.fixture
def manifest_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point manifest fetches at the in-repo manifest so the 409 payload
    is built deterministically (no network)."""
    monkeypatch.setenv("GENOMEINSIGHT_MANIFEST_PATH", str(REPO_MANIFEST))
    from backend.db.manifest import reset_cache

    reset_cache()
    yield
    reset_cache()


def _make_client(tmp_data_dir: Path, *, vep_bundle_version: str | None) -> TestClient:
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
    ref_path = settings.reference_db_path
    engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(engine)
    engine.dispose()

    if vep_bundle_version is not None:
        _seed_vep_bundle_version(ref_path, vep_bundle_version)

    patchers = [
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
        patch("backend.api.routes.ingest.get_registry"),
        patch("backend.api.routes.samples.get_registry"),
    ]
    started = [p.start() for p in patchers]

    from backend.db.connection import DBRegistry, reset_registry

    reset_registry()
    registry = DBRegistry(settings)
    # Indices 2 and 3 are the get_registry patches.
    started[2].return_value = registry
    started[3].return_value = registry

    from backend.main import create_app

    app = create_app()
    client = TestClient(app)

    def _close() -> None:
        client.close()
        registry.dispose_all()
        reset_registry()
        for p in patchers:
            p.stop()

    client.__teardown__ = _close
    return client


@pytest.fixture
def client_factory(tmp_data_dir: Path):
    clients: list[TestClient] = []

    def _factory(vep_bundle_version: str | None) -> TestClient:
        c = _make_client(tmp_data_dir, vep_bundle_version=vep_bundle_version)
        clients.append(c)
        return c

    yield _factory

    for c in clients:
        c.__teardown__()


def test_ancestrydna_with_v1_bundle_returns_409(manifest_env, client_factory) -> None:
    client = client_factory("v1.0.0")
    with open(ANCESTRY_FILE, "rb") as f:
        response = client.post(
            "/api/ingest",
            files={"file": ("ancestry.txt", f, "text/plain")},
        )
    assert response.status_code == 409, response.text
    payload = response.json()["detail"]
    assert payload["error"] == "bundle_version_too_old"
    assert payload["installed_version"] == "v1.0.0"
    assert payload["required_version"] == "v2.0.0"
    assert payload["vendor"] == "ancestrydna"
    assert payload["update_url"]  # non-empty
    assert payload["size_bytes"] > 0
    assert isinstance(payload["checksum_sha256"], str)
    assert len(payload["checksum_sha256"]) == 64


def test_ancestrydna_with_v2_bundle_returns_202(manifest_env, client_factory) -> None:
    # Real AncestryDNA parser lands in step 30; stub the parser so the test
    # locks step 7's branching: gate not fired ⇒ ingest succeeds.
    fake_result = ParseResult(
        vendor=SourceVendor.TWENTYTHREEANDME,
        version="v5",
        build="GRCh37",
        variants=[ParsedVariant(rsid="rs1", chrom="1", pos=100, genotype="AA")],
        nocall_count=0,
        total_lines=1,
        skipped_lines=0,
    )
    client = client_factory("v2.0.0")
    with (
        patch("backend.api.routes.ingest.parse_23andme", return_value=fake_result),
        open(ANCESTRY_FILE, "rb") as f,
    ):
        response = client.post(
            "/api/ingest",
            files={"file": ("ancestry.txt", f, "text/plain")},
        )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["variant_count"] == 1


def test_23andme_with_v1_bundle_returns_202(manifest_env, client_factory) -> None:
    client = client_factory("v1.0.0")
    with open(V5_FILE, "rb") as f:
        response = client.post(
            "/api/ingest",
            files={"file": ("sample.txt", f, "text/plain")},
        )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["file_format"] == "23andme_v5"
