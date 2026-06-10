"""Removal invariants for the legacy ``scripts/lai_runner.py`` (Step 22a; Plan §6.6).

Locks two contracts after the step-22a deletion:

1. The legacy file is gone from the working tree.
2. No source-tree import or path reference to it remains across
   ``backend/``, ``tests/``, ``scripts/``, and ``frontend/``.

Re-runs the same grep invariant the deletion PR ran pre-merge so a future
regression (e.g. a stale import in a generated file or a copy-paste from the
git history) trips immediately.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SEARCH_ROOTS = ("backend", "tests", "scripts", "frontend")
IMPORT_PATTERN = re.compile(
    r"scripts/lai_runner|from scripts\.lai_runner|import scripts\.lai_runner"
)


def test_scripts_lai_runner_file_is_removed():
    assert not (REPO_ROOT / "scripts" / "lai_runner.py").exists(), (
        "scripts/lai_runner.py should have been deleted in step 22a. "
        "Restore the deletion or update this invariant test if the legacy "
        "script is being re-introduced for a documented reason."
    )


def test_no_source_tree_imports_or_path_references():
    """`git grep` the deletion-PR invariant — must return zero hits.

    Excludes this test file itself via a pathspec exclusion since it
    legitimately contains the pattern as the invariant under test.
    """
    cmd = [
        "git",
        "grep",
        "-nE",
        "scripts/lai_runner|from scripts.lai_runner|import scripts.lai_runner",
        "--",
        *SEARCH_ROOTS,
        ":(exclude)tests/backend/test_scripts_lai_runner_removed.py",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT, check=False)
    # `git grep` exits 1 on zero matches, 0 on at least one match, 128 on error.
    # Fail loudly on a broken git environment rather than skipping: a silent skip
    # would let the removal invariant go unverified and read as "passed".
    assert result.returncode != 128, (
        f"git grep failed (exit 128) — the removal invariant could not be "
        f"checked: {result.stderr.strip()}"
    )
    assert result.returncode == 1, (
        "Found references to the deleted scripts/lai_runner.py — clean these "
        "up before re-asserting the step-22a removal invariant:\n" + result.stdout
    )
    assert result.stdout == ""


def test_no_untracked_references_in_source_trees():
    """Belt-and-suspenders: walk the source trees with stdlib for untracked files."""
    self_path = Path(__file__).resolve()
    hits: list[str] = []
    for root_name in SEARCH_ROOTS:
        root = REPO_ROOT / root_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.resolve() == self_path:
                # This test file legitimately contains the pattern as the
                # invariant under test — skip self.
                continue
            # Skip caches, build artifacts, binaries
            if any(
                part in {"__pycache__", "node_modules", ".pytest_cache", "dist", "build"}
                for part in path.parts
            ):
                continue
            binary_suffixes = {
                ".pyc",
                ".pyo",
                ".so",
                ".db",
                ".sqlite",
                ".png",
                ".jpg",
                ".gz",
                ".zip",
            }
            if path.suffix in binary_suffixes:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except (OSError, UnicodeDecodeError):
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if IMPORT_PATTERN.search(line):
                    hits.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not hits, (
        "Found references to the deleted scripts/lai_runner.py (including "
        "untracked files):\n" + "\n".join(hits)
    )
