"""LAI bundle staleness soft-gate (Step 23, Plan §6.7).

When the installed ``lai_bundle`` is older than ``v2.0.0`` *and* the
requested sample carries an AncestryDNA contribution (single-source or
merged), the LAI endpoints flag ``degraded_coverage=True`` in their HTTP
200 payload — the gate is advisory, never 423. 23andMe-only samples
never carry the flag regardless of bundle version (Plan §6.7 negative
case).

The helper layer here is intentionally pure: each function takes its
inputs explicitly and never touches the global registry. Routes and
tests call the small wrappers below; the wrappers do the registry
lookups.
"""

from __future__ import annotations

import sqlalchemy as sa
import structlog
from packaging.version import InvalidVersion, Version

from backend.db.connection import get_registry
from backend.db.tables import database_versions, samples

logger = structlog.get_logger(__name__)

# Plan §6.7 — v2.0.0 is the first bundle with full AncestryDNA chromosome
# painting coverage. Anything strictly below is degraded.
_LAI_BUNDLE_V2 = Version("2.0.0")


def file_format_has_ancestrydna(file_format: str | None) -> bool:
    """Return True when ``file_format`` indicates an AncestryDNA contribution.

    Single-source AncestryDNA samples carry ``file_format`` strings like
    ``"ancestrydna_v2.0"`` (Plan §8.7). Merged-sample handling lands in
    Phase 3; until then the only positive case is the literal prefix.
    """
    if not file_format:
        return False
    return file_format.lower().startswith("ancestrydna")


def lai_bundle_below_v2(lai_bundle_version: str | None) -> bool:
    """Return True when ``lai_bundle_version`` parses as ``< v2.0.0``.

    Tolerates a leading ``v`` and ``None``. Unparseable values short-
    circuit to ``False`` — the user-facing surface is the bundle Update
    Manager, not this helper.
    """
    if not lai_bundle_version:
        return False
    try:
        return Version(lai_bundle_version.lstrip("v")) < _LAI_BUNDLE_V2
    except InvalidVersion:
        logger.warning(
            "lai_bundle_version_unparseable",
            recorded_version=lai_bundle_version,
        )
        return False


def is_lai_coverage_degraded(
    file_format: str | None,
    lai_bundle_version: str | None,
) -> bool:
    """Pure predicate combining the file-format and bundle-version gates."""
    return file_format_has_ancestrydna(file_format) and lai_bundle_below_v2(lai_bundle_version)


def _read_installed_lai_version() -> str | None:
    """Read ``database_versions['lai_bundle'].version`` or ``None``."""
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(database_versions.c.version).where(
                database_versions.c.db_name == "lai_bundle"
            )
        ).fetchone()
    return row.version if row else None


def _read_sample_file_format(sample_id: int) -> str | None:
    """Read ``samples.file_format`` for ``sample_id`` from the reference DB."""
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.file_format).where(samples.c.id == sample_id)
        ).fetchone()
    return row.file_format if row else None


def is_degraded_for_sample(sample_id: int) -> bool:
    """Resolve degraded-coverage status for ``sample_id`` against the install.

    Per-sample wrapper used by the per-sample LAI routes. Returns ``False``
    when the sample row is missing — the gate is best-effort advisory.
    """
    file_format = _read_sample_file_format(sample_id)
    if file_format is None:
        return False
    bundle_version = _read_installed_lai_version()
    return is_lai_coverage_degraded(file_format, bundle_version)


def is_degraded_globally() -> bool:
    """True when *any* installed sample would trigger the soft gate.

    Powers the dashboard-mounted ``<AppUpdateBanner>`` (Plan §6.7) — the
    banner surfaces once per install, independent of which sample is
    currently selected. Walks ``samples.file_format`` (reference DB only;
    no per-sample DB opens).
    """
    bundle_version = _read_installed_lai_version()
    if not lai_bundle_below_v2(bundle_version):
        return False
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        rows = conn.execute(sa.select(samples.c.file_format)).fetchall()
    return any(file_format_has_ancestrydna(r.file_format) for r in rows)
