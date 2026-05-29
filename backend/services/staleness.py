"""Sample-annotation staleness service (Plan §7.4 step 3, ADNA-00c part 4).

``is_sample_stale(sample_id)`` returns ``True`` when the bundle recorded in
the per-sample ``annotation_state`` row has a strictly lower **major**
``packaging.version.Version`` than the installed ``vep_bundle``. Minor or
patch differences are not stale.

Missing-state fallback (Plan §7.4): when the per-sample DB has no
``annotation_state`` table, the ``vep_bundle_version`` row is absent, or
its value cannot be parsed as a semver, the sample is treated as
``v1.0.0`` and a structured ``annotation_state_missing`` warning is
emitted. The helper never raises on a malformed per-sample DB — the gate
(step 12's ``require_fresh_sample``) is the user-facing surface, not this
function.
"""

from __future__ import annotations

import sqlalchemy as sa
import structlog
from packaging.version import InvalidVersion, Version

from backend.db.connection import get_registry
from backend.db.tables import annotation_state, database_versions, samples

logger = structlog.get_logger(__name__)

# Per Plan §7.4 — every pre-Phase-0 sample is treated as having been
# annotated against the v1.0.0 bundle.
_FALLBACK_SAMPLE_VERSION = "v1.0.0"


def _coerce_major(raw: str | None) -> int | None:
    """Return the semver major of ``raw`` or ``None`` when unparseable."""
    if not raw:
        return None
    try:
        return Version(raw.lstrip("v")).major
    except InvalidVersion:
        return None


def _read_installed_major() -> int | None:
    """Read ``database_versions['vep_bundle'].version``'s semver major.

    Returns ``None`` when the ``database_versions`` table is absent or the
    reference DB is unreachable — e.g. a fresh install before any bundle has
    been recorded. Mirrors the defensive handling in
    ``_read_sample_bundle_version``; ``is_sample_stale`` treats ``None`` as
    "decline to gate".
    """
    registry = get_registry()
    try:
        with registry.reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions.c.version).where(
                    database_versions.c.db_name == "vep_bundle"
                )
            ).fetchone()
    except sa.exc.OperationalError as exc:
        logger.warning(
            "database_versions_unreadable",
            reason="table_or_db_unreachable",
            error=str(exc),
        )
        return None
    return _coerce_major(row.version if row else None)


def _read_sample_bundle_version(sample_id: int) -> str:
    """Return the sample's recorded ``vep_bundle_version`` or the fallback.

    Emits a structured ``annotation_state_missing`` warning when any of
    these defensive paths fire: sample row missing from reference DB,
    per-sample DB unreachable, ``annotation_state`` table missing, or the
    reserved ``vep_bundle_version`` row absent.
    """
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.db_path).where(samples.c.id == sample_id)
        ).fetchone()
    if row is None:
        logger.warning(
            "annotation_state_missing",
            sample_id=sample_id,
            reason="sample_row_missing",
        )
        return _FALLBACK_SAMPLE_VERSION

    sample_db = registry.settings.data_dir / row.db_path
    try:
        sample_engine = registry.get_sample_engine(sample_db)
        with sample_engine.connect() as conn:
            value_row = conn.execute(
                sa.select(annotation_state.c.value).where(
                    annotation_state.c.key == "vep_bundle_version"
                )
            ).fetchone()
    except sa.exc.OperationalError as exc:
        logger.warning(
            "annotation_state_missing",
            sample_id=sample_id,
            reason="table_or_db_unreachable",
            error=str(exc),
        )
        return _FALLBACK_SAMPLE_VERSION

    if value_row is None or not value_row.value:
        logger.warning(
            "annotation_state_missing",
            sample_id=sample_id,
            reason="vep_bundle_version_row_missing",
        )
        return _FALLBACK_SAMPLE_VERSION

    return value_row.value


def is_sample_stale(sample_id: int) -> bool:
    """Return ``True`` when ``sample_id``'s bundle major < installed major.

    Comparison is on ``packaging.version.Version.major`` only — minor and
    patch differences are not stale (Plan §7.4 step 3). The
    missing-state fallback treats a per-sample DB without an
    ``annotation_state.vep_bundle_version`` row as ``v1.0.0``.
    """
    installed_major = _read_installed_major()
    if installed_major is None:
        # No installed-version row to compare against — log and decline to
        # gate. The bundle-update flow (not this service) is the user's
        # path back to a known state.
        logger.warning(
            "vep_bundle_version_unreadable",
            sample_id=sample_id,
        )
        return False

    sample_raw = _read_sample_bundle_version(sample_id)
    sample_major = _coerce_major(sample_raw)
    if sample_major is None:
        logger.warning(
            "annotation_state_missing",
            sample_id=sample_id,
            reason="malformed_recorded_version",
            recorded_version=sample_raw,
        )
        sample_major = _coerce_major(_FALLBACK_SAMPLE_VERSION)

    return sample_major < installed_major
