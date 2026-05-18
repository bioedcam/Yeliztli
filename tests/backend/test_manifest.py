"""Tests for backend.db.manifest — bundle manifest loader + cache."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from backend.db import manifest as manifest_mod
from backend.db.manifest import (
    BundleManifestEntry,
    Manifest,
    ManifestFetchError,
    PipelinePinEntry,
    fetch_manifest,
    get_bundle_info,
    get_pipeline_pin,
    reset_cache,
)

SAMPLE_PAYLOAD: dict = {
    "schema_version": 1,
    "generated_at": "2026-05-08T00:00:00Z",
    "bundles": {
        "lai_bundle": {
            "version": "v1.1",
            "build_date": "2026-04-07",
            "url": "https://example.com/lai.tar.gz",
            "sha256": "959ed0fd9ebe2ad8fa542776a59ce73072d928c7ce59839ea81d0f1e78a5c18e",
            "size_bytes": 523801111,
        },
        "vep_bundle": {
            "version": "v1.0.0",
            "build_date": "2026-04-10",
            "url": "https://example.com/vep.db",
            "sha256": "1786b5bc1a6f5a0440239f40d5f5ac69d15ce213015a9cbf11affa05bbedfff0",
            "size_bytes": 11374592,
        },
    },
    "pipeline_pins": {
        "clinvar": {
            "url": "https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh37/clinvar.vcf.gz",
            "last_known_version": "",
        },
        "dbnsfp": {
            "url": "https://dist.genos.us/academic/e55b09/dbNSFP5.3.1a.zip",
            "last_known_version": "5.3.1a",
        },
    },
}

V2_PAYLOAD: dict = {
    "schema_version": 1,
    "generated_at": "2026-05-18T00:00:00Z",
    "bundles": {
        "lai_bundle": {
            "version": "v1.1",
            "build_date": "2026-04-07",
            "url": "https://example.com/lai.tar.gz",
            "sha256": "959ed0fd9ebe2ad8fa542776a59ce73072d928c7ce59839ea81d0f1e78a5c18e",
            "size_bytes": 523801111,
        },
        "vep_bundle": {
            "version": "v2.0.0",
            "build_date": "2026-05-18",
            "url": "https://github.com/bioedcam/GenomeInsight/releases/download/bundle-v2.0.0/vep_bundle.db",
            "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
            "size_bytes": 600_000_000,
            "min_app_version": "0.2.0",
        },
    },
    "pipeline_pins": {
        "clinvar": {
            "url": "https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh37/clinvar.vcf.gz",
            "last_known_version": "",
        },
    },
}


@pytest.fixture(autouse=True)
def _clear_cache_and_env(monkeypatch):
    """Each test starts with an empty cache and no env override."""
    monkeypatch.delenv(manifest_mod.MANIFEST_PATH_ENV, raising=False)
    reset_cache()
    yield
    reset_cache()


def _write_manifest(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _make_response(json_data: dict, status: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


# ── dataclass surface ─────────────────────────────────────────────────


class TestDataclasses:
    def test_bundle_entry_is_frozen(self):
        entry = BundleManifestEntry(
            version="v1", build_date="2026-01-01", url="u", sha256="abc", size_bytes=10
        )
        with pytest.raises((AttributeError, TypeError)):
            entry.version = "v2"  # type: ignore[misc]

    def test_pipeline_pin_is_frozen(self):
        pin = PipelinePinEntry(url="u", last_known_version="1.0")
        with pytest.raises((AttributeError, TypeError)):
            pin.url = "x"  # type: ignore[misc]

    def test_manifest_is_frozen(self):
        m = Manifest(schema_version=1, generated_at="now", bundles={}, pipeline_pins={})
        with pytest.raises((AttributeError, TypeError)):
            m.schema_version = 2  # type: ignore[misc]


# ── env-var local override ────────────────────────────────────────────


class TestLocalOverride:
    def test_loads_from_env_path(self, tmp_path: Path, monkeypatch):
        path = _write_manifest(tmp_path / "manifest.json", SAMPLE_PAYLOAD)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        m = fetch_manifest()
        assert m.schema_version == 1
        assert m.generated_at == "2026-05-08T00:00:00Z"
        assert "lai_bundle" in m.bundles
        assert m.bundles["lai_bundle"].version == "v1.1"
        assert m.bundles["lai_bundle"].size_bytes == 523_801_111
        assert m.pipeline_pins["dbnsfp"].last_known_version == "5.3.1a"

    def test_local_override_skips_network(self, tmp_path: Path, monkeypatch):
        path = _write_manifest(tmp_path / "manifest.json", SAMPLE_PAYLOAD)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        with patch("backend.db.manifest.httpx.get") as http_get:
            fetch_manifest()
            http_get.assert_not_called()

    def test_local_override_not_cached(self, tmp_path: Path, monkeypatch):
        """Env override re-reads each call so tests can swap files."""
        path = tmp_path / "manifest.json"
        _write_manifest(path, SAMPLE_PAYLOAD)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        first = fetch_manifest()
        assert first.bundles["lai_bundle"].version == "v1.1"

        updated = json.loads(json.dumps(SAMPLE_PAYLOAD))
        updated["bundles"]["lai_bundle"]["version"] = "v1.2"
        _write_manifest(path, updated)

        second = fetch_manifest()
        assert second.bundles["lai_bundle"].version == "v1.2"

    def test_missing_local_file_raises(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(tmp_path / "nope.json"))
        with pytest.raises(ManifestFetchError):
            fetch_manifest()

    def test_malformed_local_file_raises(self, tmp_path: Path, monkeypatch):
        bad = tmp_path / "manifest.json"
        bad.write_text("{not json", encoding="utf-8")
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(bad))
        with pytest.raises(ManifestFetchError):
            fetch_manifest()


# ── remote fetch + caching ────────────────────────────────────────────


class TestRemoteFetch:
    def test_fetch_returns_parsed_manifest(self):
        resp = _make_response(SAMPLE_PAYLOAD)
        with patch("backend.db.manifest.httpx.get", return_value=resp) as http_get:
            m = fetch_manifest()

        http_get.assert_called_once()
        called_url = http_get.call_args.args[0]
        assert called_url == manifest_mod.MANIFEST_URL
        assert m.bundles["vep_bundle"].sha256.startswith("1786b5bc")

    def test_cache_ttl_skips_second_network_call(self):
        resp = _make_response(SAMPLE_PAYLOAD)
        with patch("backend.db.manifest.httpx.get", return_value=resp) as http_get:
            fetch_manifest()
            fetch_manifest()
        assert http_get.call_count == 1

    def test_cache_expires_after_ttl(self):
        resp = _make_response(SAMPLE_PAYLOAD)
        with (
            patch("backend.db.manifest.httpx.get", return_value=resp) as http_get,
            patch("backend.db.manifest.time.monotonic") as mono,
        ):
            mono.return_value = 1000.0
            fetch_manifest()
            mono.return_value = 1000.0 + manifest_mod.CACHE_TTL_SECONDS + 1
            fetch_manifest()
        assert http_get.call_count == 2

    def test_force_refresh_bypasses_cache(self):
        resp = _make_response(SAMPLE_PAYLOAD)
        with patch("backend.db.manifest.httpx.get", return_value=resp) as http_get:
            fetch_manifest()
            fetch_manifest(force_refresh=True)
        assert http_get.call_count == 2

    def test_network_error_raises_typed(self):
        with patch(
            "backend.db.manifest.httpx.get",
            side_effect=httpx.ConnectError("refused"),
        ):
            with pytest.raises(ManifestFetchError):
                fetch_manifest()

    def test_timeout_raises_typed(self):
        with patch(
            "backend.db.manifest.httpx.get",
            side_effect=httpx.TimeoutException("slow"),
        ):
            with pytest.raises(ManifestFetchError):
                fetch_manifest()

    def test_http_error_status_raises_typed(self):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 500
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "server error", request=MagicMock(), response=resp
        )
        with patch("backend.db.manifest.httpx.get", return_value=resp):
            with pytest.raises(ManifestFetchError):
                fetch_manifest()

    def test_invalid_json_raises_typed(self):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.side_effect = ValueError("not json")
        with patch("backend.db.manifest.httpx.get", return_value=resp):
            with pytest.raises(ManifestFetchError):
                fetch_manifest()

    def test_malformed_payload_raises_typed(self):
        bad = {"schema_version": 1, "bundles": {"x": {"version": "v1"}}}
        resp = _make_response(bad)
        with patch("backend.db.manifest.httpx.get", return_value=resp):
            with pytest.raises(ManifestFetchError):
                fetch_manifest()

    def test_null_required_field_raises_typed(self):
        """Null sha256 (or other required strings) must fail loudly, not str-coerce to 'None'."""
        bad = json.loads(json.dumps(SAMPLE_PAYLOAD))
        bad["bundles"]["lai_bundle"]["sha256"] = None
        resp = _make_response(bad)
        with patch("backend.db.manifest.httpx.get", return_value=resp):
            with pytest.raises(ManifestFetchError, match="sha256"):
                fetch_manifest()

    def test_invalid_sha256_format_raises_typed(self):
        """sha256 must be 64 hex chars."""
        bad = json.loads(json.dumps(SAMPLE_PAYLOAD))
        bad["bundles"]["lai_bundle"]["sha256"] = "not-a-real-hash"
        resp = _make_response(bad)
        with patch("backend.db.manifest.httpx.get", return_value=resp):
            with pytest.raises(ManifestFetchError, match="sha256"):
                fetch_manifest()

    def test_zero_size_bundle_raises_typed(self):
        """size_bytes must be > 0."""
        bad = json.loads(json.dumps(SAMPLE_PAYLOAD))
        bad["bundles"]["lai_bundle"]["size_bytes"] = 0
        resp = _make_response(bad)
        with patch("backend.db.manifest.httpx.get", return_value=resp):
            with pytest.raises(ManifestFetchError, match="size_bytes"):
                fetch_manifest()

    def test_missing_top_level_sections_raises_typed(self):
        """Missing bundles or pipeline_pins must fail loudly, not silently become empty."""
        for missing in ("bundles", "pipeline_pins"):
            bad = json.loads(json.dumps(SAMPLE_PAYLOAD))
            del bad[missing]
            resp = _make_response(bad)
            with patch("backend.db.manifest.httpx.get", return_value=resp):
                with pytest.raises(ManifestFetchError, match="required"):
                    fetch_manifest()
            reset_cache()

    def test_non_object_payload_raises_typed(self):
        resp = _make_response([])  # type: ignore[arg-type]
        with patch("backend.db.manifest.httpx.get", return_value=resp):
            with pytest.raises(ManifestFetchError):
                fetch_manifest()

    def test_failure_does_not_pollute_cache(self):
        resp_bad = MagicMock(spec=httpx.Response)
        resp_bad.status_code = 200
        resp_bad.raise_for_status = MagicMock()
        resp_bad.json.side_effect = ValueError("bad")
        resp_good = _make_response(SAMPLE_PAYLOAD)

        with patch(
            "backend.db.manifest.httpx.get",
            side_effect=[resp_bad, resp_good],
        ) as http_get:
            with pytest.raises(ManifestFetchError):
                fetch_manifest()
            m = fetch_manifest()

        assert http_get.call_count == 2
        assert m.bundles["lai_bundle"].version == "v1.1"


# ── accessor helpers ──────────────────────────────────────────────────


class TestAccessors:
    def test_get_bundle_info_returns_entry(self, tmp_path: Path, monkeypatch):
        path = _write_manifest(tmp_path / "manifest.json", SAMPLE_PAYLOAD)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        entry = get_bundle_info("lai_bundle")
        assert entry is not None
        assert entry.version == "v1.1"
        assert entry.size_bytes == 523_801_111

    def test_get_bundle_info_unknown_returns_none(self, tmp_path: Path, monkeypatch):
        path = _write_manifest(tmp_path / "manifest.json", SAMPLE_PAYLOAD)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        assert get_bundle_info("not_a_bundle") is None

    def test_get_bundle_info_swallows_fetch_error(self):
        with patch(
            "backend.db.manifest.httpx.get",
            side_effect=httpx.ConnectError("nope"),
        ):
            assert get_bundle_info("lai_bundle") is None

    def test_get_pipeline_pin_returns_entry(self, tmp_path: Path, monkeypatch):
        path = _write_manifest(tmp_path / "manifest.json", SAMPLE_PAYLOAD)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        pin = get_pipeline_pin("dbnsfp")
        assert pin is not None
        assert pin.last_known_version == "5.3.1a"
        assert pin.url.endswith("dbNSFP5.3.1a.zip")

    def test_get_pipeline_pin_unknown_returns_none(self, tmp_path: Path, monkeypatch):
        path = _write_manifest(tmp_path / "manifest.json", SAMPLE_PAYLOAD)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        assert get_pipeline_pin("nope") is None

    def test_get_pipeline_pin_swallows_fetch_error(self):
        with patch(
            "backend.db.manifest.httpx.get",
            side_effect=httpx.ConnectError("nope"),
        ):
            assert get_pipeline_pin("clinvar") is None


# ── v2.0.0 bundle fixture ─────────────────────────────────────────────


class TestBundleV2:
    """The v2.0.0 vep_bundle fixture loads with all current fields and
    tolerates the additive ``min_app_version`` JSON key (parser support for
    that field lands in step 5)."""

    def test_v2_manifest_loads_with_all_fields(self, tmp_path: Path, monkeypatch):
        path = _write_manifest(tmp_path / "manifest.json", V2_PAYLOAD)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        m = fetch_manifest()
        entry = m.bundles["vep_bundle"]
        assert entry.version == "v2.0.0"
        assert entry.build_date == "2026-05-18"
        assert entry.url.endswith("/bundle-v2.0.0/vep_bundle.db")
        assert entry.sha256 == "0" * 64
        assert entry.size_bytes == 600_000_000

    def test_v2_manifest_tolerates_min_app_version_additive_key(
        self, tmp_path: Path, monkeypatch
    ):
        """Forward-compat contract — additive keys must not break older parsers."""
        path = _write_manifest(tmp_path / "manifest.json", V2_PAYLOAD)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        m = fetch_manifest()
        assert "vep_bundle" in m.bundles
        assert "min_app_version" in V2_PAYLOAD["bundles"]["vep_bundle"]

    def test_legacy_v1_version_normalized_to_v1_0_0(self):
        """Prior ``v1.0`` was normalized to ``v1.0.0`` for clean semver compare."""
        assert SAMPLE_PAYLOAD["bundles"]["vep_bundle"]["version"] == "v1.0.0"


# ── repo manifest sanity (defensive — catches drift in bundles/manifest.json) ──


class TestRepoManifest:
    def test_repo_manifest_loads_and_has_required_bundles(self, monkeypatch):
        repo_root = Path(__file__).resolve().parents[2]
        path = repo_root / "bundles" / "manifest.json"
        if not path.is_file():
            pytest.skip("bundles/manifest.json not present in this checkout")
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        m = fetch_manifest()
        assert m.schema_version == 1
        for required in ("lai_bundle", "vep_bundle", "ancestry_pca"):
            assert required in m.bundles, f"missing bundle entry: {required}"
        required_pins = (
            "clinvar",
            "gnomad",
            "dbnsfp",
            "cpic",
            "gwas_catalog",
            "dbsnp",
            "mondo_hpo",
        )
        for required in required_pins:
            assert required in m.pipeline_pins, f"missing pipeline pin: {required}"

    def test_repo_manifest_vep_bundle_is_v2_0_0(self, monkeypatch):
        """Pins step 4's manifest update against accidental rollback."""
        repo_root = Path(__file__).resolve().parents[2]
        path = repo_root / "bundles" / "manifest.json"
        if not path.is_file():
            pytest.skip("bundles/manifest.json not present in this checkout")
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        m = fetch_manifest()
        vep = m.bundles["vep_bundle"]
        assert vep.version == "v2.0.0"
        assert vep.url.endswith("/bundle-v2.0.0/vep_bundle.db")
        assert vep.size_bytes >= 100_000_000  # release-asset territory (>100 MB)
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["bundles"]["vep_bundle"]["min_app_version"] == "0.2.0"
