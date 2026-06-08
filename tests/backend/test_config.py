"""Tests for backend.config module."""

from pathlib import Path

import pytest

from backend.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Clear the get_settings lru_cache between tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_default_settings():
    """Settings should load with sensible defaults."""
    settings = get_settings()
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.debug is False
    assert settings.wal_mode is True
    assert settings.auth_enabled is False
    assert settings.theme == "system"
    assert settings.log_level == "INFO"
    assert settings.update_check_interval == "daily"


def test_data_dir_default():
    """Default data_dir should be ~/.yeliztli."""
    settings = get_settings()
    assert settings.data_dir == Path.home() / ".yeliztli"


def test_derived_paths():
    """Derived paths should be relative to data_dir."""
    settings = Settings(data_dir=Path("/tmp/gi-test"))
    assert settings.samples_dir == Path("/tmp/gi-test/samples")
    assert settings.downloads_dir == Path("/tmp/gi-test/downloads")
    assert settings.resolved_log_dir == Path("/tmp/gi-test/logs")
    assert settings.reference_db_path == Path("/tmp/gi-test/reference.db")
    assert settings.vep_bundle_db_path == Path("/tmp/gi-test/vep_bundle.db")
    assert settings.gnomad_db_path == Path("/tmp/gi-test/gnomad_af.db")
    assert settings.dbnsfp_db_path == Path("/tmp/gi-test/dbnsfp.db")


def test_env_override(monkeypatch):
    """Canonical YELIZTLI_ environment variables should override defaults."""
    monkeypatch.setenv("YELIZTLI_PORT", "9000")
    monkeypatch.setenv("YELIZTLI_DEBUG", "true")
    settings = Settings()
    assert settings.port == 9000
    assert settings.debug is True


def test_get_settings_caching():
    """get_settings should return the same instance on repeated calls."""
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2


# --- Back-compat shims (one-release deprecation window) ---


def test_legacy_env_prefix_still_resolves(monkeypatch):
    """Deprecated GENOMEINSIGHT_ env vars still resolve as a fallback."""
    monkeypatch.delenv("YELIZTLI_PORT", raising=False)
    monkeypatch.setenv("GENOMEINSIGHT_PORT", "9100")
    settings = Settings()
    assert settings.port == 9100


def test_canonical_env_prefix_wins_over_legacy(monkeypatch):
    """When both prefixes are set, the canonical YELIZTLI_ wins."""
    monkeypatch.setenv("YELIZTLI_PORT", "9200")
    monkeypatch.setenv("GENOMEINSIGHT_PORT", "9300")
    settings = Settings()
    assert settings.port == 9200


def _write_toml(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_config_toml_section_applied(tmp_path, monkeypatch):
    """Q13 fix: values under the [yeliztli] table reach Settings."""
    monkeypatch.setattr("backend.config.DEFAULT_DATA_DIR", tmp_path)
    _write_toml(
        tmp_path / "config.toml",
        '[yeliztli]\nauth_enabled = true\nauth_password_hash = "abc123"\ntheme = "dark"\n',
    )
    settings = Settings()
    assert settings.auth_enabled is True
    assert settings.auth_password_hash == "abc123"
    assert settings.theme == "dark"


def test_config_toml_legacy_section_fallback(tmp_path, monkeypatch):
    """Legacy [genomeinsight] table is read as a one-release fallback."""
    monkeypatch.setattr("backend.config.DEFAULT_DATA_DIR", tmp_path)
    _write_toml(
        tmp_path / "config.toml",
        '[genomeinsight]\nauth_enabled = true\nauth_password_hash = "legacy"\n',
    )
    settings = Settings()
    assert settings.auth_enabled is True
    assert settings.auth_password_hash == "legacy"


def test_config_toml_data_dir_excluded(tmp_path, monkeypatch):
    """data_dir is never sourced from config.toml (location-defining; avoids stale path)."""
    monkeypatch.setattr("backend.config.DEFAULT_DATA_DIR", tmp_path)
    _write_toml(
        tmp_path / "config.toml",
        f'[yeliztli]\ndata_dir = "{tmp_path / "stale"}"\ntheme = "dark"\n',
    )
    settings = Settings()
    assert settings.theme == "dark"  # other keys still applied
    assert settings.data_dir != tmp_path / "stale"


# --- First-boot data-dir migration ---


def test_migrate_legacy_data_dir(tmp_path, monkeypatch):
    """Legacy dir is renamed to the new default; idempotent."""
    from backend import config as cfg

    legacy = tmp_path / ".genomeinsight"
    target = tmp_path / ".yeliztli"
    legacy.mkdir()
    (legacy / "marker.txt").write_text("data", encoding="utf-8")
    monkeypatch.setattr(cfg, "LEGACY_DATA_DIR", legacy)
    monkeypatch.setattr(cfg, "DEFAULT_DATA_DIR", target)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("YELIZTLI_DATA_DIR", raising=False)
    monkeypatch.delenv("GENOMEINSIGHT_DATA_DIR", raising=False)

    cfg.migrate_legacy_data_dir()
    assert target.exists()
    assert (target / "marker.txt").read_text(encoding="utf-8") == "data"
    assert not legacy.exists()

    # Idempotent: second call is a no-op (target already exists)
    cfg.migrate_legacy_data_dir()
    assert target.exists()


def test_migrate_skipped_under_pytest(tmp_path, monkeypatch):
    """Migration is a no-op while running under pytest (safety guard)."""
    from backend import config as cfg

    legacy = tmp_path / ".genomeinsight"
    target = tmp_path / ".yeliztli"
    legacy.mkdir()
    monkeypatch.setattr(cfg, "LEGACY_DATA_DIR", legacy)
    monkeypatch.setattr(cfg, "DEFAULT_DATA_DIR", target)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")

    cfg.migrate_legacy_data_dir()
    assert legacy.exists()  # untouched
    assert not target.exists()


def test_migrate_skipped_when_data_dir_override_set(tmp_path, monkeypatch):
    """Migration is a no-op when an explicit data-dir env override is set."""
    from backend import config as cfg

    legacy = tmp_path / ".genomeinsight"
    target = tmp_path / ".yeliztli"
    legacy.mkdir()
    monkeypatch.setattr(cfg, "LEGACY_DATA_DIR", legacy)
    monkeypatch.setattr(cfg, "DEFAULT_DATA_DIR", target)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("YELIZTLI_DATA_DIR", str(tmp_path / "custom"))

    cfg.migrate_legacy_data_dir()
    assert legacy.exists()  # untouched
    assert not target.exists()
