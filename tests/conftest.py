"""Root-level pytest conftest — project-wide fixtures and markers."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from backend.config import Settings


def _java_available() -> bool:
    """Return True if a Java runtime is on PATH."""
    return shutil.which("java") is not None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-skip tests marked ``requires_java`` when Java is absent."""
    if _java_available():
        return
    skip_java = pytest.mark.skip(reason="Java runtime not available")
    for item in items:
        if "requires_java" in item.keywords:
            item.add_marker(skip_java)


# ── Custom Markers ───────────────────────────────────────────────────


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers to avoid 'unknown marker' warnings."""
    config.addinivalue_line(
        "markers",
        "slow: marks tests as slow (deselect with '-m not slow')",
    )
    config.addinivalue_line("markers", "e2e: marks end-to-end tests")
    config.addinivalue_line("markers", "integration: marks integration tests")
    config.addinivalue_line(
        "markers",
        "requires_java: marks tests that need a real Java runtime (skipped when unavailable)",
    )


# ── Project-wide Fixtures ────────────────────────────────────────────


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Create a temporary data directory mimicking ~/.genomeinsight layout.

    Creates the standard subdirectories (samples, downloads, logs) so that
    Settings and DBRegistry can operate without error.
    """
    (tmp_path / "samples").mkdir()
    (tmp_path / "downloads").mkdir()
    (tmp_path / "logs").mkdir()
    return tmp_path


@pytest.fixture
def test_settings(tmp_data_dir: Path) -> Settings:
    """Return a Settings instance pointing at the temporary data directory.

    WAL mode is disabled for in-memory / temp-file SQLite to avoid
    PRAGMA errors that do not apply to ephemeral databases.
    """
    return Settings(data_dir=tmp_data_dir, wal_mode=False)
