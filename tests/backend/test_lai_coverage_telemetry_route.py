"""Unit tests for the Step-24 LAI coverage telemetry route surface.

The route lifts the runner's per-source telemetry (Plan §6.6) out of the
``lai_results.metadata_json`` blob and exposes it as a typed
``coverage_telemetry`` field on ``LAIResultResponse`` so ``AncestryView``
can render the "X of Y rsIDs mapped" summary and the merged-sample
breakdown table (Plan §6.7).

These tests pin the helper's behavior without spinning up the full
FastAPI app — that surface is covered by the broader ancestry e2e
suite. The helper-level coverage here is the contract `AncestryView`
relies on.
"""

from __future__ import annotations

import pytest

from backend.api.routes.ancestry import (
    LAICoverageSourceTelemetry,
    LAICoverageTelemetry,
    LAIResultResponse,
    _parse_coverage_telemetry,
)

# ── _parse_coverage_telemetry ────────────────────────────────────────


class TestParseCoverageTelemetry:
    """`_parse_coverage_telemetry` round-trips Step-22 runner telemetry."""

    def test_single_source_ancestrydna_payload(self) -> None:
        """Unmerged AncestryDNA → single-bucket payload + computed totals."""
        metadata = {
            "coverage_telemetry": {"ancestrydna": {"hits": 480, "drops": 20}},
            "drop_rate": 0.04,
            "drop_rate_warning": False,
        }
        result = _parse_coverage_telemetry(metadata)
        assert result is not None
        assert set(result.per_source.keys()) == {"ancestrydna"}
        assert result.per_source["ancestrydna"] == LAICoverageSourceTelemetry(hits=480, drops=20)
        assert result.total_hits == 480
        assert result.total_drops == 20
        assert result.drop_rate == pytest.approx(0.04)
        assert result.drop_rate_warning is False

    def test_single_source_23andme_payload(self) -> None:
        """23andMe payload: no leakage of merged-sample keys."""
        metadata = {
            "coverage_telemetry": {"23andme": {"hits": 600, "drops": 5}},
            "drop_rate": 0.0083,
            "drop_rate_warning": False,
        }
        result = _parse_coverage_telemetry(metadata)
        assert result is not None
        assert list(result.per_source.keys()) == ["23andme"]

    def test_merged_sample_three_key_payload(self) -> None:
        """Merged sample three-key uppercase telemetry passes through verbatim."""
        metadata = {
            "coverage_telemetry": {
                "S1": {"hits": 400, "drops": 30},
                "S2": {"hits": 350, "drops": 20},
                "both": {"hits": 200, "drops": 0},
            },
            "drop_rate": 0.05,
            "drop_rate_warning": False,
        }
        result = _parse_coverage_telemetry(metadata)
        assert result is not None
        assert set(result.per_source.keys()) == {"S1", "S2", "both"}
        assert result.total_hits == 950
        assert result.total_drops == 50
        assert result.drop_rate == pytest.approx(0.05)

    def test_high_dropout_flag_round_trips(self) -> None:
        """`drop_rate_warning` is preserved when explicitly set."""
        metadata = {
            "coverage_telemetry": {"ancestrydna": {"hits": 300, "drops": 200}},
            "drop_rate": 0.4,
            "drop_rate_warning": True,
        }
        result = _parse_coverage_telemetry(metadata)
        assert result is not None
        assert result.drop_rate_warning is True
        assert result.drop_rate == pytest.approx(0.4)

    def test_missing_coverage_telemetry_returns_none(self) -> None:
        """Pre-Step-22 LAI runs lack the telemetry key — surface is omitted."""
        metadata = {"runtime_seconds": 600, "populations": ["EUR", "AFR"]}
        assert _parse_coverage_telemetry(metadata) is None

    def test_empty_coverage_telemetry_returns_none(self) -> None:
        """Empty dict → None so the frontend skips the section entirely."""
        metadata = {"coverage_telemetry": {}}
        assert _parse_coverage_telemetry(metadata) is None

    def test_non_dict_coverage_telemetry_returns_none(self) -> None:
        """Defensive: garbage in `metadata.coverage_telemetry` never raises."""
        for value in ["not-a-dict", 42, None, ["a", "b"]]:
            metadata = {"coverage_telemetry": value}
            assert _parse_coverage_telemetry(metadata) is None

    def test_drop_rate_warning_derived_when_metadata_omits_flag(self) -> None:
        """Threshold-derived `drop_rate_warning` when key is missing."""
        # 200/500 = 40% drop rate; > 15% threshold → derived True
        metadata = {
            "coverage_telemetry": {"ancestrydna": {"hits": 300, "drops": 200}},
        }
        result = _parse_coverage_telemetry(metadata)
        assert result is not None
        assert result.drop_rate_warning is True

    def test_drop_rate_derived_from_counts_when_missing(self) -> None:
        """`drop_rate` computed from buckets when not in metadata."""
        metadata = {
            "coverage_telemetry": {"ancestrydna": {"hits": 400, "drops": 100}},
        }
        result = _parse_coverage_telemetry(metadata)
        assert result is not None
        assert result.drop_rate == pytest.approx(0.2)

    def test_handles_missing_hits_or_drops_keys(self) -> None:
        """Missing `hits` / `drops` default to 0 — never raise."""
        metadata = {
            "coverage_telemetry": {
                "ancestrydna": {"hits": 500},
                "23andme": {"drops": 3},
            },
        }
        result = _parse_coverage_telemetry(metadata)
        assert result is not None
        assert result.per_source["ancestrydna"].drops == 0
        assert result.per_source["23andme"].hits == 0

    def test_skips_non_dict_buckets(self) -> None:
        """A malformed inner value is skipped, not raised."""
        metadata = {
            "coverage_telemetry": {
                "ancestrydna": {"hits": 100, "drops": 5},
                "garbage": "not a dict",
            },
        }
        result = _parse_coverage_telemetry(metadata)
        assert result is not None
        assert set(result.per_source.keys()) == {"ancestrydna"}


# ── LAIResultResponse ─────────────────────────────────────────────────


class TestLAIResultResponseShape:
    """The pydantic shape contract `AncestryView` reads from."""

    def test_telemetry_is_optional_on_the_response(self) -> None:
        """Pre-Step-24 callers (and pre-Step-22 runs) parse cleanly."""
        response = LAIResultResponse(
            global_ancestry={},
            chromosome_painting={},
            metadata={},
            created_at="2025-01-01T00:00:00",
        )
        assert response.coverage_telemetry is None

    def test_telemetry_payload_passes_through(self) -> None:
        """Constructing with telemetry keeps the typed bucket structure."""
        telemetry = LAICoverageTelemetry(
            per_source={"ancestrydna": LAICoverageSourceTelemetry(hits=480, drops=20)},
            total_hits=480,
            total_drops=20,
            drop_rate=0.04,
            drop_rate_warning=False,
        )
        response = LAIResultResponse(
            global_ancestry={},
            chromosome_painting={},
            metadata={},
            created_at="2025-01-01T00:00:00",
            coverage_telemetry=telemetry,
        )
        assert response.coverage_telemetry is not None
        assert response.coverage_telemetry.per_source["ancestrydna"].hits == 480
