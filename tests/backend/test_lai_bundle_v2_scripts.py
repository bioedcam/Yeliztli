"""Sanity tests for scripts/lai_bundle_v2/ — Step 20 deliverable.

The actual cluster rebuild is out-of-repo (Plan §6.2, §12.2 PR-0c). This
test module verifies that the in-repo scripts package ships with:

  1. The expected phase scripts present and executable.
  2. The orchestrator `run_rebuild.sh` references every phase in the
     documented order.
  3. No script hardcodes the v1.1 working directory — every path is
     either an env-var-overridable default or sourced from `env.sh`.
  4. Phase scripts source the shared `env.sh` (so overrides flow through).
  5. The Python helper scripts compile cleanly under the project Python.

The runbook is also verified for the rsync flow that ports the scripts onto
the cluster (Plan §6.3 step 1, runbook §4).
"""

from __future__ import annotations

import importlib.util
import py_compile
import re
import stat
import subprocess
from pathlib import Path

import pytest


def _load_module(filename: str, mod_name: str):
    """Import a digit-prefixed helper (e.g. 06e_lai_accuracy.py) by path."""
    spec = importlib.util.spec_from_file_location(mod_name, SCRIPTS_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts" / "lai_bundle_v2"
RUNBOOK = REPO_ROOT / "docs" / "lai-bundle-release-runbook.md"


EXPECTED_PHASE_SCRIPTS = [
    "01_download_panel.sh",
    "02_prepare_sites.sh",
    "03_subset_panel.sh",
    "04_admixture_filter.sh",
    "05_train_gnomix.sh",
    "06_validate.sh",
    "07_assemble_bundle.sh",
]

EXPECTED_HELPERS = [
    "env.sh",
    "run_rebuild.sh",
    "04c_filter_single_ancestry.py",
    "06a_identify_trios.py",
    "06b_mendelian_phasing.py",
    "06c_beagle_loo_phasing.sh",
    "06d_phasing_accuracy.py",
    "06e_lai_accuracy.py",
    "07_write_metadata.py",
]


# v1.1 hardcoded path that scripts MUST NOT bake in. The dispatcher should
# accept it via env override only.
_V1_HARDCODED_PATH = re.compile(r"/exports/people/mondragonlab/ecc1695/lai_bundle/(?!v2)")
_HOME_LAI_BUNDLE_V1_HARDCODED = re.compile(r"\$HOME/lai_bundle(?!_v2)\b|~/lai_bundle(?!_v2)\b")


class TestScriptsPresent:
    @pytest.mark.parametrize("name", EXPECTED_PHASE_SCRIPTS + EXPECTED_HELPERS)
    def test_script_exists(self, name: str) -> None:
        path = SCRIPTS_DIR / name
        assert path.is_file(), f"{path} missing"

    @pytest.mark.parametrize("name", EXPECTED_PHASE_SCRIPTS + EXPECTED_HELPERS)
    def test_script_executable(self, name: str) -> None:
        path = SCRIPTS_DIR / name
        mode = path.stat().st_mode
        assert mode & stat.S_IXUSR, f"{path} is not user-executable"


class TestOrchestratorPhaseOrder:
    def test_run_rebuild_lists_every_phase_in_order(self) -> None:
        text = (SCRIPTS_DIR / "run_rebuild.sh").read_text()
        # ALL_PHASES=(01 02 03 04 05 06 07)
        m = re.search(r"ALL_PHASES=\(([^)]+)\)", text)
        assert m, "run_rebuild.sh must declare ALL_PHASES=(...)"
        phases = m.group(1).split()
        assert phases == ["01", "02", "03", "04", "05", "06", "07"]

    def test_phase_dispatch_maps_each_phase_to_its_script(self) -> None:
        text = (SCRIPTS_DIR / "run_rebuild.sh").read_text()
        for phase_script in EXPECTED_PHASE_SCRIPTS:
            phase_num = phase_script.split("_", 1)[0]
            # PHASE_SCRIPT[NN]="NN_..."
            pat = rf"\[{re.escape(phase_num)}\]=\"{re.escape(phase_script)}\""
            assert re.search(pat, text), f"run_rebuild.sh missing dispatch for phase {phase_num}"

    def test_orchestrator_sources_env_sh(self) -> None:
        text = (SCRIPTS_DIR / "run_rebuild.sh").read_text()
        assert 'source "$SCRIPT_DIR/env.sh"' in text


class TestEveryPhaseSourcesEnv:
    @pytest.mark.parametrize("name", EXPECTED_PHASE_SCRIPTS + ["06c_beagle_loo_phasing.sh"])
    def test_phase_script_sources_env(self, name: str) -> None:
        text = (SCRIPTS_DIR / name).read_text()
        assert 'source "$SCRIPT_DIR/env.sh"' in text, f"{name} must source env.sh"


class TestNoV11PathLeak:
    """Plan §6.2 mandates the v1.1 working dir is read-only reference. Scripts
    must default to v2.0.0 paths and accept the v1.1 path only via env-var
    override (`WORKDIR=...`), never as a hardcoded constant.
    """

    @pytest.mark.parametrize(
        "name",
        EXPECTED_PHASE_SCRIPTS + EXPECTED_HELPERS,
    )
    def test_no_hardcoded_v1_cluster_path(self, name: str) -> None:
        text = (SCRIPTS_DIR / name).read_text()
        assert not _V1_HARDCODED_PATH.search(text), (
            f"{name} hardcodes the v1.1 cluster path; parametrize via env.sh instead"
        )

    @pytest.mark.parametrize(
        "name",
        EXPECTED_PHASE_SCRIPTS + EXPECTED_HELPERS,
    )
    def test_no_hardcoded_home_lai_bundle_v1(self, name: str) -> None:
        text = (SCRIPTS_DIR / name).read_text()
        # env.sh ships the default `$HOME/lai_bundle_v2` as the WORKDIR
        # default; no other script may bake in a `~/lai_bundle` (v1) path.
        if name == "env.sh":
            return
        assert not _HOME_LAI_BUNDLE_V1_HARDCODED.search(text), (
            f"{name} hardcodes ~/lai_bundle (v1.1); use $WORKDIR (sourced from env.sh)"
        )


class TestEnvShDefaults:
    """`env.sh` is the single source of truth for parametrization."""

    def test_default_workdir_is_v2(self) -> None:
        text = (SCRIPTS_DIR / "env.sh").read_text()
        assert "WORKDIR:=$HOME/lai_bundle_v2" in text

    def test_default_bundle_version_is_v2(self) -> None:
        text = (SCRIPTS_DIR / "env.sh").read_text()
        assert "LAI_BUNDLE_VERSION:=v2.0.0" in text

    def test_union_catalog_required_input(self) -> None:
        # UNION_CATALOG_TSV must default to empty and be checked by
        # 02_prepare_sites.sh via require_file (Plan §6.4 phase 2).
        env_text = (SCRIPTS_DIR / "env.sh").read_text()
        phase2_text = (SCRIPTS_DIR / "02_prepare_sites.sh").read_text()
        assert "UNION_CATALOG_TSV:=" in env_text
        assert 'require_file "$UNION_CATALOG_TSV"' in phase2_text

    def test_admixture_seed_is_locked(self) -> None:
        # Plan §6.3 step 4: re-running with the same seed reproduces labels
        # bit-for-bit. The seed default is part of the build contract.
        text = (SCRIPTS_DIR / "env.sh").read_text()
        assert "ADMIXTURE_SEED:=42" in text


class TestShellSyntax:
    """Catch shell parse errors before they hit the cluster."""

    @pytest.mark.parametrize(
        "name",
        ["env.sh", "run_rebuild.sh"] + EXPECTED_PHASE_SCRIPTS + ["06c_beagle_loo_phasing.sh"],
    )
    def test_bash_n_passes(self, name: str) -> None:
        path = SCRIPTS_DIR / name
        result = subprocess.run(
            ["bash", "-n", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{name} has shell-syntax errors:\n{result.stderr}"


class TestPythonHelpersCompile:
    @pytest.mark.parametrize(
        "name",
        [
            "04c_filter_single_ancestry.py",
            "06a_identify_trios.py",
            "06b_mendelian_phasing.py",
            "06d_phasing_accuracy.py",
            "06e_lai_accuracy.py",
            "07_write_metadata.py",
        ],
    )
    def test_py_compile(self, name: str) -> None:
        py_compile.compile(str(SCRIPTS_DIR / name), doraise=True)


class TestLaiAccuracyParser:
    """06e parses gnomix's `Estimated val accuracy: NN.NN%` (the proven v1.1
    LAI-accuracy source), so lock in the real log-line format.
    """

    def _mod(self):
        return _load_module("06e_lai_accuracy.py", "lai_accuracy_06e")

    @pytest.mark.parametrize(
        "line,expected",
        [
            ("Estimated val accuracy: 86.88%", 0.8688),
            ("Estimated val accuracy: 85.7%", 0.857),  # gnomix drops trailing zero
            ("Estimated val accuracy: 89.79%", 0.8979),
        ],
    )
    def test_parses_real_gnomix_format(self, line: str, expected: float) -> None:
        acc = self._mod().parse_val_accuracy(f"...\n{line}\nTime: 5m\n")
        assert acc == pytest.approx(expected, abs=1e-6)

    def test_last_match_wins(self) -> None:
        text = "Estimated val accuracy: 70.0%\nretry\nEstimated val accuracy: 88.5%\n"
        assert self._mod().parse_val_accuracy(text) == pytest.approx(0.885)

    def test_no_match_returns_none(self) -> None:
        assert self._mod().parse_val_accuracy("no accuracy here\n") is None


class TestPhase06WiresLogParser:
    """06_validate.sh must drive 06e off the gnomix logs (--log-dir/--chroms),
    not the removed inference-glob contract (--gnomix-dir/--single-ancestry).
    """

    def test_06e_called_with_log_dir(self) -> None:
        text = (SCRIPTS_DIR / "06_validate.sh").read_text()
        assert "06e_lai_accuracy.py" in text
        assert "--log-dir" in text and "--chroms" in text
        # the dead inference-glob flags must be gone
        assert "--gnomix-dir" not in text


class TestTrioIdentification:
    """06a builds trios from the 1000G pedigree ∩ the panel (v1.1 method), since
    the gnomAD meta has no paternal/maternal-id columns.
    """

    def _run(self, tmp_path, ped_rows, panel, meta_rows):
        ped = tmp_path / "g1k.ped"
        ped.write_text(
            "Family ID\tIndividual ID\tPaternal ID\tMaternal ID\tGender\tPopulation\n"
            + "".join(ped_rows)
        )
        (tmp_path / "panel.txt").write_text("\n".join(panel) + "\n")
        meta = tmp_path / "meta.tsv"
        meta.write_text(
            "s\thgdp_tgp_meta.Genetic.region\thgdp_tgp_meta.Population\n" + "".join(meta_rows)
        )
        out_ped = tmp_path / "trio_pedigree.tsv"
        out_children = tmp_path / "trio_children.txt"
        subprocess.run(
            [
                "python",
                str(SCRIPTS_DIR / "06a_identify_trios.py"),
                "--ped",
                str(ped),
                "--panel-samples",
                str(tmp_path / "panel.txt"),
                "--meta",
                str(meta),
                "--out-trios",
                str(out_children),
                "--out-pedigree",
                str(out_ped),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return out_ped.read_text(), out_children.read_text()

    def test_complete_trio_kept_incomplete_dropped(self, tmp_path) -> None:
        ped_rows = [
            # complete trio: child HG1 + both parents all in panel
            "F1\tHG1\tHG2\tHG3\t1\tACB\n",
            "F1\tHG2\t0\t0\t1\tACB\n",
            "F1\tHG3\t0\t0\t2\tACB\n",
            # child whose father is NOT in the panel -> dropped
            "F2\tHG4\tHG9\tHG5\t1\tCEU\n",
            "F2\tHG5\t0\t0\t2\tCEU\n",
        ]
        panel = ["HG1", "HG2", "HG3", "HG4", "HG5"]  # HG9 (father of HG4) absent
        meta_rows = ["HG1\tAFR\tACB\n", "HG4\tEUR\tCEU\n"]
        ped_text, children = self._run(tmp_path, ped_rows, panel, meta_rows)
        assert "child\tfather\tmother\tpopulation\tregion" in ped_text
        assert "HG1\tHG2\tHG3\tACB\tAFR" in ped_text
        assert "HG4" not in ped_text  # incomplete trio dropped
        assert children.strip() == "HG1"


class TestSlurmRebuild:
    """SLURM DAG: prep(02-04) -> gnomix array(05, per-chrom) -> finish(06-07),
    and phase 05 runs gnomix in its own conda env.
    """

    SLURM_DIR = SCRIPTS_DIR / "slurm"

    @pytest.mark.parametrize("name", ["prep.sbatch", "05_train_gnomix.sbatch", "finish.sbatch"])
    def test_sbatch_present_and_bash_n(self, name: str) -> None:
        path = self.SLURM_DIR / name
        assert path.is_file(), f"{path} missing"
        r = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, check=False)
        assert r.returncode == 0, r.stderr

    def test_orchestrator_chains_the_dag(self) -> None:
        path = SCRIPTS_DIR / "run_rebuild_slurm.sh"
        r = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, check=False)
        assert r.returncode == 0, r.stderr
        text = path.read_text()
        for f in ("prep.sbatch", "05_train_gnomix.sbatch", "finish.sbatch"):
            assert f in text
        assert "--dependency=" in text and "afterok" in text  # chained
        assert "--array=" in text  # phase 05 is an array

    def test_phase05_array_is_per_chromosome_and_caps_cores(self) -> None:
        text = (self.SLURM_DIR / "05_train_gnomix.sbatch").read_text()
        assert "--array=1-22" in text
        assert "SLURM_ARRAY_TASK_ID" in text  # one chromosome per task
        assert "n_cores" in text  # caps gnomix cores per task

    def test_phase05_runs_in_gnomix_env(self) -> None:
        text = (SCRIPTS_DIR / "05_train_gnomix.sh").read_text()
        assert "conda run -n" in text and "GNOMIX_ENV" in text

    def test_env_defines_gnomix_env_and_config(self) -> None:
        text = (SCRIPTS_DIR / "env.sh").read_text()
        assert "GNOMIX_ENV:=gnomix" in text
        assert "GNOMIX_CONFIG:=" in text


class TestRunbook:
    def test_runbook_exists(self) -> None:
        assert RUNBOOK.is_file(), f"runbook missing at {RUNBOOK}"

    def test_runbook_documents_rsync_flow(self) -> None:
        text = RUNBOOK.read_text()
        # Plan §6.3 step 1 mandates an rsync section.
        assert "rsync" in text.lower()
        assert "scripts/lai_bundle_v2" in text
        assert "two:~/lai_bundle_v2/scripts/" in text

    def test_runbook_calls_out_v2_paths(self) -> None:
        text = RUNBOOK.read_text()
        # Both v1.1 (reference) and v2.0.0 working dirs must be named so the
        # operator can't confuse the two.
        assert "/exports/people/mondragonlab/ecc1695/lai_bundle_v2/" in text
        assert "/exports/people/mondragonlab/ecc1695/lai_bundle/" in text

    def test_runbook_lists_bio_validator_targets(self) -> None:
        text = RUNBOOK.read_text()
        # Plan §6.4 final paragraph + Plan §12.2 Validation gates.
        assert "0.88" in text  # mean per-window LAI accuracy
        assert "0.0566" in text  # phasing switch error baseline

    def test_runbook_orchestrator_invocation_documented(self) -> None:
        text = RUNBOOK.read_text()
        assert "bash scripts/run_rebuild.sh" in text
        assert "UNION_CATALOG_TSV=" in text
