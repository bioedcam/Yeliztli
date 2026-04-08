"""LAI module entry point — local ancestry inference integration.

Provides the high-level interface for running LAI analysis on a sample,
checking availability, and storing results in the sample DB.

LAI is optional and requires:
  - LAI bundle downloaded and extracted (~500 MB)
  - Java 8+ runtime for Beagle phasing

Analysis runs as a Huey background task (15-30 min) and must not block
the API thread.  Progress updates are written to the jobs table for
SSE polling.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

import sqlalchemy as sa
import structlog

from backend.config import get_settings
from backend.db.database_registry import detect_java, validate_lai_bundle
from backend.db.tables import findings, lai_results

logger = structlog.get_logger(__name__)


@dataclass
class LAIResult:
    """Result from LAI analysis."""

    global_ancestry: dict[str, dict]
    chromosome_painting: dict[str, list[dict]]
    metadata: dict
    is_available: bool = True


def is_lai_available() -> bool:
    """Check whether LAI analysis can be run.

    Returns True only if both the LAI bundle is downloaded/extracted
    AND Java 8+ is available on PATH.
    """
    settings = get_settings()
    bundle_path = settings.resolved_lai_bundle_path
    return validate_lai_bundle(bundle_path) and detect_java()


def run_lai_analysis(
    sample_id: int,
    sample_engine: sa.Engine,
    progress_callback: Callable[[str, float], None] | None = None,
) -> LAIResult:
    """Run LAI analysis on a sample.

    Reads genotypes from the sample DB, runs the full LAI pipeline,
    stores results in the lai_results and findings tables.

    Args:
        sample_id: Sample ID for progress tracking.
        sample_engine: SQLAlchemy engine for the sample database.
        progress_callback: Optional function(message, fraction) for updates.

    Returns:
        LAIResult with global ancestry and chromosome painting.

    Raises:
        RuntimeError: If LAI bundle or Java is unavailable.
    """
    settings = get_settings()
    bundle_path = settings.resolved_lai_bundle_path

    if not validate_lai_bundle(bundle_path):
        raise RuntimeError("LAI bundle is not downloaded or incomplete")
    if not detect_java():
        raise RuntimeError("Java 8+ is required for LAI analysis")

    # Ensure lai_results table exists (CREATE TABLE IF NOT EXISTS)
    _ensure_lai_tables(sample_engine)

    # Read genotypes from sample DB
    from backend.db.tables import raw_variants

    with sample_engine.connect() as conn:
        rows = conn.execute(sa.select(raw_variants)).fetchall()

    genotypes = [
        {"rsid": r.rsid, "chrom": r.chrom, "pos": r.pos, "genotype": r.genotype} for r in rows
    ]

    if not genotypes:
        raise RuntimeError("No genotypes found in sample database")

    # Set up output directory
    output_dir = settings.data_dir / "lai_work" / f"sample_{sample_id}"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Run LAI pipeline
    from backend.analysis.lai_runner import LAIRunner

    runner = LAIRunner(
        bundle_path=str(bundle_path),
        java_mem=settings.lai_java_mem,
    )
    runner_result = runner.run(
        genotypes=genotypes,
        output_dir=str(output_dir),
        progress_callback=progress_callback,
        cleanup=True,
    )

    # Store results
    _store_lai_results(sample_engine, runner_result)

    return LAIResult(
        global_ancestry=runner_result.global_ancestry,
        chromosome_painting=runner_result.chromosome_painting,
        metadata=runner_result.metadata,
    )


def _ensure_lai_tables(engine: sa.Engine) -> None:
    """Create the lai_results table if it doesn't exist."""
    lai_results.create(engine, checkfirst=True)


def _store_lai_results(
    engine: sa.Engine,
    runner_result: object,
) -> None:
    """Store LAI results in both lai_results and findings tables."""
    with engine.begin() as conn:
        # Store full results in lai_results table
        conn.execute(
            lai_results.insert().values(
                global_ancestry_json=json.dumps(runner_result.global_ancestry),
                chromosome_painting_json=json.dumps(runner_result.chromosome_painting),
                metadata_json=json.dumps(runner_result.metadata),
            )
        )

        # Determine top population
        top_pop = ""
        top_frac = 0.0
        for pop, info in runner_result.global_ancestry.items():
            if info["fraction"] > top_frac:
                top_frac = info["fraction"]
                top_pop = pop

        # Build summary for findings
        ancestry_parts = []
        for pop in sorted(
            runner_result.global_ancestry.keys(),
            key=lambda p: runner_result.global_ancestry[p]["fraction"],
            reverse=True,
        ):
            info = runner_result.global_ancestry[pop]
            if info["percentage"] >= 1.0:
                ancestry_parts.append(f"{info['display_name']}: {info['percentage']}%")

        finding_text = (
            f"Local ancestry inference: {', '.join(ancestry_parts[:4])}"
            if ancestry_parts
            else "Local ancestry inference completed"
        )

        detail = {
            "top_population": top_pop,
            "global_ancestry": runner_result.global_ancestry,
            "chromosomes_analyzed": runner_result.metadata.get("chromosomes_analyzed", 0),
            "runtime_seconds": runner_result.metadata.get("runtime_seconds", 0),
        }

        conn.execute(
            findings.insert().values(
                module="ancestry",
                category="local_ancestry",
                evidence_level=2,
                finding_text=finding_text,
                detail_json=json.dumps(detail),
            )
        )

    logger.info(
        "lai_results_stored",
        top_population=top_pop,
        chromosomes=runner_result.metadata.get("chromosomes_analyzed", 0),
    )
