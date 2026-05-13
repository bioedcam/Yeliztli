"""Tests for the database update manager (P4-16).

Covers:
- Bandwidth window parsing and enforcement
- Version checking (get_current_version)
- ClinVar update check (HTTP HEAD mock)
- Differential ClinVar update (reclassification detection)
- Re-annotation pre-check (single sample, all samples)
- Re-annotation prompt CRUD (create, get, dismiss)
- Update history recording and retrieval
- Scheduled update orchestrator
- Huey task wrappers
- API endpoints
"""

from __future__ import annotations

from datetime import time as dt_time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    annotated_variants,
    clinvar_variants,
    database_versions,
    reference_metadata,
    watched_variants,
)
from backend.db.update_manager import (
    BANDWIDTH_WINDOW_THRESHOLD,
    UpdateCheckResult,
    UpdateResult,
    VersionInfo,
    _create_reannotation_prompt,
    _precheck_clinvar,
    _record_update_history,
    _record_version,
    check_all_updates,
    check_clinvar_update,
    dismiss_prompt,
    format_version_display,
    get_active_prompts,
    get_all_version_stamps,
    get_current_version,
    get_update_history,
    parse_time_window,
    run_scheduled_update_check,
    should_download_now,
)


def _settings_mock(tmp_path: Path) -> MagicMock:
    """MagicMock Settings with a real Path-backed data_dir / reference_db_path.

    Guards against stringified-mock paths leaking into CWD if a runner code
    path touches settings.data_dir before its patch fires.
    """
    settings = MagicMock()
    settings.data_dir = tmp_path
    settings.reference_db_path = tmp_path / "reference.db"
    settings.downloads_dir = tmp_path / "downloads"
    settings.samples_dir = tmp_path / "samples"
    settings.update_download_window = None
    return settings


# ═══════════════════════════════════════════════════════════════════════
# Bandwidth window tests
# ═══════════════════════════════════════════════════════════════════════


class TestParseTimeWindow:
    def test_normal_window(self):
        start, end = parse_time_window("02:00-06:00")
        assert start == dt_time(2, 0)
        assert end == dt_time(6, 0)

    def test_midnight_span(self):
        start, end = parse_time_window("22:00-06:00")
        assert start == dt_time(22, 0)
        assert end == dt_time(6, 0)

    def test_with_spaces(self):
        start, end = parse_time_window(" 03:30 - 05:45 ")
        assert start == dt_time(3, 30)
        assert end == dt_time(5, 45)

    def test_invalid_format(self):
        with pytest.raises(ValueError, match="Invalid time window"):
            parse_time_window("not-a-window")

    def test_single_time(self):
        with pytest.raises(ValueError, match="Invalid time window"):
            parse_time_window("02:00")


class TestShouldDownloadNow:
    def test_small_download_always_proceeds(self):
        # Under 100 MB threshold
        assert should_download_now(50 * 1024 * 1024, "02:00-06:00") is True

    def test_no_window_always_proceeds(self):
        assert should_download_now(200 * 1024 * 1024, None) is True

    def test_large_download_within_window(self):
        with patch("backend.db.update_manager.datetime") as mock_dt:
            mock_dt.now.return_value.time.return_value = dt_time(3, 0)
            assert should_download_now(200 * 1024 * 1024, "02:00-06:00") is True

    def test_large_download_outside_window(self):
        with patch("backend.db.update_manager.datetime") as mock_dt:
            mock_dt.now.return_value.time.return_value = dt_time(12, 0)
            assert should_download_now(200 * 1024 * 1024, "02:00-06:00") is False

    def test_exactly_at_threshold_is_gated(self):
        # Exactly 100 MB is NOT under threshold (< not <=), so bandwidth window applies
        with patch("backend.db.update_manager.datetime") as mock_dt:
            mock_dt.now.return_value.time.return_value = dt_time(12, 0)
            assert should_download_now(BANDWIDTH_WINDOW_THRESHOLD, "02:00-06:00") is False


# ═══════════════════════════════════════════════════════════════════════
# Version management tests
# ═══════════════════════════════════════════════════════════════════════


class TestGetCurrentVersion:
    def test_no_version(self, reference_engine):
        assert get_current_version(reference_engine, "clinvar") is None

    def test_with_version(self, reference_engine):
        with reference_engine.begin() as conn:
            conn.execute(database_versions.insert().values(db_name="clinvar", version="20260301"))
        assert get_current_version(reference_engine, "clinvar") == "20260301"


class TestRecordVersion:
    def test_insert_new(self, reference_engine):
        _record_version(reference_engine, "clinvar", "20260301", 1000)
        v = get_current_version(reference_engine, "clinvar")
        assert v == "20260301"

    def test_update_existing(self, reference_engine):
        _record_version(reference_engine, "clinvar", "20260301", 1000)
        _record_version(reference_engine, "clinvar", "20260315", 1100)
        v = get_current_version(reference_engine, "clinvar")
        assert v == "20260315"


# ═══════════════════════════════════════════════════════════════════════
# ClinVar update check tests
# ═══════════════════════════════════════════════════════════════════════


class TestCheckClinvarUpdate:
    def test_update_available(self, reference_engine):
        # Set old version
        _record_version(reference_engine, "clinvar", "20250101")

        mock_resp = MagicMock()
        mock_resp.headers = {
            "Last-Modified": "Thu, 20 Mar 2026 00:00:00 GMT",
            "Content-Length": "30000000",
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("backend.db.update_manager.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.head.return_value = mock_resp

            result = check_clinvar_update(reference_engine)

        assert result is not None
        assert result.db_name == "clinvar"
        assert result.latest_version == "20260320"
        assert result.download_size_bytes == 30000000

    def test_already_up_to_date(self, reference_engine):
        _record_version(reference_engine, "clinvar", "20260401")

        mock_resp = MagicMock()
        mock_resp.headers = {
            "Last-Modified": "Thu, 20 Mar 2026 00:00:00 GMT",
            "Content-Length": "30000000",
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("backend.db.update_manager.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.head.return_value = mock_resp

            result = check_clinvar_update(reference_engine)

        assert result is None

    def test_network_error_returns_none(self, reference_engine):
        with patch("backend.db.update_manager.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.head.side_effect = Exception("Network error")

            result = check_clinvar_update(reference_engine)
        assert result is None


class TestCheckAllUpdates:
    def test_returns_result(self, reference_engine):
        with patch("backend.db.update_manager.check_clinvar_update", return_value=None):
            result = check_all_updates(reference_engine)

        assert isinstance(result, UpdateCheckResult)
        assert "clinvar" in result.up_to_date


# ═══════════════════════════════════════════════════════════════════════
# Update history tests
# ═══════════════════════════════════════════════════════════════════════


class TestUpdateHistory:
    def test_record_and_retrieve(self, reference_engine):
        _record_update_history(
            reference_engine,
            db_name="clinvar",
            previous_version="20260301",
            new_version="20260315",
            variants_added=100,
            variants_reclassified=5,
            download_size_bytes=30000000,
            duration_seconds=45,
        )

        history = get_update_history(reference_engine)
        assert len(history) == 1
        assert history[0]["db_name"] == "clinvar"
        assert history[0]["previous_version"] == "20260301"
        assert history[0]["new_version"] == "20260315"
        assert history[0]["variants_added"] == 100
        assert history[0]["variants_reclassified"] == 5

    def test_filter_by_db_name(self, reference_engine):
        _record_update_history(
            reference_engine,
            db_name="clinvar",
            previous_version=None,
            new_version="20260301",
        )
        _record_update_history(
            reference_engine,
            db_name="gwas",
            previous_version=None,
            new_version="2026-03",
        )

        clinvar_history = get_update_history(reference_engine, db_name="clinvar")
        assert len(clinvar_history) == 1
        assert clinvar_history[0]["db_name"] == "clinvar"

    def test_limit(self, reference_engine):
        for i in range(10):
            _record_update_history(
                reference_engine,
                db_name="clinvar",
                previous_version=None,
                new_version=f"2026030{i}",
            )

        history = get_update_history(reference_engine, limit=3)
        assert len(history) == 3

    def test_ordered_most_recent_first(self, reference_engine):
        _record_update_history(
            reference_engine,
            db_name="clinvar",
            previous_version=None,
            new_version="20260301",
        )
        _record_update_history(
            reference_engine,
            db_name="clinvar",
            previous_version="20260301",
            new_version="20260315",
        )

        history = get_update_history(reference_engine)
        assert history[0]["new_version"] == "20260315"
        assert history[1]["new_version"] == "20260301"


# ═══════════════════════════════════════════════════════════════════════
# Re-annotation prompt tests
# ═══════════════════════════════════════════════════════════════════════


class TestReannotationPrompts:
    def test_create_prompt(self, reference_engine):
        _create_reannotation_prompt(
            reference_engine,
            sample_id=1,
            db_name="clinvar",
            db_version="20260315",
            candidate_count=3,
        )

        prompts = get_active_prompts(reference_engine)
        assert len(prompts) == 1
        assert prompts[0]["sample_id"] == 1
        assert prompts[0]["db_name"] == "clinvar"
        assert prompts[0]["candidate_count"] == 3

    def test_update_existing_prompt(self, reference_engine):
        _create_reannotation_prompt(
            reference_engine,
            sample_id=1,
            db_name="clinvar",
            db_version="20260301",
            candidate_count=2,
        )
        _create_reannotation_prompt(
            reference_engine,
            sample_id=1,
            db_name="clinvar",
            db_version="20260315",
            candidate_count=5,
        )

        prompts = get_active_prompts(reference_engine)
        assert len(prompts) == 1
        assert prompts[0]["candidate_count"] == 5
        assert prompts[0]["db_version"] == "20260315"

    def test_dismiss_prompt(self, reference_engine):
        _create_reannotation_prompt(
            reference_engine,
            sample_id=1,
            db_name="clinvar",
            db_version="20260315",
            candidate_count=3,
        )

        prompts = get_active_prompts(reference_engine)
        assert len(prompts) == 1

        ok = dismiss_prompt(reference_engine, prompts[0]["id"])
        assert ok is True

        prompts = get_active_prompts(reference_engine)
        assert len(prompts) == 0

    def test_dismiss_nonexistent(self, reference_engine):
        ok = dismiss_prompt(reference_engine, 999)
        assert ok is False

    def test_create_prompt_with_watched_data(self, reference_engine):
        """T4-22m: Prompt stores watched variant reclassification details."""
        watched = [
            {
                "rsid": "rs80357906",
                "gene_symbol": "BRCA2",
                "old_significance": "Uncertain_significance",
                "new_significance": "Likely_pathogenic",
            }
        ]
        _create_reannotation_prompt(
            reference_engine,
            sample_id=1,
            db_name="clinvar",
            db_version="20260315",
            candidate_count=3,
            watched_count=1,
            watched_details=watched,
        )

        prompts = get_active_prompts(reference_engine)
        assert len(prompts) == 1
        assert prompts[0]["watched_count"] == 1
        assert len(prompts[0]["watched_details"]) == 1
        assert prompts[0]["watched_details"][0]["rsid"] == "rs80357906"
        assert prompts[0]["watched_details"][0]["new_significance"] == "Likely_pathogenic"

    def test_prompt_no_watched_data_defaults(self, reference_engine):
        """T4-22n: Prompt without watched data has zero count and empty list."""
        _create_reannotation_prompt(
            reference_engine,
            sample_id=1,
            db_name="clinvar",
            db_version="20260315",
            candidate_count=3,
        )

        prompts = get_active_prompts(reference_engine)
        assert len(prompts) == 1
        assert prompts[0]["watched_count"] == 0
        assert prompts[0]["watched_details"] == []

    def test_update_prompt_with_watched_data(self, reference_engine):
        """Updating a prompt replaces watched data."""
        _create_reannotation_prompt(
            reference_engine,
            sample_id=1,
            db_name="clinvar",
            db_version="20260301",
            candidate_count=2,
        )
        watched = [
            {
                "rsid": "rs80357906",
                "gene_symbol": "BRCA2",
                "old_significance": "Uncertain_significance",
                "new_significance": "Likely_pathogenic",
            }
        ]
        _create_reannotation_prompt(
            reference_engine,
            sample_id=1,
            db_name="clinvar",
            db_version="20260315",
            candidate_count=5,
            watched_count=1,
            watched_details=watched,
        )

        prompts = get_active_prompts(reference_engine)
        assert len(prompts) == 1
        assert prompts[0]["candidate_count"] == 5
        assert prompts[0]["watched_count"] == 1
        assert prompts[0]["watched_details"][0]["rsid"] == "rs80357906"

    def test_filter_by_sample_id(self, reference_engine):
        _create_reannotation_prompt(
            reference_engine,
            sample_id=1,
            db_name="clinvar",
            db_version="20260315",
            candidate_count=3,
        )
        _create_reannotation_prompt(
            reference_engine,
            sample_id=2,
            db_name="clinvar",
            db_version="20260315",
            candidate_count=1,
        )

        prompts = get_active_prompts(reference_engine, sample_id=1)
        assert len(prompts) == 1
        assert prompts[0]["sample_id"] == 1


# ═══════════════════════════════════════════════════════════════════════
# Pre-check tests
# ═══════════════════════════════════════════════════════════════════════


class TestPrecheck:
    def test_clinvar_precheck_with_reclassification(self, reference_engine, sample_engine):
        # Seed sample with annotated variants
        create_sample_tables(sample_engine)
        with sample_engine.begin() as conn:
            conn.execute(
                annotated_variants.insert(),
                [
                    {
                        "rsid": "rs429358",
                        "chrom": "19",
                        "pos": 44908684,
                        "clinvar_significance": "risk_factor",
                        "annotation_coverage": 2,
                    },
                    {
                        "rsid": "rs7412",
                        "chrom": "19",
                        "pos": 44908822,
                        "clinvar_significance": "benign",
                        "annotation_coverage": 2,
                    },
                ],
            )

        old_sigs = {"rs429358": "risk_factor", "rs7412": "benign"}
        new_sigs = {"rs429358": "risk_factor", "rs7412": "Pathogenic"}

        result = _precheck_clinvar(
            sample_engine,
            reference_engine,
            sample_id=1,
            sample_name="Test",
            old_significances=old_sigs,
            new_significances=new_sigs,
        )

        assert result.candidate_count == 1
        assert result.reclassified_variants[0]["rsid"] == "rs7412"
        assert result.reclassified_variants[0]["old_significance"] == "benign"
        assert result.reclassified_variants[0]["new_significance"] == "Pathogenic"

    def test_precheck_no_changes(self, reference_engine, sample_engine):
        create_sample_tables(sample_engine)
        with sample_engine.begin() as conn:
            conn.execute(
                annotated_variants.insert(),
                [
                    {
                        "rsid": "rs429358",
                        "chrom": "19",
                        "pos": 44908684,
                        "clinvar_significance": "risk_factor",
                        "annotation_coverage": 2,
                    },
                ],
            )

        old_sigs = {"rs429358": "risk_factor"}
        new_sigs = {"rs429358": "risk_factor"}

        result = _precheck_clinvar(
            sample_engine,
            reference_engine,
            sample_id=1,
            sample_name="Test",
            old_significances=old_sigs,
            new_significances=new_sigs,
        )

        assert result.candidate_count == 0

    def test_precheck_empty_sample(self, reference_engine, sample_engine):
        create_sample_tables(sample_engine)

        result = _precheck_clinvar(
            sample_engine,
            reference_engine,
            sample_id=1,
            sample_name="Test",
            old_significances={},
            new_significances={},
        )
        assert result.candidate_count == 0

    def test_precheck_watched_variant_reclassification(self, reference_engine, sample_engine):
        create_sample_tables(sample_engine)
        with sample_engine.begin() as conn:
            conn.execute(
                annotated_variants.insert(),
                [
                    {
                        "rsid": "rs80357906",
                        "chrom": "17",
                        "pos": 43091983,
                        "clinvar_significance": "Uncertain_significance",
                        "gene_symbol": "BRCA2",
                        "annotation_coverage": 2,
                    },
                ],
            )
            conn.execute(
                watched_variants.insert(),
                [
                    {
                        "rsid": "rs80357906",
                        "clinvar_significance_at_watch": "Uncertain_significance",
                    },
                ],
            )

        old_sigs = {"rs80357906": "Uncertain_significance"}
        new_sigs = {"rs80357906": "Likely_pathogenic"}

        result = _precheck_clinvar(
            sample_engine,
            reference_engine,
            sample_id=1,
            sample_name="Test",
            old_significances=old_sigs,
            new_significances=new_sigs,
        )

        assert result.candidate_count == 1
        assert len(result.watched_reclassified) == 1
        assert result.watched_reclassified[0]["rsid"] == "rs80357906"
        assert result.watched_reclassified[0]["new_significance"] == "Likely_pathogenic"

    def test_precheck_watched_no_significance_change(self, reference_engine, sample_engine):
        """T4-22n: Pre-check does NOT upgrade banner when watched variant has no change."""
        create_sample_tables(sample_engine)
        with sample_engine.begin() as conn:
            conn.execute(
                annotated_variants.insert(),
                [
                    {
                        "rsid": "rs80357906",
                        "chrom": "17",
                        "pos": 43091983,
                        "clinvar_significance": "Uncertain_significance",
                        "gene_symbol": "BRCA2",
                        "annotation_coverage": 2,
                    },
                ],
            )
            conn.execute(
                watched_variants.insert(),
                [
                    {
                        "rsid": "rs80357906",
                        "clinvar_significance_at_watch": "Uncertain_significance",
                    },
                ],
            )

        # Significance stays the same
        old_sigs = {"rs80357906": "Uncertain_significance"}
        new_sigs = {"rs80357906": "Uncertain_significance"}

        result = _precheck_clinvar(
            sample_engine,
            reference_engine,
            sample_id=1,
            sample_name="Test",
            old_significances=old_sigs,
            new_significances=new_sigs,
        )

        assert result.candidate_count == 0
        assert len(result.watched_reclassified) == 0

    def test_precheck_watched_direct_query_reclassification(self, reference_engine, sample_engine):
        """T4-22m variant: Watched variant reclassification detected via direct query path."""
        create_sample_tables(sample_engine)
        with sample_engine.begin() as conn:
            conn.execute(
                annotated_variants.insert(),
                [
                    {
                        "rsid": "rs80357906",
                        "chrom": "17",
                        "pos": 43091983,
                        "clinvar_significance": "Uncertain_significance",
                        "gene_symbol": "BRCA2",
                        "annotation_coverage": 2,
                    },
                ],
            )
            conn.execute(
                watched_variants.insert(),
                [
                    {
                        "rsid": "rs80357906",
                        "clinvar_significance_at_watch": "Uncertain_significance",
                    },
                ],
            )

        # Seed reference DB with updated ClinVar significance
        with reference_engine.begin() as conn:
            conn.execute(
                clinvar_variants.insert(),
                [
                    {
                        "rsid": "rs80357906",
                        "chrom": "17",
                        "pos": 43091983,
                        "ref": "A",
                        "alt": "G",
                        "significance": "Likely_pathogenic",
                        "review_stars": 2,
                    },
                ],
            )

        # Call without precomputed dicts (direct query path)
        result = _precheck_clinvar(
            sample_engine,
            reference_engine,
            sample_id=1,
            sample_name="Test",
        )

        assert result.candidate_count == 1
        assert len(result.watched_reclassified) == 1
        assert result.watched_reclassified[0]["rsid"] == "rs80357906"
        assert result.watched_reclassified[0]["old_significance"] == "Uncertain_significance"
        assert result.watched_reclassified[0]["new_significance"] == "Likely_pathogenic"

    def test_precheck_direct_query(self, reference_engine, sample_engine):
        """Pre-check without precomputed dicts (queries reference DB)."""
        create_sample_tables(sample_engine)

        # Seed reference with updated significance
        with reference_engine.begin() as conn:
            conn.execute(
                clinvar_variants.insert(),
                [
                    {
                        "rsid": "rs429358",
                        "chrom": "19",
                        "pos": 44908684,
                        "ref": "T",
                        "alt": "C",
                        "significance": "Pathogenic",
                        "review_stars": 3,
                    },
                ],
            )

        # Sample has old significance
        with sample_engine.begin() as conn:
            conn.execute(
                annotated_variants.insert(),
                [
                    {
                        "rsid": "rs429358",
                        "chrom": "19",
                        "pos": 44908684,
                        "clinvar_significance": "risk_factor",
                        "annotation_coverage": 2,
                    },
                ],
            )

        result = _precheck_clinvar(
            sample_engine,
            reference_engine,
            sample_id=1,
            sample_name="Test",
        )

        assert result.candidate_count == 1
        assert result.reclassified_variants[0]["new_significance"] == "Pathogenic"


# ═══════════════════════════════════════════════════════════════════════
# Scheduled update orchestrator tests
# ═══════════════════════════════════════════════════════════════════════


class TestScheduledUpdateCheck:
    def test_orchestrator_skips_auto_disabled(self, reference_engine, tmp_path: Path):
        settings = _settings_mock(tmp_path)

        registry = MagicMock()
        registry.reference_engine = reference_engine
        registry.settings = settings

        update_info = VersionInfo(
            db_name="gnomad",
            latest_version="4.0",
            download_url="https://example.com/gnomad.db.gz",
            download_size_bytes=2_000_000_000,
        )

        with patch(
            "backend.db.update_manager.check_all_updates",
            return_value=UpdateCheckResult(available=[update_info]),
        ):
            result = run_scheduled_update_check(registry)

        # gnomAD is auto-update disabled, so no update should run
        assert len(result.available) == 1

    def test_orchestrator_runs_clinvar_auto_update(self, reference_engine, tmp_path: Path):
        settings = _settings_mock(tmp_path)

        registry = MagicMock()
        registry.reference_engine = reference_engine
        registry.settings = settings

        update_info = VersionInfo(
            db_name="clinvar",
            latest_version="20260320",
            download_url="https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh37/clinvar.vcf.gz",
            download_size_bytes=30_000_000,
        )

        with (
            patch(
                "backend.db.update_manager.check_all_updates",
                return_value=UpdateCheckResult(available=[update_info]),
            ),
            patch(
                "backend.db.update_manager.run_clinvar_update",
                return_value=UpdateResult(
                    db_name="clinvar",
                    previous_version="20260301",
                    new_version="20260320",
                ),
            ) as mock_update,
        ):
            run_scheduled_update_check(registry)

        mock_update.assert_called_once_with(registry)


# ═══════════════════════════════════════════════════════════════════════
# API endpoint tests
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def update_client(tmp_data_dir: Path) -> TestClient:
    """FastAPI TestClient with update routes enabled."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_path = settings.reference_db_path
    engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(engine)
    engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()

        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc

        reset_registry()


class TestUpdateAPI:
    def test_check_updates(self, update_client):
        with patch("backend.db.update_manager.check_clinvar_update", return_value=None):
            resp = update_client.get("/api/updates/check")
        assert resp.status_code == 200
        data = resp.json()
        assert "available" in data
        assert "up_to_date" in data
        assert "checked_at" in data

    def test_get_status(self, update_client):
        resp = update_client.get("/api/updates/status")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        db_names = [d["db_name"] for d in data]
        assert "clinvar" in db_names

    def test_get_history_empty(self, update_client):
        resp = update_client.get("/api/updates/history")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_prompts_empty(self, update_client):
        resp = update_client.get("/api/updates/prompts")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_dismiss_nonexistent_prompt(self, update_client):
        resp = update_client.post("/api/updates/prompts/999/dismiss")
        assert resp.status_code == 404

    def test_trigger_unsupported_db(self, update_client):
        resp = update_client.post("/api/updates/trigger", json={"db_name": "nonexistent_db"})
        assert resp.status_code == 400
        assert "not supported" in resp.json()["detail"].lower()

    def test_trigger_clinvar_update(self, update_client):
        with (
            patch(
                "backend.tasks.huey_tasks.run_database_update_task",
            ),
            patch(
                "backend.tasks.huey_tasks.create_database_update_job",
                return_value="test-job-id",
            ),
        ):
            resp = update_client.post("/api/updates/trigger", json={"db_name": "clinvar"})

        assert resp.status_code == 202
        data = resp.json()
        assert data["db_name"] == "clinvar"
        assert data["job_id"] == "test-job-id"

    def test_status_returns_enhanced_fields(self, update_client):
        """P4-17: status returns display_name, version_display, downloaded_at."""
        resp = update_client.get("/api/updates/status")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0

        first = data[0]
        assert "display_name" in first
        assert "version_display" in first
        assert "downloaded_at" in first
        assert "update_available" in first
        assert isinstance(first["update_available"], bool)

        # Check that display_name comes from database_registry
        clinvar = next((d for d in data if d["db_name"] == "clinvar"), None)
        assert clinvar is not None
        assert clinvar["display_name"] == "ClinVar"


# ═══════════════════════════════════════════════════════════════════════
# Version stamping tests (P4-17 / T4-18)
# ═══════════════════════════════════════════════════════════════════════


class TestVersionStamping:
    """T4-18: Version stamps correctly recorded after each database load/update."""

    def test_get_all_version_stamps_empty(self, reference_engine):
        stamps = get_all_version_stamps(reference_engine)
        assert stamps == []

    def test_get_all_version_stamps_after_record(self, reference_engine):
        _record_version(reference_engine, "clinvar", "20260315", 30_000_000)
        _record_version(reference_engine, "gnomad", "2.1.1", 2_000_000_000)

        stamps = get_all_version_stamps(reference_engine)
        assert len(stamps) == 2

        by_name = {s["db_name"]: s for s in stamps}
        assert by_name["clinvar"]["version"] == "20260315"
        assert by_name["clinvar"]["file_size_bytes"] == 30_000_000
        assert by_name["clinvar"]["downloaded_at"] is not None
        assert by_name["gnomad"]["version"] == "2.1.1"

    def test_version_stamp_updated_on_second_record(self, reference_engine):
        _record_version(reference_engine, "clinvar", "20260301", 29_000_000)
        _record_version(reference_engine, "clinvar", "20260315", 30_000_000)

        stamps = get_all_version_stamps(reference_engine)
        assert len(stamps) == 1
        assert stamps[0]["version"] == "20260315"
        assert stamps[0]["file_size_bytes"] == 30_000_000

    def test_format_version_display_clinvar_date(self):
        assert format_version_display("20260315", "clinvar") == "Mar 2026"
        assert format_version_display("20260101", "clinvar") == "Jan 2026"
        assert format_version_display("20251201", "clinvar") == "Dec 2025"

    def test_format_version_display_non_date(self):
        assert format_version_display("2.1.1", "gnomad") == "2.1.1"
        assert format_version_display("4.6a", "dbnsfp") == "4.6a"
        assert format_version_display("110", "vep_bundle") == "110"

    def test_format_version_display_none(self):
        assert format_version_display(None, "clinvar") is None

    def test_stamps_have_downloaded_at_as_iso(self, reference_engine):
        _record_version(reference_engine, "clinvar", "20260315", 1000)

        stamps = get_all_version_stamps(reference_engine)
        assert len(stamps) == 1
        # downloaded_at should be an ISO-format string
        dt_str = stamps[0]["downloaded_at"]
        assert dt_str is not None
        # Verify it parses
        from datetime import datetime

        datetime.fromisoformat(dt_str)
