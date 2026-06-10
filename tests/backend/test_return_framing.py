"""Tests for the shared responsible-return framing (SW-A1)."""

from __future__ import annotations

from backend.analysis.return_framing import (
    CLIA_CONFIRMATION,
    prs_ci_label,
    prs_return_framing,
    prs_source_population_label,
)


class TestCliaConfirmation:
    def test_text_is_confirm_in_clia_and_counseling(self) -> None:
        text = CLIA_CONFIRMATION.lower()
        assert "clia" in text
        assert "not a clinical diagnosis" in text
        assert "genetic counselor" in text


class TestSourcePopulationLabel:
    def test_names_the_population(self) -> None:
        assert "EUR" in prs_source_population_label("EUR")

    def test_handles_missing_population(self) -> None:
        assert "unspecified" in prs_source_population_label(None)


class TestCiLabel:
    def test_paired_ci(self) -> None:
        assert prs_ci_label(55.2, 74.8) == "95% CI 55–75th percentile"

    def test_ci_always_stated_when_unavailable(self) -> None:
        assert "unavailable" in prs_ci_label(None, None)
        assert "unavailable" in prs_ci_label(50.0, None)


class TestPrsReturnFraming:
    def test_block_pairs_research_source_and_ci(self) -> None:
        block = prs_return_framing(
            {"source_ancestry": "EAS", "bootstrap_ci_lower": 40.0, "bootstrap_ci_upper": 60.0}
        )
        assert block["research_use_only"] is True
        assert block["source_population"] == "EAS"
        assert "EAS" in block["source_population_label"]
        assert block["ci_label"] == "95% CI 40–60th percentile"

    def test_block_states_ci_unavailable_when_missing(self) -> None:
        block = prs_return_framing({"source_ancestry": "EUR"})
        assert "unavailable" in block["ci_label"]
