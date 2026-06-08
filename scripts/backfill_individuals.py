#!/usr/bin/env python3
"""Backfill suggestions for grouping samples into individuals (Plan §14.1).

Scans the reference ``samples`` table and each per-sample DB's
``sample_metadata`` row, and emits a JSON file listing candidate sample
pairs that may belong to the same individual.

This script never writes to any database. The user reviews the suggestions
JSON and decides whether to invoke ``POST /api/individuals`` +
``POST /api/individuals/{id}/link-sample`` (Plan §9.2, §9.3) for any
suggested pair.

Heuristics (Plan §14.1, IND-11):

- ``file_hash`` match — both samples carry the same SHA-256 of their raw
  export. Strongest signal: the same file was uploaded twice.
- ``name + date_collected`` near-match — display names match (case-
  insensitive) above a similarity threshold AND ``date_collected`` is
  exactly equal (or within a configurable window). Catches the common
  cross-vendor case (e.g. one person uploaded a 23andMe export and an
  AncestryDNA export with similar nicknames + same lab-draw date).

Pairs whose endpoints are already linked to the *same* individual are
skipped. Pairs whose endpoints are linked to *different* individuals are
kept and surfaced under ``conflict: true`` so the operator can decide
whether to relink — never auto-executed.

Usage::

    python scripts/backfill_individuals.py
    python scripts/backfill_individuals.py --data-dir ~/.yeliztli
    python scripts/backfill_individuals.py --output suggestions.json
    python scripts/backfill_individuals.py --name-threshold 0.90
    python scripts/backfill_individuals.py --date-window-days 7
    python scripts/backfill_individuals.py --include-linked   # don't skip
                                                              # same-individual
                                                              # pairs
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

import sqlalchemy as sa

# Allow ``python scripts/backfill_individuals.py …`` without an editable install.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.config import Settings  # noqa: E402
from backend.db.tables import sample_metadata_table, samples  # noqa: E402

DEFAULT_NAME_THRESHOLD: float = 0.85
DEFAULT_DATE_WINDOW_DAYS: int = 0


@dataclass(frozen=True)
class SampleRecord:
    """Aggregated view of one sample for matching purposes.

    Combines reference-DB columns (id, name, db_path, file_format, file_hash,
    individual_id) with the per-sample ``sample_metadata`` row's
    ``date_collected`` (which lives only in the per-sample DB).
    """

    id: int
    name: str
    db_path: str
    file_format: str | None
    file_hash: str | None
    individual_id: int | None
    date_collected: date | None


@dataclass
class Suggestion:
    """One candidate same-individual pair the operator should review."""

    sample_ids: tuple[int, int]
    reason: str  # "file_hash_match" | "name_date_match"
    confidence: str  # "high" | "medium"
    sample_a: dict
    sample_b: dict
    details: dict = field(default_factory=dict)
    conflict: bool = False


@dataclass
class Report:
    """Top-level suggestions output written to JSON."""

    generated_at: str
    data_dir: str
    sample_count: int
    suggestion_count: int
    name_threshold: float
    date_window_days: int
    suggestions: list[Suggestion]


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------


def _read_sample_records(settings: Settings) -> list[SampleRecord]:
    """Read the ``samples`` table + each per-sample ``sample_metadata`` row.

    Per-sample DBs that are missing on disk (e.g. a stale registry row
    pointing at a file the user deleted out-of-band) are still surfaced
    with ``date_collected=None`` so file_hash matches still fire.
    """
    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    try:
        with ref_engine.connect() as conn:
            rows = conn.execute(
                sa.select(
                    samples.c.id,
                    samples.c.name,
                    samples.c.db_path,
                    samples.c.file_format,
                    samples.c.file_hash,
                    samples.c.individual_id,
                ).order_by(samples.c.id)
            ).fetchall()
    finally:
        ref_engine.dispose()

    records: list[SampleRecord] = []
    for row in rows:
        date_collected = _read_date_collected(settings.data_dir / row.db_path)
        records.append(
            SampleRecord(
                id=row.id,
                name=row.name,
                db_path=row.db_path,
                file_format=row.file_format,
                file_hash=row.file_hash,
                individual_id=row.individual_id,
                date_collected=date_collected,
            )
        )
    return records


def _read_date_collected(sample_db_path: Path) -> date | None:
    """Return the per-sample ``sample_metadata.date_collected`` or None.

    Missing file, missing table, and NULL value all collapse to None — the
    backfill heuristic falls back to file_hash matching when date is
    unavailable.
    """
    if not sample_db_path.exists():
        return None
    engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    try:
        inspector = sa.inspect(engine)
        if "sample_metadata" not in inspector.get_table_names():
            return None
        with engine.connect() as conn:
            row = conn.execute(
                sa.select(sample_metadata_table.c.date_collected).where(
                    sample_metadata_table.c.id == 1
                )
            ).fetchone()
    finally:
        engine.dispose()
    if row is None or row[0] is None:
        return None
    value = row[0]
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _name_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _name_substring_match(a: str, b: str, *, min_length: int = 3) -> bool:
    """Whether the shorter name is a substring of the longer one.

    Catches the common cross-vendor naming pattern where one upload was
    saved as ``"Mom"`` and the next as ``"Mom 23andMe"``. ``min_length``
    blocks pathological cases like the literal name ``"a"`` matching every
    string in the registry.
    """
    a_norm = a.lower().strip()
    b_norm = b.lower().strip()
    shorter, longer = (a_norm, b_norm) if len(a_norm) <= len(b_norm) else (b_norm, a_norm)
    if len(shorter) < min_length:
        return False
    return shorter in longer


def _dates_match(
    a: date | None,
    b: date | None,
    *,
    window_days: int,
) -> bool:
    if a is None or b is None:
        return False
    delta = abs((a - b).days)
    return delta <= window_days


def _record_summary(record: SampleRecord) -> dict:
    """Public-safe summary embedded in the suggestion JSON.

    Carries identifier + provenance fields only — never variant rows. Safe
    for the operator to paste into a ticket or share with the bio-validator.
    """
    return {
        "id": record.id,
        "name": record.name,
        "db_path": record.db_path,
        "file_format": record.file_format,
        "file_hash": record.file_hash,
        "individual_id": record.individual_id,
        "date_collected": record.date_collected.isoformat() if record.date_collected else None,
    }


def _conflict(a: SampleRecord, b: SampleRecord) -> bool:
    """Both samples linked, but to *different* individuals — needs operator review."""
    return (
        a.individual_id is not None
        and b.individual_id is not None
        and a.individual_id != b.individual_id
    )


def _already_linked_together(a: SampleRecord, b: SampleRecord) -> bool:
    return (
        a.individual_id is not None
        and b.individual_id is not None
        and a.individual_id == b.individual_id
    )


def find_suggestions(
    records: list[SampleRecord],
    *,
    name_threshold: float = DEFAULT_NAME_THRESHOLD,
    date_window_days: int = DEFAULT_DATE_WINDOW_DAYS,
    include_linked: bool = False,
) -> list[Suggestion]:
    """Return the candidate-pair list (deterministic ordering by sample id).

    A pair appears at most once. ``file_hash`` matches take precedence over
    name+date matches when both heuristics fire on the same pair.
    """
    suggestions: list[Suggestion] = []
    seen_pairs: set[tuple[int, int]] = set()

    for i, a in enumerate(records):
        for b in records[i + 1 :]:
            if a.id == b.id:
                continue
            pair_key = (a.id, b.id) if a.id < b.id else (b.id, a.id)
            if pair_key in seen_pairs:
                continue
            if not include_linked and _already_linked_together(a, b):
                continue

            # file_hash exact match — strongest signal.
            if a.file_hash and b.file_hash and a.file_hash == b.file_hash:
                seen_pairs.add(pair_key)
                suggestions.append(
                    Suggestion(
                        sample_ids=pair_key,
                        reason="file_hash_match",
                        confidence="high",
                        sample_a=_record_summary(a),
                        sample_b=_record_summary(b),
                        details={"file_hash": a.file_hash},
                        conflict=_conflict(a, b),
                    )
                )
                continue

            # name + date heuristic. Two ways for names to "near-match":
            # (1) SequenceMatcher.ratio() >= threshold (catches typo-level
            #     drift like "Jane Doe v1" vs "Jane Doe v2"); (2) shorter
            #     name is a substring of the longer one (catches the
            #     cross-vendor case of "Mom" vs "Mom 23andMe").
            similarity = _name_similarity(a.name, b.name)
            substring = _name_substring_match(a.name, b.name)
            if similarity < name_threshold and not substring:
                continue
            if not _dates_match(
                a.date_collected,
                b.date_collected,
                window_days=date_window_days,
            ):
                continue

            seen_pairs.add(pair_key)
            suggestions.append(
                Suggestion(
                    sample_ids=pair_key,
                    reason="name_date_match",
                    confidence="medium",
                    sample_a=_record_summary(a),
                    sample_b=_record_summary(b),
                    details={
                        "name_similarity": round(similarity, 3),
                        "name_substring_match": substring,
                        "date_window_days": date_window_days,
                    },
                    conflict=_conflict(a, b),
                )
            )

    # Deterministic order: by (sample_id_low, sample_id_high).
    suggestions.sort(key=lambda s: s.sample_ids)
    return suggestions


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


def build_report(
    settings: Settings,
    *,
    name_threshold: float = DEFAULT_NAME_THRESHOLD,
    date_window_days: int = DEFAULT_DATE_WINDOW_DAYS,
    include_linked: bool = False,
) -> Report:
    records = _read_sample_records(settings)
    suggestions = find_suggestions(
        records,
        name_threshold=name_threshold,
        date_window_days=date_window_days,
        include_linked=include_linked,
    )
    return Report(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        data_dir=str(settings.data_dir),
        sample_count=len(records),
        suggestion_count=len(suggestions),
        name_threshold=name_threshold,
        date_window_days=date_window_days,
        suggestions=suggestions,
    )


def _report_to_json(report: Report) -> str:
    payload = asdict(report)
    # ``Suggestion.sample_ids`` is a tuple — JSON-serialise as a list.
    for suggestion in payload["suggestions"]:
        suggestion["sample_ids"] = list(suggestion["sample_ids"])
    return json.dumps(payload, indent=2, sort_keys=False)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Suggest candidate same-individual sample pairs (Plan §14.1, IND-11). "
            "Never writes to the database — emits a JSON suggestions file the "
            "operator reviews before invoking POST /api/individuals/{id}/link-sample."
        ),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help=(
            "Path to the Yeliztli data directory (defaults to the value "
            "Settings() resolves from config / environment)."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Where to write the suggestions JSON. Defaults to stdout. The path "
            "is created if its parent directory exists."
        ),
    )
    parser.add_argument(
        "--name-threshold",
        type=float,
        default=DEFAULT_NAME_THRESHOLD,
        help=(
            "Minimum difflib SequenceMatcher ratio between display names for "
            "the name+date heuristic to fire. Default "
            f"{DEFAULT_NAME_THRESHOLD}."
        ),
    )
    parser.add_argument(
        "--date-window-days",
        type=int,
        default=DEFAULT_DATE_WINDOW_DAYS,
        help=(
            "Maximum absolute difference in days between two samples' "
            "``date_collected`` values to count as a date match. Default "
            f"{DEFAULT_DATE_WINDOW_DAYS} (exact match only)."
        ),
    )
    parser.add_argument(
        "--include-linked",
        action="store_true",
        help=(
            "Also surface pairs already linked to the same individual. By "
            "default these are filtered as redundant."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if not (0.0 <= args.name_threshold <= 1.0):
        sys.stderr.write("error: --name-threshold must be in [0.0, 1.0]\n")
        return 2
    if args.date_window_days < 0:
        sys.stderr.write("error: --date-window-days must be >= 0\n")
        return 2

    settings = Settings(data_dir=args.data_dir) if args.data_dir else Settings()

    if not settings.reference_db_path.exists():
        sys.stderr.write(f"error: reference DB not found at {settings.reference_db_path}\n")
        return 1

    report = build_report(
        settings,
        name_threshold=args.name_threshold,
        date_window_days=args.date_window_days,
        include_linked=args.include_linked,
    )
    payload = _report_to_json(report)

    if args.output:
        args.output.write_text(payload + "\n")
        sys.stderr.write(f"wrote {report.suggestion_count} suggestion(s) to {args.output}\n")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
