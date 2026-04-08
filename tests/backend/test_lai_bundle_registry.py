"""Tests for LAI bundle registry, extraction, and Java detection (Step 3.7).

T-DL-01: LAI bundle listed in registry with correct metadata
T-DL-02: LAI bundle marked as optional
T-DL-03: Bundle extraction creates expected directory structure
T-DL-04: Java detection returns True/False correctly
T-DL-05: Bundle validation checks all 22 chromosome model files
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.db.database_registry import (
    DATABASES,
    detect_java,
    get_database,
    validate_lai_bundle,
)

# ── T-DL-01: LAI bundle listed in registry with correct metadata ─────


class TestLAIBundleRegistry:
    """Verify LAI bundle registry entry metadata."""

    def test_lai_bundle_exists_in_registry(self):
        assert "lai_bundle" in DATABASES

    def test_lai_bundle_metadata(self):
        db = DATABASES["lai_bundle"]
        assert db.name == "lai_bundle"
        assert db.display_name == "LAI Bundle (Chromosome Painting)"
        assert db.filename == "lai_bundle.tar.gz"
        assert db.expected_size_bytes == 523_801_111
        assert db.build_mode == "download"
        assert db.target_db == "standalone"
        assert db.phase == 3

    def test_lai_bundle_has_post_download(self):
        db = DATABASES["lai_bundle"]
        assert db.post_download is not None
        assert callable(db.post_download)

    def test_lai_bundle_url_set(self):
        db = DATABASES["lai_bundle"]
        assert db.url != ""
        assert "lai-bundle" in db.url

    def test_get_database_returns_lai_bundle(self):
        db = get_database("lai_bundle")
        assert db is not None
        assert db.name == "lai_bundle"


# ── T-DL-02: LAI bundle marked as optional ───────────────────────────


class TestLAIBundleOptional:
    """Verify LAI bundle is optional."""

    def test_lai_bundle_not_required(self):
        db = DATABASES["lai_bundle"]
        assert db.required is False


# ── T-DL-03: Bundle extraction creates expected directory structure ───


class TestLAIBundleExtraction:
    """Test the post-download extraction callback."""

    def test_extract_creates_model_dirs(self, tmp_path: Path):
        """Create a minimal tarball and verify extraction."""
        import tarfile

        from backend.db.database_registry import _extract_lai_bundle

        tarball = tmp_path / "test_bundle.tar.gz"
        dest_path = tmp_path / "data" / "lai_bundle.tar.gz"
        dest_path.parent.mkdir(parents=True)

        # Create a minimal tarball with expected structure
        with tarfile.open(tarball, "w:gz") as tf:
            for chrom in range(1, 23):
                for fname in ("base_coefs.npz", "metadata.npz", "smoother.json"):
                    fpath = f"gnomix_models/chr{chrom}/{fname}"
                    info = tarfile.TarInfo(name=fpath)
                    info.size = 4
                    tf.addfile(info, fileobj=__import__("io").BytesIO(b"test"))
            # Add other expected dirs
            for dirname in ("beagle", "genetic_maps", "liftover", "phasing_panel"):
                info = tarfile.TarInfo(name=f"{dirname}/")
                info.type = tarfile.DIRTYPE
                tf.addfile(info)

        # Copy tarball to dest_path location (simulating download)
        import shutil

        shutil.copy2(str(tarball), str(dest_path))

        # Run extraction (uses dest_path to derive dest_dir)
        _extract_lai_bundle(dest_path, dest_path)

        # Verify structure
        lai_dir = dest_path.parent / "lai_bundle"
        assert lai_dir.is_dir()
        for chrom in range(1, 23):
            model_dir = lai_dir / "gnomix_models" / f"chr{chrom}"
            assert model_dir.is_dir(), f"Missing chr{chrom} model dir"
            for fname in ("base_coefs.npz", "metadata.npz", "smoother.json"):
                assert (model_dir / fname).exists(), f"Missing {fname} in chr{chrom}"

    def test_extract_raises_on_incomplete_bundle(self, tmp_path: Path):
        """Extraction should raise if chromosome models are missing."""
        import tarfile

        from backend.db.database_registry import _extract_lai_bundle

        tarball = tmp_path / "incomplete.tar.gz"
        dest_path = tmp_path / "data" / "lai_bundle.tar.gz"
        dest_path.parent.mkdir(parents=True)

        # Only create chr1 — missing chr2-22
        with tarfile.open(tarball, "w:gz") as tf:
            for fname in ("base_coefs.npz", "metadata.npz", "smoother.json"):
                fpath = f"gnomix_models/chr1/{fname}"
                info = tarfile.TarInfo(name=fpath)
                info.size = 4
                tf.addfile(info, fileobj=__import__("io").BytesIO(b"test"))

        import shutil

        shutil.copy2(str(tarball), str(dest_path))

        with pytest.raises(ValueError, match="LAI bundle extraction incomplete"):
            _extract_lai_bundle(dest_path, dest_path)


# ── T-DL-04: Java detection returns True/False correctly ─────────────


class TestJavaDetection:
    """Test Java runtime detection."""

    def test_detect_java_when_present(self):
        with patch("shutil.which", return_value="/usr/bin/java"):
            assert detect_java() is True

    def test_detect_java_when_absent(self):
        with patch("shutil.which", return_value=None):
            assert detect_java() is False


# ── T-DL-05: Bundle validation checks all 22 chromosome model files ──


class TestLAIBundleValidation:
    """Test LAI bundle directory validation."""

    def test_validate_complete_bundle(self, tmp_path: Path):
        """A complete bundle should pass validation."""
        for chrom in range(1, 23):
            model_dir = tmp_path / "gnomix_models" / f"chr{chrom}"
            model_dir.mkdir(parents=True)
            for fname in ("base_coefs.npz", "metadata.npz", "smoother.json"):
                (model_dir / fname).write_text("test")
        assert validate_lai_bundle(tmp_path) is True

    def test_validate_incomplete_bundle(self, tmp_path: Path):
        """A bundle missing chr22 should fail validation."""
        for chrom in range(1, 22):  # Only 1-21
            model_dir = tmp_path / "gnomix_models" / f"chr{chrom}"
            model_dir.mkdir(parents=True)
            for fname in ("base_coefs.npz", "metadata.npz", "smoother.json"):
                (model_dir / fname).write_text("test")
        assert validate_lai_bundle(tmp_path) is False

    def test_validate_missing_file_in_chrom(self, tmp_path: Path):
        """A bundle missing smoother.json in chr10 should fail."""
        for chrom in range(1, 23):
            model_dir = tmp_path / "gnomix_models" / f"chr{chrom}"
            model_dir.mkdir(parents=True)
            for fname in ("base_coefs.npz", "metadata.npz", "smoother.json"):
                if chrom == 10 and fname == "smoother.json":
                    continue
                (model_dir / fname).write_text("test")
        assert validate_lai_bundle(tmp_path) is False

    def test_validate_nonexistent_dir(self, tmp_path: Path):
        """A non-existent directory should fail validation."""
        assert validate_lai_bundle(tmp_path / "nonexistent") is False

    def test_validate_empty_dir(self, tmp_path: Path):
        """An empty directory should fail validation."""
        assert validate_lai_bundle(tmp_path) is False
