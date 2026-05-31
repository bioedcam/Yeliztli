"""Tests for ``scripts/generate_vep_input.py``.

Covers the dispatcher-backed vendor parse path (23andMe today, AncestryDNA
once step 27 lands the dispatcher), the ``--rsid-catalog`` mode that emits a
sites-only VCF, and the ``--rsid-list`` mode that emits a bare rs* ID list
(for ``vep --format id``) — all from a bare rsid+chrom+pos TSV.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "generate_vep_input.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("generate_vep_input", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _has_dispatcher() -> bool:
    try:
        import backend.ingestion.dispatcher  # noqa: F401
    except ModuleNotFoundError as exc:
        if exc.name != "backend.ingestion.dispatcher":
            raise
        return False
    return True


def _has_ancestrydna_parser() -> bool:
    """The dispatcher routes AncestryDNA to ``parser_ancestrydna`` (step 30).

    Until that module exists, the dispatcher raises ``UnsupportedFormatError``
    on AncestryDNA inputs — so the vendor-ancestrydna case must remain
    skipped even though step 27's dispatcher is in place.
    """
    try:
        import backend.ingestion.parser_ancestrydna  # noqa: F401
    except ModuleNotFoundError as exc:
        if exc.name != "backend.ingestion.parser_ancestrydna":
            raise
        return False
    return True


def _ancestrydna_fixture() -> Path | None:
    # Step 33 retired the legacy `sample_ancestrydna.txt` in favor of the
    # §8.6 edge-case-covering `sample_ancestrydna_v2.txt`. Prefer the v2
    # fixture; fall back to the legacy name only if it still lingers on an
    # un-migrated checkout.
    for name in ("sample_ancestrydna_v2.txt", "sample_ancestrydna.txt"):
        path = FIXTURES / name
        if path.exists():
            return path
    return None


_ANCESTRYDNA_FIXTURE = _ancestrydna_fixture()
_DISPATCHER_AVAILABLE = _has_dispatcher()
_ANCESTRYDNA_PARSER_AVAILABLE = _has_ancestrydna_parser()


def _read_vcf_lines(path: Path) -> tuple[list[str], list[str]]:
    content = path.read_text(encoding="utf-8").splitlines()
    headers = [ln for ln in content if ln.startswith("#")]
    data_lines = [ln for ln in content if ln and not ln.startswith("#")]
    return headers, data_lines


# ---------------------------------------------------------------------------
# Vendor parse path (genotype-VCF mode)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "vendor,fixture",
    [
        pytest.param(
            "23andme",
            FIXTURES / "sample_23andme_v5.txt",
            id="vendor-23andme",
        ),
        pytest.param(
            "ancestrydna",
            _ANCESTRYDNA_FIXTURE,
            marks=pytest.mark.skipif(
                not _DISPATCHER_AVAILABLE
                or _ANCESTRYDNA_FIXTURE is None
                or not _ANCESTRYDNA_PARSER_AVAILABLE,
                reason=(
                    "dispatcher (step 27), AncestryDNA parser (step 30), or "
                    "AncestryDNA v2 fixture (step 33/34) not yet landed"
                ),
            ),
            id="vendor-ancestrydna",
        ),
    ],
)
def test_generate_vep_vcf_per_vendor(tmp_path: Path, vendor: str, fixture: Path) -> None:
    module = _load_script_module()
    output_path = tmp_path / "vep_input.vcf"

    stats = module.generate_vep_vcf(fixture, output_path)

    assert output_path.exists()
    headers, data_lines = _read_vcf_lines(output_path)

    assert headers[0] == "##fileformat=VCFv4.2"
    assert any(h.startswith("##contig=<ID=1>") for h in headers)
    assert any(h.startswith("##contig=<ID=X>") for h in headers)
    assert headers[-1] == "\t".join(
        ("#CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO")
    )

    assert stats["total_parsed"] > 0
    assert stats["written"] >= 0
    assert stats["written"] + stats["skipped"] == stats["total_parsed"]
    assert len(data_lines) == stats["written"]

    for line in data_lines:
        cols = line.split("\t")
        assert len(cols) == 8
        chrom, pos, _rsid, ref, alt, qual, filt, info = cols
        assert chrom in {str(n) for n in range(1, 23)} | {"X", "Y", "MT"}
        assert int(pos) > 0
        assert ref in {"A", "C", "G", "T"}
        assert alt == "." or alt in {"A", "C", "G", "T"}
        assert qual == "."
        assert filt == "PASS"
        assert info == "23AM"


def test_generate_vep_vcf_falls_back_to_23andme_without_dispatcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the dispatcher is absent, the script must transparently call
    `parse_23andme` so 23andMe ingestion keeps working pre-step-27.
    """
    module = _load_script_module()
    monkeypatch.setattr(module, "_dispatcher_parse", None)
    monkeypatch.setattr(module, "_HAS_DISPATCHER", False)

    real_parse_23andme = module.parse_23andme
    call_count = {"n": 0}

    def _spy(*args, **kwargs):
        call_count["n"] += 1
        return real_parse_23andme(*args, **kwargs)

    monkeypatch.setattr(module, "parse_23andme", _spy)

    output_path = tmp_path / "fallback.vcf"
    stats = module.generate_vep_vcf(FIXTURES / "sample_23andme_v5.txt", output_path)
    assert call_count["n"] == 1, "legacy parse_23andme must be invoked when dispatcher is absent"
    assert stats["total_parsed"] > 0
    assert output_path.exists()


# ---------------------------------------------------------------------------
# --rsid-catalog mode
# ---------------------------------------------------------------------------


def test_rsid_catalog_mode_emits_sorted_sites_only_vcf(tmp_path: Path) -> None:
    module = _load_script_module()

    catalog_path = tmp_path / "union_catalog.tsv"
    catalog_path.write_text(
        "# union catalog header comment\nrs1\t1\t100\nrs2\tX\t500\nrs3\t1\t50\n\nrs4\tMT\t200\n",
        encoding="utf-8",
    )

    output_path = tmp_path / "catalog.vcf"
    stats = module.generate_catalog_vcf(catalog_path, output_path)

    assert stats == {"total_parsed": 4, "written": 4}
    assert output_path.exists()

    headers, data_lines = _read_vcf_lines(output_path)
    assert headers[0] == "##fileformat=VCFv4.2"
    assert any("##source=GenomeInsight-rsid-catalog" in h for h in headers)
    assert headers[-1] == "\t".join(
        ("#CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO")
    )
    assert len(data_lines) == 4

    fields = [ln.split("\t") for ln in data_lines]
    assert fields[0] == ["1", "50", "rs3", "N", ".", ".", "PASS", "."]
    assert fields[1] == ["1", "100", "rs1", "N", ".", ".", "PASS", "."]
    assert fields[2] == ["X", "500", "rs2", "N", ".", ".", "PASS", "."]
    assert fields[3] == ["MT", "200", "rs4", "N", ".", ".", "PASS", "."]


@pytest.mark.parametrize(
    "row,error_fragment",
    [
        ("rs1\t1\n", "expected 3 tab-separated columns"),
        ("rs1\t1\t100\textra\n", "expected 3 tab-separated columns"),
        ("\t1\t100\n", "empty rsid"),
        ("rs1\t\t100\n", "empty chrom"),
        ("rs1\t1\tabc\n", "non-numeric position"),
        ("rs1\t1\t0\n", "non-positive position"),
        ("rs1\t1\t-5\n", "non-positive position"),
    ],
)
def test_rsid_catalog_rejects_malformed(tmp_path: Path, row: str, error_fragment: str) -> None:
    module = _load_script_module()
    catalog_path = tmp_path / "bad.tsv"
    catalog_path.write_text(row, encoding="utf-8")

    with pytest.raises(ValueError, match=error_fragment):
        list(module._iter_catalog_rows(catalog_path))


@pytest.mark.parametrize(
    "genotype,expected",
    [
        ("", None),
        ("--", None),
        ("AAA", None),
        ("ACGT", None),
        ("XY", None),
        ("A", ("A", ".")),
        ("AA", ("A", ".")),
        ("AC", ("A", "C")),
    ],
)
def test_genotype_to_ref_alt_rejects_invalid_lengths(genotype, expected) -> None:
    module = _load_script_module()
    assert module._genotype_to_ref_alt(genotype) == expected


def test_cli_rsid_catalog_round_trip(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.tsv"
    catalog_path.write_text("rs1\t1\t100\nrs2\t2\t200\n", encoding="utf-8")
    output_path = tmp_path / "out.vcf"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--rsid-catalog",
            str(catalog_path),
            "-o",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.returncode == 0
    headers, data_lines = _read_vcf_lines(output_path)
    assert any("##source=GenomeInsight-rsid-catalog" in h for h in headers)
    assert data_lines == [
        "1\t100\trs1\tN\t.\t.\tPASS\t.",
        "2\t200\trs2\tN\t.\t.\tPASS\t.",
    ]


# ---------------------------------------------------------------------------
# --rsid-list mode (for VEP --format id)
# ---------------------------------------------------------------------------


def test_rsid_list_mode_filters_dedupes_and_sorts(tmp_path: Path) -> None:
    module = _load_script_module()

    # rs* (incl. a duplicate rsID at a 2nd position), i* internal, coordinate-style,
    # and kgp* proxy — only the unique rs* IDs survive, lexicographically sorted.
    catalog_path = tmp_path / "union_catalog.tsv"
    catalog_path.write_text(
        "# header\n"
        "rs10\t1\t100\n"
        "rs2\t1\t150\n"
        "rs10\tX\t9000\n"  # duplicate rsID at another position -> deduped
        "i5000123\t2\t200\n"  # 23andMe internal -> skipped
        "1:762320C>T\t1\t762320\n"  # coordinate-style chip ID -> skipped
        "kgp123\t3\t300\n",  # AncestryDNA proxy -> skipped
        encoding="utf-8",
    )

    output_path = tmp_path / "rsids.txt"
    stats = module.generate_rsid_list(catalog_path, output_path)

    assert stats == {"total_catalog_rows": 6, "rs_written": 2, "non_rs_skipped": 4}
    # lexicographic sort: "rs10" < "rs2" ('1' < '2'); deduped to one "rs10".
    assert output_path.read_text(encoding="utf-8") == "rs10\nrs2\n"


def test_cli_rsid_list_round_trip(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.tsv"
    catalog_path.write_text("rs5\t1\t100\ni999\t2\t200\nrs3\t2\t300\n", encoding="utf-8")
    output_path = tmp_path / "rsids.txt"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--rsid-list",
            str(catalog_path),
            "-o",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.returncode == 0
    assert output_path.read_text(encoding="utf-8") == "rs3\nrs5\n"


def test_cli_rsid_list_and_catalog_are_mutually_exclusive(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.tsv"
    catalog_path.write_text("rs1\t1\t100\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--rsid-list",
            "--rsid-catalog",
            str(catalog_path),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "mutually exclusive" in result.stderr


def test_rsid_list_empty_catalog(tmp_path: Path) -> None:
    module = _load_script_module()
    catalog_path = tmp_path / "empty.tsv"
    catalog_path.write_text("# only a comment\n\n", encoding="utf-8")
    output_path = tmp_path / "rsids.txt"

    stats = module.generate_rsid_list(catalog_path, output_path)

    assert stats == {"total_catalog_rows": 0, "rs_written": 0, "non_rs_skipped": 0}
    assert output_path.read_text(encoding="utf-8") == ""  # no trailing newline


def test_rsid_list_all_non_rs_catalog(tmp_path: Path) -> None:
    module = _load_script_module()
    catalog_path = tmp_path / "non_rs.tsv"
    catalog_path.write_text(
        "i5000123\t1\t100\nkgp99\t2\t200\nVG12\t3\t300\n1:762320C>T\t1\t762320\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "rsids.txt"

    stats = module.generate_rsid_list(catalog_path, output_path)

    assert stats == {"total_catalog_rows": 4, "rs_written": 0, "non_rs_skipped": 4}
    assert output_path.read_text(encoding="utf-8") == ""


def test_cli_rsid_list_stdout_keeps_stats_off_stdout(tmp_path: Path) -> None:
    # stdout (no -o) must carry ONLY the rsID list; --stats summary goes to stderr.
    catalog_path = tmp_path / "catalog.tsv"
    catalog_path.write_text("rs5\t1\t100\ni999\t2\t200\nrs3\t2\t300\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--rsid-list", "--stats", str(catalog_path)],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout == "rs3\nrs5\n"  # pure rsID list, no stats pollution
    assert "Mode:     rsid-list" in result.stderr
