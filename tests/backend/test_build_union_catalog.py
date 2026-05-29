"""Tests for ``scripts/build_union_catalog.py`` (Plan §0a).

Covers the seven cases enumerated in the plan:

1. Crafted ``(chrom, pos)`` collisions produce the expected audit log.
2. AncestryDNA ``25 → X`` collapse merges with a 23andMe ``X`` row at the same pos.
3. ``rs* vs kgp*`` tiebreak: rs wins regardless of lexicographic order.
4. The per-chrom hard-fail fires when a fixture omits chr7.
5. A 1-column ``--vep-bundle-rsids`` makes the bundle-known rsid win over a sibling.
6. A 3-column ``--vep-bundle-rsids`` TSV is consumed identically (first column drives it).
7. A 2-column ``--vep-bundle-rsids`` aborts with a clear error.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "build_union_catalog.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("build_union_catalog", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


buc = _load_script_module()


def _write_tsv(path: Path, rows: list[tuple[str, str, int]]) -> Path:
    path.write_text(
        "".join(f"{rsid}\t{chrom}\t{pos}\n" for rsid, chrom, pos in rows),
        encoding="utf-8",
    )
    return path


def _run(tmp_path: Path, v5_rows, adna_rows, *, vep_lines: str | None = None):
    """Drive the full ``run`` pipeline over tmp fixtures; return (report, union, audit)."""
    v5 = _write_tsv(tmp_path / "v5.tsv", v5_rows)
    adna = _write_tsv(tmp_path / "adna.tsv", adna_rows)
    vep = None
    if vep_lines is not None:
        vep = tmp_path / "vep.tsv"
        vep.write_text(vep_lines, encoding="utf-8")

    report, union_rows = buc.run(
        twentythreeandme_sites=v5,
        ancestrydna_sites=adna,
        output=tmp_path / "union_sites.tsv",
        audit_log=tmp_path / "union_sites_audit.tsv",
        report_json=tmp_path / "union_sites_report.json",
        vep_bundle_rsids=vep,
    )
    audit_lines = (tmp_path / "union_sites_audit.tsv").read_text().splitlines()
    return report, union_rows, audit_lines


# --- Case 1: crafted collisions produce the expected audit log -------------


def test_crafted_collision_produces_audit_log(tmp_path):
    # rs100 vs rs200 collide at 1:100 -> lex-smallest (rs100) wins; rs200 logged.
    # rs300 at 2:200 is conflict-free -> no audit row.
    v5_rows = [("rs200", "1", 100), ("rs300", "2", 200)]
    adna_rows = [("rs100", "1", 100)]
    union_rows, audit_rows, conflict_count = buc.build_union(v5_rows, adna_rows)

    assert ("rs100", "1", 100) in union_rows
    assert ("rs300", "2", 200) in union_rows
    assert conflict_count == 1
    assert audit_rows == [("1", 100, "rs100", "rs200", "lexicographic")]


# --- Case 2: AncestryDNA 25 -> X collapse merges with a 23andMe X row -------


def test_ancestrydna_par_collapse_merges_with_23andme_x(tmp_path):
    # AncestryDNA raw chrom 25 collapses to X (PAR); same site as the v5 X row.
    report, union_rows, _ = _run(
        tmp_path,
        v5_rows=[("rs1", "X", 5000)],
        adna_rows=[("rs1", "25", 5000)],
    )
    # The two rows merge into one X site; no leftover "25" chromosome anywhere.
    x_rows = [r for r in union_rows if r[1] == "X"]
    assert x_rows == [("rs1", "X", 5000)]
    assert all(r[1] != "25" for r in union_rows)
    assert report["union_count"] == 1
    assert report["intersection_count"] == 1
    assert report["per_chrom_counts"] == {"X": 1}
    # §0a: the vep_bundle_rsids key is omitted when --vep-bundle-rsids is absent.
    assert set(report["sha256_inputs"]) == {"twentythreeandme", "ancestrydna"}


# --- Case 3: rs* vs kgp* tiebreak (rs wins regardless of lex order) ---------


def test_rs_beats_kgp_regardless_of_lex_order(tmp_path):
    # "kgp001" < "rs100" lexicographically, but rule 5 forces the rs* ID to win.
    v5_rows = [("rs100", "1", 100)]
    adna_rows = [("kgp001", "1", 100)]
    union_rows, audit_rows, _ = buc.build_union(v5_rows, adna_rows)

    assert ("rs100", "1", 100) in union_rows
    assert audit_rows == [("1", 100, "rs100", "kgp001", "rs_over_non_rs")]


# --- Case 4: per-chrom hard-fail fires when chr7 is omitted -----------------


def test_per_chrom_assertion_fires_when_chr7_omitted():
    # One site per autosome except chr7, plus chrX. With min_autosome=1 the only
    # autosome below floor is the omitted chr7.
    union_rows = [(f"rs{i}", str(i), 1000 + i) for i in range(1, 23) if i != 7] + [("rsX", "X", 1)]
    report = buc.build_report(
        union_rows,
        [],
        union_rows,
        0,
        sha256_inputs={},
        sha256_output="",
        git_commit="test",
        build_date="2026-05-28",
    )
    _, hard_failures = buc.check_assertions(
        report,
        union_rows,
        min_union=1,
        min_intersection=0,
        min_rs=0,
        min_autosome=1,
        min_chrx=1,
        warn_chry=0,
        warn_chrmt=0,
    )
    assert any("chromosome 7 count" in f for f in hard_failures)
    # Present autosomes (e.g. chr1) and chrX do not fire.
    assert not any(f.startswith("chromosome 1 count") for f in hard_failures)
    assert not any(f.startswith("chrX") for f in hard_failures)


def test_full_thresholds_pass_clears_small_realistic_report():
    # Sanity: a report meeting every floor produces no hard failures.
    union_rows = [(f"rs{i}", str(i), i) for i in range(1, 23)] + [
        ("rsX", "X", 1),
        ("rsY", "Y", 1),
        ("rsMT", "MT", 1),
    ]
    report = buc.build_report(
        union_rows,
        union_rows,
        union_rows,
        0,
        sha256_inputs={},
        sha256_output="",
        git_commit="t",
        build_date="2026-05-28",
    )
    warnings, hard_failures = buc.check_assertions(
        report,
        union_rows,
        min_union=1,
        min_intersection=1,
        min_rs=1,
        min_autosome=1,
        min_chrx=1,
        warn_chry=1,
        warn_chrmt=1,
    )
    assert hard_failures == []
    assert warnings == []


# --- Case 5: 1-column --vep-bundle-rsids tiebreak ---------------------------


def test_vep_bundle_one_column_breaks_tie(tmp_path):
    # rs111 (lex-smaller, not in bundle) vs rs999 (in bundle). The bundle-known
    # sibling must win even though it loses the lexicographic fallback.
    report, union_rows, audit_lines = _run(
        tmp_path,
        v5_rows=[("rs111", "1", 100)],
        adna_rows=[("rs999", "1", 100)],
        vep_lines="rs999\n",
    )
    assert ("rs999", "1", 100) in union_rows
    assert "1\t100\trs999\trs111\tvep_bundle" in audit_lines
    assert "vep_bundle_rsids" in report["sha256_inputs"]


# --- Case 6: 3-column --vep-bundle-rsids consumed identically ---------------


def test_vep_bundle_three_column_consumed_identically(tmp_path):
    # Same outcome as the 1-column case using the extract_vep_bundle_rsids.py
    # 3-column layout; only the first column (rsid) drives the tiebreak.
    report, union_rows, audit_lines = _run(
        tmp_path,
        v5_rows=[("rs111", "1", 100)],
        adna_rows=[("rs999", "1", 100)],
        vep_lines="rs999\tX\t12345\nrs777\t2\t6789\n",
    )
    assert ("rs999", "1", 100) in union_rows
    assert "1\t100\trs999\trs111\tvep_bundle" in audit_lines


def test_vep_bundle_loader_one_and_three_columns(tmp_path):
    one_col = tmp_path / "one.tsv"
    one_col.write_text("rs1\nrs2\n# comment\n\nrs3\n", encoding="utf-8")
    assert buc.load_vep_bundle_rsids(one_col) == {"rs1", "rs2", "rs3"}

    three_col = tmp_path / "three.tsv"
    three_col.write_text("rs1\t1\t100\nrs2\tX\t200\n", encoding="utf-8")
    assert buc.load_vep_bundle_rsids(three_col) == {"rs1", "rs2"}


# --- Case 7: 2-column (and >3-column) --vep-bundle-rsids aborts -------------


def test_vep_bundle_two_columns_aborts(tmp_path):
    bad = tmp_path / "bad.tsv"
    bad.write_text("rs1\t100\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected --vep-bundle-rsids column count: 2"):
        buc.load_vep_bundle_rsids(bad)


def test_vep_bundle_four_columns_aborts(tmp_path):
    bad = tmp_path / "bad4.tsv"
    bad.write_text("rs1\t1\t100\tEXTRA\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected --vep-bundle-rsids column count: 4"):
        buc.load_vep_bundle_rsids(bad)
