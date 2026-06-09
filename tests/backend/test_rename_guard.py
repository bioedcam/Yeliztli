"""Residual rename-guard for the GenomeInsight -> Yeliztli rebrand (Phase 7 hard gate).

Asserts the repository contains NO ``genomeinsight`` reference (case-insensitive)
except an explicit allow-list of:

* one-release back-compat shims (deprecated ``GENOMEINSIGHT_*`` env vars, the legacy
  ``[genomeinsight]`` config table, ``~/.genomeinsight`` data-dir migration, the
  ``genomeinsight_backup_`` download validator),
* immutable published-asset filenames (``genomeinsight_lai_bundle_*.tar.gz`` — kept
  verbatim; only the org-slug in their URLs was rebranded),
* append-only history / published-asset runbook docs, and
* one deliberately-malformed reject-this-input test fixture.

It also asserts the dead ``DBNSFP_PREBUILT_URL`` constant (a different-org URL whose
licensing forbids wiring it up) stays deleted.

WHEN A BACK-COMPAT SHIM IS RETIRED (after its one-release deprecation window), delete
its allow-list entry below so the guard re-tightens. A brand-new ``genomeinsight``
reference that is NOT intentional back-compat should be renamed to Yeliztli, not
added here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Files where ANY ``genomeinsight`` match is allowed: append-only history (CHANGELOG,
# release notes) and bundle-publish runbooks that intentionally record the as-shipped,
# immutable published-asset names/URLs.
FULLY_ALLOWED_FILES = {
    "CHANGELOG.md",
    "docs/lai-bundle-release-runbook.md",
    "docs/lai-bundle-mid-rebalance-runbook.md",
}
FULLY_ALLOWED_PREFIXES = ("docs/release-notes/",)

# This guard file itself necessarily contains ``genomeinsight`` tokens (the allow-list
# below) and the ``DBNSFP_PREBUILT_URL`` name (in its assertion). It is excluded from
# both greps.
SELF = "tests/backend/test_rename_guard.py"

# Per-file allow-list: a ``genomeinsight`` line in one of these files is permitted iff
# it contains at least one of the listed (case-sensitive) tokens. Anything else fails —
# including a stray ``genomeinsight`` in a file not listed here at all.
ALLOWED_BY_FILE: dict[str, list[str]] = {
    # ── one-release back-compat shims ────────────────────────────────────────────
    "backend/config.py": [
        "GENOMEINSIGHT_",
        ".genomeinsight",
        "[genomeinsight]",
        '"genomeinsight"',
    ],
    "backend/db/manifest.py": ["GENOMEINSIGHT_"],
    "backend/installer.py": [".genomeinsight", "GENOMEINSIGHT_"],
    "backend/main.py": [".genomeinsight", "GENOMEINSIGHT_"],
    "backend/tasks/huey_tasks.py": [".genomeinsight", "GENOMEINSIGHT_"],
    "backend/api/routes/backup.py": ["genomeinsight_backup_"],
    "docs/setup-guide.md": ["[genomeinsight]"],
    "tests/backend/test_config.py": ["GENOMEINSIGHT_", "[genomeinsight]", ".genomeinsight"],
    "tests/backend/test_cross_platform.py": [".genomeinsight"],
    "tests/backend/test_setup_api.py": ["[genomeinsight]", "GenomeInsight -> Yeliztli"],
    "tests/backend/test_preferences_api.py": ['"genomeinsight"'],
    "tests/backend/test_backup_api.py": ["genomeinsight_backup_"],
    "tests/e2e/global-setup.ts": ["GENOMEINSIGHT_"],
    # ── immutable published-asset filenames (org-slug rebranded, filename kept) ───
    "backend/db/database_registry.py": ["genomeinsight_lai_bundle"],
    "bundles/manifest.json": ["genomeinsight_lai_bundle"],
    "tests/fixtures/manifest_v2.json": ["genomeinsight_lai_bundle"],
    "tests/backend/test_lai_bundle_registry.py": ["genomeinsight_lai_bundle"],
    "tests/backend/test_database_registry_lai.py": ["genomeinsight_lai_bundle"],
    "tests/backend/test_manifest.py": ["genomeinsight_lai_bundle", "GENOMEINSIGHT_"],
    "docs/setup-update-plan.md": ["genomeinsight_lai_bundle", "GENOMEINSIGHT_"],
    "docs/setup-update-steps.md": ["genomeinsight_lai_bundle", "GENOMEINSIGHT_"],
    # ── ignore-pattern for the still-published legacy asset (kept alongside yeliztli_) ─
    ".gitignore": ["genomeinsight_lai_bundle"],
    # ── Docker named-volume migration note (PR-D) ────────────────────────────────
    "docker-compose.yml": ["genomeinsight-data"],
    # ── deliberately-malformed reject-this-input fixture (must NOT be a valid tag) ─
    "tests/fixtures/sample_not_23andme.vcf": ["GenomeInsightTest"],
}


def _git_grep(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", "grep", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    # git grep exit code: 0 = matches, 1 = no matches, >1 = error.
    if result.returncode > 1:
        raise RuntimeError(f"git grep failed: {result.stderr}")
    return result.stdout.splitlines()


def test_no_unexpected_genomeinsight_references() -> None:
    """The tree contains only allow-listed ``genomeinsight`` references."""
    lines = _git_grep(
        "-niI",
        "genomeinsight",
        "--",
        ".",
        f":!{SELF}",
        ":!RENAME-TO-YELIZTLI.md",
    )

    violations: list[str] = []
    for line in lines:
        path, _, content = line.partition(":")
        _lineno, _, text = content.partition(":")
        if path in FULLY_ALLOWED_FILES or path.startswith(FULLY_ALLOWED_PREFIXES):
            continue
        allowed = ALLOWED_BY_FILE.get(path)
        if allowed and any(token in text for token in allowed):
            continue
        violations.append(f"{path}: {text.strip()}")

    assert not violations, (
        "Unexpected `genomeinsight` reference(s) (rebrand residual). Rename to Yeliztli, "
        "or — if this is an intentional one-release back-compat shim — add it to the "
        "allow-list in tests/backend/test_rename_guard.py:\n  " + "\n  ".join(violations)
    )


def test_dead_dbnsfp_prebuilt_url_stays_deleted() -> None:
    """R8: the dead, different-org ``DBNSFP_PREBUILT_URL`` must stay deleted."""
    lines = _git_grep("-nI", "DBNSFP_PREBUILT_URL", "--", ".", f":!{SELF}")
    assert not lines, (
        "DBNSFP_PREBUILT_URL must stay deleted (R8: a different-org dead URL; dbNSFP "
        "redistribution licensing forbids wiring it up):\n  " + "\n  ".join(lines)
    )
