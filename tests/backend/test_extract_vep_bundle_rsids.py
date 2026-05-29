"""Tests for ``scripts/extract_vep_bundle_rsids.py`` (Plan §0g).

Covers the four cases enumerated in the plan:

1. A fixture SQLite at the expected schema produces the expected TSV + report.
2. A bundle whose ``bundle_metadata.bundle_version = "v2.0.0"`` is rejected.
3. An empty ``vep_annotations`` table raises a clear error.
4. The sort order is deterministic (run twice on one fixture → byte-identical).

Plus quality extras: chromosome ordering is numeric/version (not SQLite lex),
``SELECT DISTINCT`` dedup, NULL/empty rsIDs skipped, a missing ``bundle_metadata``
table raises a clear error, the post-extraction floor checks fire, and ``main``
exits non-zero (yet still writes outputs) below the row floor.

Fixtures are synthetic SQLite bundles built inline at the canonical schema — no
real bundle asset is required to exercise the logic.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "extract_vep_bundle_rsids.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("extract_vep_bundle_rsids", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evr = _load_script_module()


def _make_bundle(
    path: Path,
    rows: list[tuple[str | None, str, int]],
    *,
    version: str | None = "v1.0.0",
    create_meta: bool = True,
) -> Path:
    """Write a synthetic VEP bundle at the canonical schema.

    ``rows`` are ``(rsid, chrom, pos)`` triples inserted into ``vep_annotations``
    (the remaining columns stay NULL). When ``create_meta`` is False the
    ``bundle_metadata`` table is omitted entirely (simulates a corrupt bundle);
    when ``version`` is None the table exists but carries no ``bundle_version``.
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE vep_annotations ("
            "rsid TEXT, chrom TEXT, pos INTEGER, ref TEXT, alt TEXT, "
            "gene_symbol TEXT, transcript_id TEXT, consequence TEXT, "
            "hgvs_coding TEXT, hgvs_protein TEXT, strand TEXT, "
            "exon_number INTEGER, intron_number INTEGER, mane_select INTEGER)"
        )
        conn.executemany(
            "INSERT INTO vep_annotations (rsid, chrom, pos) VALUES (?, ?, ?)",
            rows,
        )
        if create_meta:
            conn.execute("CREATE TABLE bundle_metadata (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO bundle_metadata (key, value) VALUES ('schema_version', '1')")
            if version is not None:
                conn.execute(
                    "INSERT INTO bundle_metadata (key, value) VALUES ('bundle_version', ?)",
                    (version,),
                )
        conn.commit()
    finally:
        conn.close()
    return path


def _run(tmp_path: Path, bundle: Path):
    return evr.run(
        vep_bundle=bundle,
        output=tmp_path / "twentythreeandme_v5_sites.tsv",
        report_json=tmp_path / "twentythreeandme_v5_report.json",
    )


# --- Case 1: canonical fixture → expected TSV + report ----------------------


def test_fixture_produces_expected_tsv_and_report(tmp_path):
    bundle = _make_bundle(
        tmp_path / "v1.db",
        [
            ("rs3", "2", 300),
            ("rs1", "1", 100),
            ("rs2", "1", 200),
            ("rsX", "X", 50),
            ("rsMT", "MT", 70),
        ],
    )
    report, rows = _run(tmp_path, bundle)

    # Canonical order: chrom 1 (asc pos) → 2 → X → MT.
    assert rows == [
        ("rs1", "1", 100),
        ("rs2", "1", 200),
        ("rs3", "2", 300),
        ("rsX", "X", 50),
        ("rsMT", "MT", 70),
    ]
    out_lines = (tmp_path / "twentythreeandme_v5_sites.tsv").read_text().splitlines()
    assert out_lines == [
        "rs1\t1\t100",
        "rs2\t1\t200",
        "rs3\t2\t300",
        "rsX\tX\t50",
        "rsMT\tMT\t70",
    ]
    assert report["row_count"] == 5
    assert report["source_bundle_version"] == "v1.0.0"
    assert report["per_chrom_counts"] == {"1": 2, "2": 1, "X": 1, "MT": 1}
    assert len(report["source_bundle_sha256"]) == 64
    assert len(report["output_sha256"]) == 64
    assert "git_commit" in report and "build_date" in report


# --- Case 2: non-v1.0 bundle is rejected ------------------------------------


def test_v2_bundle_is_rejected(tmp_path):
    bundle = _make_bundle(tmp_path / "v2.db", [("rs1", "1", 100)], version="v2.0.0")
    with pytest.raises(evr.ExtractError) as excinfo:
        evr.extract_sites(bundle)
    assert "v2.0.0" in str(excinfo.value)


def test_v2_bundle_main_exits_nonzero(tmp_path):
    bundle = _make_bundle(tmp_path / "v2.db", [("rs1", "1", 100)], version="v2.0.0")
    out = tmp_path / "sites.tsv"
    with pytest.raises(SystemExit) as excinfo:
        evr.main(
            [
                "--vep-bundle",
                str(bundle),
                "--output",
                str(out),
                "--report-json",
                str(tmp_path / "report.json"),
            ]
        )
    assert excinfo.value.code == 1
    # A rejected bundle aborts before any output is written.
    assert not out.exists()


@pytest.mark.parametrize("good", ["v1.0.0", "v1.0.1", "v1.0"])
def test_v1_0_patch_versions_accepted(tmp_path, good):
    # The gate accepts ANY v1.0.x build (a patch release is still a v1.0 catalog),
    # pinning the lower edge of startswith("v1.0") against an over-strict `== v1.0.0`.
    bundle = _make_bundle(tmp_path / "good.db", [("rs1", "1", 100)], version=good)
    rows, version, _ = evr.extract_sites(bundle)
    assert version == good
    assert rows == [("rs1", "1", 100)]


@pytest.mark.parametrize("bad", ["v1.1.0", "v1.5.0", "v0.9.0"])
def test_non_v1_0_versions_rejected(tmp_path, bad):
    # v1.x-but-not-v1.0 (and pre-v1) builds are NOT an authoritative v5 source,
    # pinning the upper edge against an over-loose startswith("v1").
    bundle = _make_bundle(tmp_path / "bad.db", [("rs1", "1", 100)], version=bad)
    with pytest.raises(evr.ExtractError):
        evr.extract_sites(bundle)


def test_missing_bundle_version_is_rejected(tmp_path):
    # bundle_metadata exists but carries no bundle_version row.
    bundle = _make_bundle(tmp_path / "nover.db", [("rs1", "1", 100)], version=None)
    with pytest.raises(evr.ExtractError) as excinfo:
        evr.extract_sites(bundle)
    assert "missing" in str(excinfo.value).lower()


def test_missing_metadata_table_is_rejected(tmp_path):
    bundle = _make_bundle(tmp_path / "nometa.db", [("rs1", "1", 100)], create_meta=False)
    with pytest.raises(evr.ExtractError):
        evr.extract_sites(bundle)


# --- Case 3: empty vep_annotations raises a clear error ---------------------


def test_empty_annotations_raises(tmp_path):
    bundle = _make_bundle(tmp_path / "empty.db", [])
    with pytest.raises(evr.ExtractError) as excinfo:
        evr.extract_sites(bundle)
    assert "empty" in str(excinfo.value).lower() or "no rsID" in str(excinfo.value)


def test_only_null_rsids_raises(tmp_path):
    # Rows exist but every rsID is NULL/empty → nothing extractable.
    bundle = _make_bundle(tmp_path / "nulls.db", [(None, "1", 100), ("", "2", 200)])
    with pytest.raises(evr.ExtractError):
        evr.extract_sites(bundle)


# --- Case 4: deterministic, byte-identical re-run ---------------------------


def test_rerun_is_byte_identical(tmp_path):
    # Build two bundles from the SAME rows in OPPOSITE insert order. Because the
    # script applies no SQL ORDER BY, this exercises that the Python sort — not
    # SQLite's DISTINCT/fetch order — is the sole source of ordering.
    rows = [
        ("rs10", "10", 5),
        ("rs2", "2", 5),
        ("rs1", "1", 5),
        ("rsY", "Y", 5),
        ("rsX", "X", 5),
        ("rsMT", "MT", 5),
    ]
    bundle_fwd = _make_bundle(tmp_path / "fwd.db", rows)
    bundle_rev = _make_bundle(tmp_path / "rev.db", list(reversed(rows)))
    first = tmp_path / "first.tsv"
    second = tmp_path / "second.tsv"
    evr.run(vep_bundle=bundle_fwd, output=first, report_json=tmp_path / "r1.json")
    evr.run(vep_bundle=bundle_rev, output=second, report_json=tmp_path / "r2.json")

    assert first.read_bytes() == second.read_bytes()
    # chrom 10 sorts AFTER 2 (numeric order), not lexically between 1 and 2; the
    # sex/mito contigs follow the canonical X < Y < MT (not sort -V's MT < X < Y).
    assert first.read_text().splitlines() == [
        "rs1\t1\t5",
        "rs2\t2\t5",
        "rs10\t10\t5",
        "rsX\tX\t5",
        "rsY\tY\t5",
        "rsMT\tMT\t5",
    ]


def test_same_position_rsid_tiebreak_is_order_independent(tmp_path):
    # Two rows share (chrom, pos) with different rsids. The rsid final-tiebreak
    # must order them rsA < rsB regardless of INSERT/fetch order — without it the
    # output would depend on the (unordered) SELECT DISTINCT row order.
    rows_fwd = [("rsB", "1", 100), ("rsA", "1", 100), ("rsC", "1", 200)]
    bundle_fwd = _make_bundle(tmp_path / "tie_fwd.db", rows_fwd)
    bundle_rev = _make_bundle(tmp_path / "tie_rev.db", list(reversed(rows_fwd)))
    out_fwd = tmp_path / "tie_fwd.tsv"
    out_rev = tmp_path / "tie_rev.tsv"
    evr.run(vep_bundle=bundle_fwd, output=out_fwd, report_json=tmp_path / "tf.json")
    evr.run(vep_bundle=bundle_rev, output=out_rev, report_json=tmp_path / "tr.json")

    expected = ["rsA\t1\t100", "rsB\t1\t100", "rsC\t1\t200"]
    assert out_fwd.read_text().splitlines() == expected
    assert out_rev.read_bytes() == out_fwd.read_bytes()


# --- Quality extras: DISTINCT dedup + NULL skipping -------------------------


def test_distinct_dedup_and_null_skipping(tmp_path):
    bundle = _make_bundle(
        tmp_path / "dup.db",
        [
            ("rs1", "1", 100),
            ("rs1", "1", 100),  # exact duplicate row -> collapsed by DISTINCT
            (None, "1", 150),  # NULL rsid -> skipped
            ("", "1", 160),  # empty rsid -> skipped
            ("rs2", "1", 200),
        ],
    )
    rows, version, _ = evr.extract_sites(bundle)
    assert version == "v1.0.0"
    assert rows == [("rs1", "1", 100), ("rs2", "1", 200)]


# --- Quality extras: floor gates --------------------------------------------


def test_check_floors_passes_at_thresholds():
    per_chrom = {str(i): 1 for i in range(1, 23)}
    per_chrom.update({"X": 1, "Y": 1, "MT": 30})
    assert evr.check_floors(600_000, per_chrom) == []


def test_check_floors_reports_each_failure():
    # Below row floor, missing Y, MT under floor.
    per_chrom = {str(i): 1 for i in range(1, 23)}
    per_chrom.update({"X": 1, "MT": 5})
    failures = evr.check_floors(10, per_chrom)
    assert any("row_count 10 < 600000" in f for f in failures)
    assert any("missing chromosomes: Y" in f for f in failures)
    assert any("MT count 5 < 30" in f for f in failures)


def test_check_floors_default_constants():
    assert evr.DEFAULT_MIN_ROWS == 600_000
    assert evr.DEFAULT_MIN_MT == 30
    assert evr.REQUIRED_CHROMS == frozenset([str(i) for i in range(1, 23)] + ["X", "Y", "MT"])


def test_main_exits_nonzero_below_floor_but_writes_outputs(tmp_path):
    # A 2-site v1.0 bundle clears the structural checks but trips the 600k floor.
    bundle = _make_bundle(tmp_path / "tiny.db", [("rs1", "1", 100), ("rs2", "2", 200)])
    out = tmp_path / "sites.tsv"
    report_json = tmp_path / "report.json"
    with pytest.raises(SystemExit) as excinfo:
        evr.main(
            [
                "--vep-bundle",
                str(bundle),
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
    assert report["row_count"] == 2


def test_main_missing_bundle_file_exits_nonzero(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        evr.main(
            [
                "--vep-bundle",
                str(tmp_path / "does_not_exist.db"),
                "--output",
                str(tmp_path / "sites.tsv"),
                "--report-json",
                str(tmp_path / "report.json"),
            ]
        )
    assert excinfo.value.code == 1
