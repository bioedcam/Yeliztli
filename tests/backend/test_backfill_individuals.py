"""Tests for ``scripts/backfill_individuals.py`` (Step 58 / IND-11; Plan §14.1).

Locks the contract that the script only *suggests* candidate same-individual
sample pairs — it never writes to ``samples``, ``individuals``, or any
per-sample DB. Two heuristics are exercised:

1. ``file_hash_match`` — identical SHA-256 across two ``samples`` rows
   (high confidence; the same raw export uploaded twice).
2. ``name_date_match`` — display-name similarity above the threshold AND
   ``date_collected`` within the configured window (medium confidence;
   the common cross-vendor case where the same person uploaded both a
   23andMe and an AncestryDNA file).

Already-linked pairs to the *same* individual are filtered by default.
Linked-to-*different*-individuals pairs are surfaced with ``conflict: true``
so the operator can decide whether to relink — the script never makes the
call.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa

import scripts.backfill_individuals as backfill
from backend.config import Settings
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    individuals,
    reference_metadata,
    sample_metadata_table,
    samples,
)
from scripts.backfill_individuals import (
    DEFAULT_DATE_WINDOW_DAYS,
    DEFAULT_NAME_THRESHOLD,
    SampleRecord,
    build_report,
    find_suggestions,
    main,
)

# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _make_record(
    sample_id: int,
    *,
    name: str = "Sample",
    file_format: str | None = "23andme_v5",
    file_hash: str | None = None,
    individual_id: int | None = None,
    date_collected: date | None = None,
    db_path: str | None = None,
) -> SampleRecord:
    return SampleRecord(
        id=sample_id,
        name=name,
        db_path=db_path or f"samples/sample_{sample_id}.db",
        file_format=file_format,
        file_hash=file_hash,
        individual_id=individual_id,
        date_collected=date_collected,
    )


def _seed_data_dir(
    tmp_data_dir: Path,
    sample_specs: list[dict],
    *,
    individuals_seed: list[dict] | None = None,
) -> Settings:
    """Materialise reference.db + per-sample DBs in *tmp_data_dir*.

    Each entry in *sample_specs* drives one ``samples`` row + one matching
    per-sample SQLite file under ``data_dir/samples/sample_{id}.db``.
    """
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)
    try:
        with ref_engine.begin() as conn:
            if individuals_seed:
                conn.execute(individuals.insert(), individuals_seed)
            for spec in sample_specs:
                conn.execute(
                    samples.insert().values(
                        id=spec["id"],
                        name=spec["name"],
                        db_path=spec.get("db_path", f"samples/sample_{spec['id']}.db"),
                        file_format=spec.get("file_format", "23andme_v5"),
                        file_hash=spec.get("file_hash"),
                        individual_id=spec.get("individual_id"),
                    )
                )
    finally:
        ref_engine.dispose()

    for spec in sample_specs:
        if spec.get("skip_sample_db"):
            continue
        sample_db_path = tmp_data_dir / spec.get("db_path", f"samples/sample_{spec['id']}.db")
        sample_db_path.parent.mkdir(parents=True, exist_ok=True)
        engine = sa.create_engine(f"sqlite:///{sample_db_path}")
        try:
            create_sample_tables(engine)
            with engine.begin() as conn:
                values: dict = {
                    "id": 1,
                    "name": spec["name"],
                    "file_format": spec.get("file_format", "23andme_v5"),
                    "file_hash": spec.get("file_hash"),
                }
                if spec.get("date_collected") is not None:
                    values["date_collected"] = spec["date_collected"]
                conn.execute(sample_metadata_table.insert().values(**values))
        finally:
            engine.dispose()

    return settings


def _registry_snapshot(settings: Settings) -> dict:
    """Snapshot the rows that the backfill script must NOT modify."""
    engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    try:
        with engine.connect() as conn:
            sample_rows = sorted(tuple(row) for row in conn.execute(sa.select(samples)).fetchall())
            individual_rows = sorted(
                tuple(row) for row in conn.execute(sa.select(individuals)).fetchall()
            )
    finally:
        engine.dispose()
    return {"samples": sample_rows, "individuals": individual_rows}


# ═══════════════════════════════════════════════════════════════════════════
# find_suggestions unit tests
# ═══════════════════════════════════════════════════════════════════════════


class TestFindSuggestionsFileHash:
    """file_hash heuristic — the strongest signal."""

    def test_identical_hash_yields_high_confidence_suggestion(self):
        records = [
            _make_record(1, name="Mom", file_hash="abc123"),
            _make_record(2, name="Mom 2", file_hash="abc123"),
        ]
        suggestions = find_suggestions(records)
        assert len(suggestions) == 1
        suggestion = suggestions[0]
        assert suggestion.sample_ids == (1, 2)
        assert suggestion.reason == "file_hash_match"
        assert suggestion.confidence == "high"
        assert suggestion.details == {"file_hash": "abc123"}
        assert suggestion.conflict is False

    def test_distinct_hashes_yield_no_hash_match(self):
        records = [
            _make_record(1, name="x", file_hash="aaa"),
            _make_record(2, name="y", file_hash="bbb"),
        ]
        assert find_suggestions(records) == []

    def test_null_hashes_never_match(self):
        records = [
            _make_record(1, name="x", file_hash=None),
            _make_record(2, name="y", file_hash=None),
        ]
        assert find_suggestions(records) == []

    def test_empty_string_hashes_never_match(self):
        """Empty-string file_hash is treated like NULL (no signal)."""
        records = [
            _make_record(1, name="x", file_hash=""),
            _make_record(2, name="y", file_hash=""),
        ]
        assert find_suggestions(records) == []


class TestFindSuggestionsNameDate:
    """name + date_collected heuristic — the cross-vendor case."""

    def test_similar_name_and_same_date_yields_medium_confidence(self):
        records = [
            _make_record(
                1,
                name="Jane Doe v1",
                file_format="23andme_v5",
                file_hash="hash_a",
                date_collected=date(2024, 6, 1),
            ),
            _make_record(
                2,
                name="Jane Doe v2",
                file_format="ancestrydna_v2.0",
                file_hash="hash_b",
                date_collected=date(2024, 6, 1),
            ),
        ]
        # "Jane Doe v1" vs "Jane Doe v2" → SequenceMatcher.ratio() ≈ 0.91,
        # well above the default 0.85 threshold.
        suggestions = find_suggestions(records)
        assert len(suggestions) == 1
        suggestion = suggestions[0]
        assert suggestion.reason == "name_date_match"
        assert suggestion.confidence == "medium"
        assert suggestion.sample_ids == (1, 2)
        assert "name_similarity" in suggestion.details
        assert suggestion.details["date_window_days"] == 0

    def test_substring_name_match_catches_cross_vendor_pattern(self):
        """The realistic case: ``"Mom"`` and ``"Mom 23andMe"`` should pair.

        SequenceMatcher gives them only ~0.43 — below any sane threshold —
        but the substring fallback fires because the shorter name is
        entirely contained in the longer one.
        """
        records = [
            _make_record(
                1,
                name="Mom",
                file_format="23andme_v5",
                file_hash="hash_a",
                date_collected=date(2024, 6, 1),
            ),
            _make_record(
                2,
                name="Mom AncestryDNA",
                file_format="ancestrydna_v2.0",
                file_hash="hash_b",
                date_collected=date(2024, 6, 1),
            ),
        ]
        suggestions = find_suggestions(records)
        assert len(suggestions) == 1
        suggestion = suggestions[0]
        assert suggestion.reason == "name_date_match"
        assert suggestion.details["name_substring_match"] is True

    def test_short_substring_does_not_match(self):
        """Two-character substring is too weak to fire the fallback."""
        records = [
            _make_record(1, name="A", file_hash="aa", date_collected=date(2024, 6, 1)),
            _make_record(
                2,
                name="A long full name",
                file_hash="bb",
                date_collected=date(2024, 6, 1),
            ),
        ]
        assert find_suggestions(records) == []

    def test_dissimilar_names_yield_no_match(self):
        records = [
            _make_record(1, name="Mom", date_collected=date(2024, 6, 1), file_hash="aa"),
            _make_record(
                2,
                name="Completely Different",
                date_collected=date(2024, 6, 1),
                file_hash="bb",
            ),
        ]
        assert find_suggestions(records, name_threshold=0.85) == []

    def test_mismatched_dates_yield_no_match(self):
        records = [
            _make_record(1, name="Mom", date_collected=date(2024, 6, 1), file_hash="aa"),
            _make_record(2, name="Mom", date_collected=date(2024, 7, 1), file_hash="bb"),
        ]
        assert find_suggestions(records) == []

    def test_dates_within_window_yield_match(self):
        records = [
            _make_record(1, name="Mom", date_collected=date(2024, 6, 1), file_hash="aa"),
            _make_record(2, name="Mom", date_collected=date(2024, 6, 5), file_hash="bb"),
        ]
        suggestions = find_suggestions(records, date_window_days=7)
        assert len(suggestions) == 1
        assert suggestions[0].reason == "name_date_match"

    def test_missing_date_blocks_name_match(self):
        records = [
            _make_record(1, name="Mom", date_collected=None, file_hash="aa"),
            _make_record(2, name="Mom", date_collected=date(2024, 6, 1), file_hash="bb"),
        ]
        assert find_suggestions(records) == []

    def test_name_threshold_threshold_inclusive(self):
        """Pairs at exactly the threshold are accepted."""
        records = [
            _make_record(1, name="abcdef", date_collected=date(2024, 1, 1), file_hash="aa"),
            _make_record(2, name="abcdef", date_collected=date(2024, 1, 1), file_hash="bb"),
        ]
        # Identical name → ratio == 1.0, well above threshold.
        suggestions = find_suggestions(records, name_threshold=1.0)
        assert len(suggestions) == 1


class TestFindSuggestionsLinkedFiltering:
    """Already-linked pairs and conflicts."""

    def test_same_individual_pair_filtered_by_default(self):
        records = [
            _make_record(1, name="Mom", file_hash="aa", individual_id=7),
            _make_record(2, name="Mom 2", file_hash="aa", individual_id=7),
        ]
        assert find_suggestions(records) == []

    def test_same_individual_pair_surfaced_when_include_linked(self):
        records = [
            _make_record(1, name="Mom", file_hash="aa", individual_id=7),
            _make_record(2, name="Mom 2", file_hash="aa", individual_id=7),
        ]
        suggestions = find_suggestions(records, include_linked=True)
        assert len(suggestions) == 1
        assert suggestions[0].conflict is False

    def test_different_individuals_pair_flagged_as_conflict(self):
        records = [
            _make_record(1, name="Mom", file_hash="aa", individual_id=7),
            _make_record(2, name="Mom 2", file_hash="aa", individual_id=8),
        ]
        suggestions = find_suggestions(records)
        assert len(suggestions) == 1
        assert suggestions[0].conflict is True

    def test_only_one_endpoint_linked_is_not_conflict(self):
        records = [
            _make_record(1, name="Mom", file_hash="aa", individual_id=7),
            _make_record(2, name="Mom 2", file_hash="aa", individual_id=None),
        ]
        suggestions = find_suggestions(records)
        assert len(suggestions) == 1
        assert suggestions[0].conflict is False


class TestFindSuggestionsOrderingAndDedup:
    def test_file_hash_match_takes_precedence_over_name_date(self):
        """A pair matched by both heuristics is reported once as file_hash."""
        records = [
            _make_record(
                1,
                name="Jane",
                file_hash="aaa",
                date_collected=date(2024, 1, 1),
            ),
            _make_record(
                2,
                name="Jane",
                file_hash="aaa",
                date_collected=date(2024, 1, 1),
            ),
        ]
        suggestions = find_suggestions(records)
        assert len(suggestions) == 1
        assert suggestions[0].reason == "file_hash_match"

    def test_deterministic_ordering_by_sample_id(self):
        records = [
            _make_record(
                3,
                name="A",
                file_hash="hash3",
                date_collected=date(2024, 1, 1),
            ),
            _make_record(
                1,
                name="A",
                file_hash="hash1",
                date_collected=date(2024, 1, 1),
            ),
            _make_record(
                2,
                name="A",
                file_hash="hash2",
                date_collected=date(2024, 1, 1),
            ),
        ]
        suggestions = find_suggestions(records)
        # Three records all pairwise name+date matching → 3 pairs in id order.
        assert [s.sample_ids for s in suggestions] == [(1, 2), (1, 3), (2, 3)]

    def test_each_pair_reported_at_most_once(self):
        records = [
            _make_record(
                1,
                name="Mom",
                file_hash="aaa",
                date_collected=date(2024, 1, 1),
            ),
            _make_record(
                2,
                name="Mom",
                file_hash="aaa",
                date_collected=date(2024, 1, 1),
            ),
        ]
        suggestions = find_suggestions(records)
        assert len(suggestions) == 1


# ═══════════════════════════════════════════════════════════════════════════
# build_report end-to-end (reads reference.db + per-sample DBs)
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildReport:
    """The script reads dates from per-sample DBs and never writes back."""

    def test_picks_up_date_collected_from_per_sample_db(self, tmp_data_dir: Path):
        settings = _seed_data_dir(
            tmp_data_dir,
            [
                {
                    "id": 1,
                    "name": "Mom 23andMe",
                    "file_format": "23andme_v5",
                    "file_hash": "hash_a",
                    "date_collected": date(2024, 6, 1),
                },
                {
                    "id": 2,
                    "name": "Mom AncestryDNA",
                    "file_format": "ancestrydna_v2.0",
                    "file_hash": "hash_b",
                    "date_collected": date(2024, 6, 1),
                },
            ],
        )
        report = build_report(settings, name_threshold=0.5)
        assert report.sample_count == 2
        assert report.suggestion_count == 1
        suggestion = report.suggestions[0]
        assert suggestion.reason == "name_date_match"
        assert suggestion.sample_a["date_collected"] == "2024-06-01"
        assert suggestion.sample_b["date_collected"] == "2024-06-01"

    def test_missing_per_sample_db_does_not_block_file_hash_match(self, tmp_data_dir: Path):
        """If a per-sample DB is missing on disk, file_hash matching still works."""
        settings = _seed_data_dir(
            tmp_data_dir,
            [
                {
                    "id": 1,
                    "name": "Sample 1",
                    "file_hash": "shared",
                    "date_collected": date(2024, 6, 1),
                },
                {
                    "id": 2,
                    "name": "Sample 2",
                    "file_hash": "shared",
                    "skip_sample_db": True,
                },
            ],
        )
        report = build_report(settings)
        assert report.suggestion_count == 1
        assert report.suggestions[0].reason == "file_hash_match"
        # The missing per-sample DB surfaces with date_collected = None.
        # Find the sample-2 endpoint and confirm.
        endpoints = (
            report.suggestions[0].sample_a,
            report.suggestions[0].sample_b,
        )
        missing = next(e for e in endpoints if e["id"] == 2)
        assert missing["date_collected"] is None

    def test_script_does_not_modify_reference_db(self, tmp_data_dir: Path):
        """Running build_report must NOT write to samples or individuals."""
        settings = _seed_data_dir(
            tmp_data_dir,
            [
                {
                    "id": 1,
                    "name": "Mom",
                    "file_hash": "aaa",
                    "date_collected": date(2024, 6, 1),
                },
                {
                    "id": 2,
                    "name": "Mom",
                    "file_hash": "aaa",
                    "date_collected": date(2024, 6, 1),
                },
            ],
            individuals_seed=[
                {"id": 5, "display_name": "preexisting"},
            ],
        )
        before = _registry_snapshot(settings)
        build_report(settings)
        after = _registry_snapshot(settings)
        assert before == after, "backfill script must never mutate the registry"

    def test_no_samples_yields_empty_report(self, tmp_data_dir: Path):
        settings = _seed_data_dir(tmp_data_dir, [])
        report = build_report(settings)
        assert report.sample_count == 0
        assert report.suggestion_count == 0
        assert report.suggestions == []

    def test_metadata_table_present_with_null_date_handled(self, tmp_data_dir: Path):
        """A NULL date_collected blocks name+date matching without crashing."""
        settings = _seed_data_dir(
            tmp_data_dir,
            [
                {"id": 1, "name": "Mom", "file_hash": "aa"},
                {"id": 2, "name": "Mom", "file_hash": "bb"},
            ],
        )
        report = build_report(settings)
        # No file_hash match (distinct hashes); no name+date match (no dates).
        assert report.suggestion_count == 0


# ═══════════════════════════════════════════════════════════════════════════
# CLI (main())
# ═══════════════════════════════════════════════════════════════════════════


class TestMainCLI:
    def test_writes_json_when_output_path_given(
        self, tmp_data_dir: Path, capsys: pytest.CaptureFixture
    ):
        _seed_data_dir(
            tmp_data_dir,
            [
                {
                    "id": 1,
                    "name": "Mom",
                    "file_hash": "aaa",
                    "date_collected": date(2024, 6, 1),
                },
                {
                    "id": 2,
                    "name": "Mom",
                    "file_hash": "aaa",
                    "date_collected": date(2024, 6, 1),
                },
            ],
        )
        out_path = tmp_data_dir / "suggestions.json"
        exit_code = main(
            [
                "--data-dir",
                str(tmp_data_dir),
                "--output",
                str(out_path),
            ]
        )
        assert exit_code == 0
        assert out_path.exists()
        payload = json.loads(out_path.read_text())
        assert payload["suggestion_count"] == 1
        suggestion = payload["suggestions"][0]
        assert suggestion["sample_ids"] == [1, 2]
        assert suggestion["reason"] == "file_hash_match"

    def test_prints_json_to_stdout_when_no_output_path(
        self, tmp_data_dir: Path, capsys: pytest.CaptureFixture
    ):
        _seed_data_dir(
            tmp_data_dir,
            [
                {"id": 1, "name": "x", "file_hash": "aa"},
            ],
        )
        exit_code = main(["--data-dir", str(tmp_data_dir)])
        assert exit_code == 0
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["sample_count"] == 1
        assert payload["suggestion_count"] == 0

    def test_missing_reference_db_returns_exit_1(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ):
        # tmp_path with no reference.db
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        exit_code = main(["--data-dir", str(empty_dir)])
        assert exit_code == 1
        assert "reference DB not found" in capsys.readouterr().err

    def test_invalid_name_threshold_returns_exit_2(
        self, tmp_data_dir: Path, capsys: pytest.CaptureFixture
    ):
        exit_code = main(
            [
                "--data-dir",
                str(tmp_data_dir),
                "--name-threshold",
                "1.5",
            ]
        )
        assert exit_code == 2
        assert "--name-threshold" in capsys.readouterr().err

    def test_negative_date_window_returns_exit_2(
        self, tmp_data_dir: Path, capsys: pytest.CaptureFixture
    ):
        exit_code = main(
            [
                "--data-dir",
                str(tmp_data_dir),
                "--date-window-days",
                "-1",
            ]
        )
        assert exit_code == 2
        assert "--date-window-days" in capsys.readouterr().err

    def test_include_linked_flag_surfaces_same_individual_pair(self, tmp_data_dir: Path):
        _seed_data_dir(
            tmp_data_dir,
            [
                {
                    "id": 1,
                    "name": "Mom",
                    "file_hash": "aa",
                    "individual_id": 1,
                },
                {
                    "id": 2,
                    "name": "Mom 2",
                    "file_hash": "aa",
                    "individual_id": 1,
                },
            ],
            individuals_seed=[{"id": 1, "display_name": "Mom"}],
        )
        out_path = tmp_data_dir / "with_linked.json"
        exit_code = main(
            [
                "--data-dir",
                str(tmp_data_dir),
                "--output",
                str(out_path),
                "--include-linked",
            ]
        )
        assert exit_code == 0
        payload = json.loads(out_path.read_text())
        assert payload["suggestion_count"] == 1

        # And without --include-linked the same setup yields zero.
        out_path_filtered = tmp_data_dir / "filtered.json"
        main(
            [
                "--data-dir",
                str(tmp_data_dir),
                "--output",
                str(out_path_filtered),
            ]
        )
        assert json.loads(out_path_filtered.read_text())["suggestion_count"] == 0


class TestModuleDefaults:
    """Lock the documented CLI defaults so they don't drift."""

    def test_default_thresholds_match_documented_values(self):
        assert DEFAULT_NAME_THRESHOLD == 0.85
        assert DEFAULT_DATE_WINDOW_DAYS == 0


class TestScriptInvocation:
    """Smoke test: invoking the script directly via ``python -m`` works."""

    def test_script_runs_as_module(self, tmp_data_dir: Path):
        _seed_data_dir(
            tmp_data_dir,
            [{"id": 1, "name": "x", "file_hash": "aa"}],
        )
        result = subprocess.run(
            [
                sys.executable,
                str(Path(backfill.__file__).resolve()),
                "--data-dir",
                str(tmp_data_dir),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["sample_count"] == 1
