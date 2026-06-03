"""Regression guard: LAIRunner must accept the v2.0.0 liftover filename.

The v2.0.0 LAI bundle renamed the rsID->GRCh38 liftover table
``liftover/rsid_to_grch38.tsv`` (v1.1) -> ``liftover/array_site_mapping.tsv``
(``scripts/lai_bundle_v2/07_assemble_bundle.sh``; identical 3-column format).
The runtime ``LAIRunner`` previously hardcoded the v1.1 name, so LAI inference
raised ``FileNotFoundError`` against every published v2.0.0 bundle. These tests
lock in that both names resolve and that a genuinely absent table still errors.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.analysis.lai_runner import LAIRunner


def _make_stub_bundle(root: Path, liftover_name: str | None) -> Path:
    """Build a minimal bundle whose component files exist (empty stubs).

    ``_validate_bundle`` only checks for *existence*, and ``__init__`` reads the
    liftover table, so empty stubs + a small liftover TSV are enough to drive
    ``LAIRunner.__init__`` without invoking Beagle/gnomix.
    """
    (root / "beagle").mkdir(parents=True)
    (root / "beagle" / "beagle.jar").touch()
    for c in range(1, 23):
        (root / "phasing_panel").mkdir(exist_ok=True)
        (root / "phasing_panel" / f"ref_panel_chr{c}.vcf.gz").touch()
        gm = root / "gnomix_models" / f"chr{c}"
        gm.mkdir(parents=True)
        for f in ("metadata.npz", "base_coefs.npz", "smoother.json"):
            (gm / f).touch()
        (root / "genetic_maps").mkdir(exist_ok=True)
        (root / "genetic_maps" / f"plink.chrchr{c}.GRCh38.map").touch()
    (root / "liftover").mkdir()
    if liftover_name is not None:
        (root / "liftover" / liftover_name).write_text(
            "rs1\tchr1\t100\nrs2\tchr2\t200\n", encoding="utf-8"
        )
    return root


def test_v2_0_0_liftover_name_is_accepted(tmp_path: Path) -> None:
    bundle = _make_stub_bundle(tmp_path / "v2", "array_site_mapping.tsv")
    runner = LAIRunner(bundle)
    assert runner.rsid_lookup == {"rs1": ("chr1", 100), "rs2": ("chr2", 200)}


def test_v1_1_liftover_name_still_works(tmp_path: Path) -> None:
    bundle = _make_stub_bundle(tmp_path / "v1", "rsid_to_grch38.tsv")
    runner = LAIRunner(bundle)
    assert runner.rsid_lookup == {"rs1": ("chr1", 100), "rs2": ("chr2", 200)}


def test_missing_liftover_table_raises(tmp_path: Path) -> None:
    bundle = _make_stub_bundle(tmp_path / "none", None)
    with pytest.raises(FileNotFoundError, match="array_site_mapping.tsv"):
        LAIRunner(bundle)
