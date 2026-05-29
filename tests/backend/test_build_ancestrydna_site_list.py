"""Tests for ``scripts/build_ancestrydna_site_list.py`` (Plan §0b).

Covers the four cases enumerated in the plan:

1. Two fixture exports with overlapping rsIDs produce a duplicate-free union.
2. Raw chrom codes ``23``/``25``/``26`` are passed through without this script
   applying any normalization of its own (the AncestryDNA parser performs the
   idempotent per-vendor collapse upstream — see the script's reconciliation
   note — so the emitted chrom is the canonical ``X``/``X``/``MT``; the union
   builder owns nothing extra here).
3. A malformed / empty input raises ``ParserError`` carrying the offending line.
4. The ``union_count ≥ 690_000`` floor assertion fires below the chip floor —
   exercised both as a unit check and end-to-end through ``main`` (non-zero
   exit, report still written).

Plus quality extras: report structure and byte-identical re-run determinism.

Fixtures are synthetic (fake rsIDs) built inline — no real, individually
identifiable export ever touches the test tree (Plan §0b PII rule).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "build_ancestrydna_site_list.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures"
MALFORMED_FIXTURE = FIXTURES / "sample_ancestrydna_malformed.txt"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("build_ancestrydna_site_list", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bda = _load_script_module()

# A minimal but dispatcher-detectable AncestryDNA v2.0 header: the
# ``#AncestryDNA`` signature + explicit array-version comment + 5-col header.
_ADNA_HEADER = (
    "#AncestryDNA raw data download\n"
    "# AncestryDNA array version: V2.0\n"
    "rsid\tchromosome\tposition\tallele1\tallele2\n"
)


def _write_adna(path: Path, rows: list[tuple[str, str, int, str, str]]) -> Path:
    """Write a synthetic AncestryDNA v2.0 export from ``(rsid,chrom,pos,a1,a2)``."""
    body = "".join(f"{rsid}\t{chrom}\t{pos}\t{a1}\t{a2}\n" for rsid, chrom, pos, a1, a2 in rows)
    path.write_text(_ADNA_HEADER + body, encoding="utf-8")
    return path


def _run(tmp_path: Path, inputs: list[Path]):
    """Drive the full ``run`` pipeline; return ``(report, union_rows)``."""
    return bda.run(
        inputs=inputs,
        output=tmp_path / "ancestrydna_v2_sites.tsv",
        report_json=tmp_path / "ancestrydna_v2_report.json",
    )


# --- Case 1: overlapping rsIDs union without duplicates ---------------------


def test_overlapping_exports_union_without_dupes(tmp_path):
    # Export A and B share rs2@1:200; the union keeps it once.
    export_a = _write_adna(
        tmp_path / "a.txt",
        [("rs1", "1", 100, "A", "G"), ("rs2", "1", 200, "C", "T")],
    )
    export_b = _write_adna(
        tmp_path / "b.txt",
        [("rs2", "1", 200, "C", "T"), ("rs3", "2", 300, "G", "G")],
    )
    report, union_rows = _run(tmp_path, [export_a, export_b])

    assert sorted(union_rows) == [
        ("rs1", "1", 100),
        ("rs2", "1", 200),
        ("rs3", "2", 300),
    ]
    assert report["union_count"] == 3  # rs2 collapsed, not double-counted
    assert report["input_count"] == 2
    # Output TSV agrees with the in-memory rows.
    out_lines = (tmp_path / "ancestrydna_v2_sites.tsv").read_text().splitlines()
    assert out_lines == ["rs1\t1\t100", "rs2\t1\t200", "rs3\t2\t300"]


# --- Case 2: raw chrom codes pass through (no normalization by THIS script) --


def test_raw_chrom_codes_not_renormalized_by_this_script(tmp_path):
    # Feed raw 23 / 25 / 26. The AncestryDNA parser collapses them (23->X,
    # 25->X PAR, 26->MT); build_ancestrydna_site_list must apply NOTHING further.
    export = _write_adna(
        tmp_path / "chroms.txt",
        [
            ("rsA", "23", 1000, "A", "A"),
            ("rsB", "25", 2000, "C", "C"),
            ("rsC", "26", 3000, "G", "G"),
            ("rsD", "7", 4000, "T", "T"),
        ],
    )
    union_rows, _ = bda.build_union([export])

    # The script is a pure union+sort of the parser's output: it transforms
    # nothing on its own. Equality with the parser's site set proves that.
    parsed_sites = bda.sites_from_result(bda.parse(export))
    assert set(union_rows) == parsed_sites

    # And the canonical chroms the parser produced survive verbatim.
    assert ("rsA", "X", 1000) in union_rows
    assert ("rsB", "X", 2000) in union_rows  # PAR (25) -> X
    assert ("rsC", "MT", 3000) in union_rows
    assert ("rsD", "7", 4000) in union_rows
    # No raw vendor codes leak into the per-vendor TSV.
    assert all(chrom not in {"23", "24", "25", "26"} for _, chrom, _ in union_rows)


# --- Case 3: malformed / empty input raises ParserError with offending line --


def test_malformed_input_raises_parser_error_with_offending_line():
    # The committed fixture's first bad data line has 4 columns; the parser
    # raises MalformedDataError (a ParserError) naming that line.
    with pytest.raises(bda.ParserError) as excinfo:
        bda.build_union([MALFORMED_FIXTURE])
    msg = str(excinfo.value)
    assert "expected 5 columns" in msg
    assert "Line" in msg  # the offending line number is reported


def test_empty_input_raises_parser_error(tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    # An empty file matches no vendor signature -> UnsupportedFormatError,
    # which is a ParserError subclass.
    with pytest.raises(bda.ParserError):
        bda.build_union([empty])


# --- Case 4: union floor (>= 690k) assertion ---------------------------------


def test_union_floor_check_fires_below_690k():
    # Uses the real production DEFAULT_MIN_UNION (690_000).
    assert bda.DEFAULT_MIN_UNION == 690_000
    assert bda.check_union_floor(5) == ["union_count 5 < 690000"]
    assert bda.check_union_floor(689_999) == ["union_count 689999 < 690000"]
    assert bda.check_union_floor(690_000) == []  # exactly at floor clears
    assert bda.check_union_floor(800_000) == []


def test_main_exits_nonzero_below_floor_but_still_writes_outputs(tmp_path):
    # End-to-end CLI: a 2-site export is far below 690k, so main() must
    # hard-fail (exit 1) yet still write the catalog + report for inspection.
    export = _write_adna(
        tmp_path / "tiny.txt",
        [("rs1", "1", 100, "A", "G"), ("rs2", "1", 200, "C", "T")],
    )
    out = tmp_path / "sites.tsv"
    report_json = tmp_path / "report.json"
    with pytest.raises(SystemExit) as excinfo:
        bda.main(
            [
                "--input",
                str(export),
                "--output",
                str(out),
                "--report-json",
                str(report_json),
            ]
        )
    assert excinfo.value.code == 1
    # Outputs written before the floor check, per run()'s contract.
    assert out.exists()
    report = json.loads(report_json.read_text())
    assert report["union_count"] == 2


# --- Quality extras: report shape + deterministic re-run --------------------


def test_report_structure_and_provenance(tmp_path):
    export = _write_adna(
        tmp_path / "rep.txt",
        [
            ("rs1", "1", 100, "A", "G"),
            ("kgp999", "2", 200, "C", "T"),
            ("rsX", "23", 300, "A", "A"),
        ],
    )
    report, _ = _run(tmp_path, [export])

    assert report["per_chrom_counts"] == {"1": 1, "2": 1, "X": 1}
    assert report["rsid_prefix_counts"]["rs"] == 2
    assert report["rsid_prefix_counts"]["kgp"] == 1
    assert len(report["sha256_output"]) == 64
    assert report["input_files"][0]["site_count"] == 3
    assert report["input_files"][0]["vendor"] == "ancestrydna"
    assert report["input_files"][0]["version"] == "v2.0"
    assert len(report["input_files"][0]["sha256"]) == 64
    # PII: inputs are named by ordinal label + SHA-256, never by file path.
    assert report["input_files"][0]["label"] == "input_1"
    assert "path" not in report["input_files"][0]
    # No genotype/row content or host file path leaks into the report (PII guard).
    assert "variants" not in report
    serialized = json.dumps(report)
    assert "\tA\tG" not in serialized
    assert str(tmp_path) not in serialized  # the export's host path never leaks


def test_rerun_is_byte_identical(tmp_path):
    rows = [
        ("rs3", "2", 300, "G", "G"),
        ("rs1", "1", 100, "A", "G"),
        ("rs2", "1", 200, "C", "T"),
    ]
    export = _write_adna(tmp_path / "det.txt", rows)

    first = tmp_path / "first.tsv"
    second = tmp_path / "second.tsv"
    bda.run(inputs=[export], output=first, report_json=tmp_path / "r1.json")
    bda.run(inputs=[export], output=second, report_json=tmp_path / "r2.json")

    assert first.read_bytes() == second.read_bytes()
    # Deterministic canonical sort: 1 before 2, ascending position.
    assert first.read_text().splitlines() == [
        "rs1\t1\t100",
        "rs2\t1\t200",
        "rs3\t2\t300",
    ]
