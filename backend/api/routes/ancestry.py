"""Ancestry inference API endpoints.

Implements the API layer for P3-23 (ancestry PCA projection),
P3-24 (admixture fraction computation), P3-25 (PCA coordinates
for visualization), and P3-33 (haplogroup assignments).
Provides endpoints to run ancestry inference and retrieve results.
"""

from __future__ import annotations

import json
from functools import lru_cache

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.db.connection import get_registry
from backend.db.tables import findings, haplogroup_assignments, samples

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/analysis/ancestry", tags=["ancestry"])


# ── Response models ───────────────────────────────────────────────────────


class PopulationDistance(BaseModel):
    """Distance to a reference population centroid."""

    population: str
    distance: float


class AncestryFindingResponse(BaseModel):
    """Ancestry inference result."""

    top_population: str
    pc_scores: list[float]
    population_distances: dict[str, float]
    admixture_fractions: dict[str, float]
    population_ranking: list[PopulationDistance]
    snps_used: int
    snps_total: int
    coverage_fraction: float
    projection_time_ms: float
    is_sufficient: bool
    evidence_level: int
    finding_text: str
    confidence: float = 0.0
    missing_aim_rate: float = 0.0
    admixture_method: str = "nnls"
    n_pcs_used: int = 0
    nnls_fractions: dict[str, float] | None = None
    knn_fractions: dict[str, float] | None = None


class AncestryRunResponse(BaseModel):
    """Response from running ancestry inference."""

    top_population: str
    admixture_fractions: dict[str, float]
    snps_used: int
    snps_total: int
    coverage_fraction: float
    is_sufficient: bool


class PCACoordinatesResponse(BaseModel):
    """PCA coordinates for scatter plot visualization (P3-25)."""

    user: list[float]
    reference_samples: dict[str, list[list[float]]]
    centroids: dict[str, list[float]]
    population_labels: dict[str, str]
    n_components: int
    pc_labels: list[str]
    top_population: str


class HaplogroupTraversalStepResponse(BaseModel):
    """A single step in the haplogroup traversal path."""

    haplogroup: str
    snps_present: int
    snps_total: int


class HaplogroupAssignmentResponse(BaseModel):
    """A haplogroup assignment for a single tree (mt or Y)."""

    type: str
    haplogroup: str
    confidence: float
    defining_snps_present: int
    defining_snps_total: int
    traversal_path: list[HaplogroupTraversalStepResponse]
    finding_text: str


class HaplogroupResponse(BaseModel):
    """Haplogroup assignments for a sample (P3-33)."""

    assignments: list[HaplogroupAssignmentResponse]


class HaplogroupRunResponse(BaseModel):
    """Response from running haplogroup assignment."""

    assignments: list[HaplogroupAssignmentResponse]


class LAIStatusResponse(BaseModel):
    """LAI bundle and Java availability status."""

    bundle_downloaded: bool
    java_available: bool
    lai_available: bool
    message: str


class LAITriggerResponse(BaseModel):
    """Response from triggering LAI analysis."""

    job_id: str
    message: str


class LAIResultResponse(BaseModel):
    """LAI analysis results."""

    global_ancestry: dict
    chromosome_painting: dict
    metadata: dict
    created_at: str


class LAIProgressResponse(BaseModel):
    """LAI analysis progress."""

    job_id: str
    status: str
    progress_pct: float
    message: str
    error: str | None = None


# ── Helpers ───────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _get_ancestry_bundle():
    """Load and cache the ancestry PCA bundle (static data)."""
    from backend.analysis.ancestry import load_ancestry_bundle

    return load_ancestry_bundle()


def _get_sample_engine(sample_id: int) -> sa.Engine:
    """Get a sample database engine by sample ID."""
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.db_path).where(samples.c.id == sample_id)
        ).fetchone()
    if not row:
        raise HTTPException(404, detail=f"Sample {sample_id} not found")
    sample_db_path = registry.settings.data_dir / row.db_path
    return registry.get_sample_engine(sample_db_path)


# ── Endpoints ─────────────────────────────────────────────────────────────


@router.get("/findings")
def get_ancestry_findings(
    sample_id: int = Query(..., description="Sample ID"),
) -> AncestryFindingResponse | None:
    """Get ancestry inference results for a sample.

    Returns the most recent PCA projection finding, or null if
    ancestry inference has not been run yet.
    """
    sample_engine = _get_sample_engine(sample_id)

    with sample_engine.connect() as conn:
        # Fetch PCA projection finding (has pc_scores, distances, ranking)
        pca_row = conn.execute(
            sa.select(findings)
            .where(
                findings.c.module == "ancestry",
                findings.c.category == "pca_projection",
            )
            .order_by(findings.c.id.desc())
            .limit(1)
        ).fetchone()

        # Fetch NNLS admixture finding (has confidence, method, fractions)
        nnls_row = conn.execute(
            sa.select(findings)
            .where(
                findings.c.module == "ancestry",
                findings.c.category == "nnls_admixture",
            )
            .order_by(findings.c.id.desc())
            .limit(1)
        ).fetchone()

        # Fetch kNN admixture finding
        knn_row = conn.execute(
            sa.select(findings)
            .where(
                findings.c.module == "ancestry",
                findings.c.category == "knn_admixture",
            )
            .order_by(findings.c.id.desc())
            .limit(1)
        ).fetchone()

    if pca_row is None:
        return None

    pca_detail = json.loads(pca_row.detail_json) if pca_row.detail_json else {}
    nnls_detail = json.loads(nnls_row.detail_json) if nnls_row and nnls_row.detail_json else {}
    knn_detail = json.loads(knn_row.detail_json) if knn_row and knn_row.detail_json else {}

    # Prefer NNLS fractions over PCA-derived fractions
    admixture_fractions = (
        nnls_detail.get("admixture_fractions")
        or pca_detail.get("admixture_fractions", {})
    )
    top_population = (
        nnls_detail.get("top_population")
        or pca_detail.get("top_population", "")
    )

    # Use NNLS finding text if available, otherwise PCA
    finding_text = (nnls_row.finding_text if nnls_row else None) or pca_row.finding_text or ""

    return AncestryFindingResponse(
        top_population=top_population,
        pc_scores=pca_detail.get("pc_scores", []),
        population_distances=pca_detail.get("population_distances", {}),
        admixture_fractions=admixture_fractions,
        population_ranking=[
            PopulationDistance(**p)
            for p in pca_detail.get("population_ranking", [])
        ],
        snps_used=pca_detail.get("snps_used", 0),
        snps_total=pca_detail.get("snps_total", 0),
        coverage_fraction=pca_detail.get("coverage_fraction", 0.0),
        projection_time_ms=pca_detail.get("projection_time_ms", 0.0),
        is_sufficient=pca_detail.get("is_sufficient", False),
        evidence_level=pca_row.evidence_level or 2,
        finding_text=finding_text,
        confidence=nnls_detail.get("confidence", 0.0),
        missing_aim_rate=pca_detail.get("missing_aim_rate", 0.0),
        admixture_method=nnls_detail.get("admixture_method") or ("nnls" if nnls_row else "idw"),
        n_pcs_used=pca_detail.get("n_pcs_used", 0),
        nnls_fractions=nnls_detail.get("admixture_fractions"),
        knn_fractions=knn_detail.get("admixture_fractions"),
    )


@router.post("/run")
def run_ancestry(
    sample_id: int = Query(..., description="Sample ID"),
) -> AncestryRunResponse:
    """Run ancestry inference for a sample.

    Projects the sample's genotypes onto pre-computed PCA space
    and classifies ancestry by nearest centroid.
    """
    sample_engine = _get_sample_engine(sample_id)

    from backend.analysis.ancestry import run_ancestry_inference

    result = run_ancestry_inference(sample_engine)

    return AncestryRunResponse(
        top_population=result.top_population,
        admixture_fractions=result.admixture_fractions,
        snps_used=result.snps_used,
        snps_total=result.snps_total,
        coverage_fraction=result.coverage_fraction,
        is_sufficient=result.is_sufficient,
    )


@router.get("/pca-coordinates")
def get_pca_coordinates_endpoint(
    sample_id: int = Query(..., description="Sample ID"),
) -> PCACoordinatesResponse | None:
    """Get PCA coordinates for scatter plot visualization (P3-25).

    Returns the user's projected PCA coordinates alongside reference
    panel sample coordinates for rendering a PCA scatter plot.
    Requires ancestry inference to have been run first.
    """
    sample_engine = _get_sample_engine(sample_id)

    with sample_engine.connect() as conn:
        row = conn.execute(
            sa.select(findings)
            .where(
                findings.c.module == "ancestry",
                findings.c.category == "pca_projection",
            )
            .order_by(findings.c.id.desc())
            .limit(1)
        ).fetchone()

    if row is None:
        return None

    detail = json.loads(row.detail_json) if row.detail_json else {}
    user_pc_scores = detail.get("pc_scores", [])
    top_population = detail.get("top_population", "")

    if not user_pc_scores:
        return None

    from backend.analysis.ancestry import (
        AncestryResult,
        get_pca_coordinates,
    )

    bundle = _get_ancestry_bundle()

    result = AncestryResult(
        pc_scores=user_pc_scores,
        top_population=top_population,
        population_distances=detail.get("population_distances", {}),
        admixture_fractions=detail.get("admixture_fractions", {}),
        snps_used=detail.get("snps_used", 0),
        snps_total=detail.get("snps_total", 0),
        coverage_fraction=detail.get("coverage_fraction", 0.0),
        projection_time_ms=detail.get("projection_time_ms", 0.0),
        is_sufficient=detail.get("is_sufficient", False),
    )

    pca_coords = get_pca_coordinates(bundle, result)

    return PCACoordinatesResponse(
        user=pca_coords.user,
        reference_samples=pca_coords.reference_samples,
        centroids=pca_coords.centroids,
        population_labels=pca_coords.population_labels,
        n_components=pca_coords.n_components,
        pc_labels=pca_coords.pc_labels,
        top_population=top_population,
    )


# ── Haplogroup endpoints (P3-33) ─────────────────────────────────────────


def _build_haplogroup_assignment_response(
    ha_row: sa.Row,
    finding_row: sa.Row | None,
) -> HaplogroupAssignmentResponse:
    """Build a HaplogroupAssignmentResponse from DB rows."""
    traversal_path: list[HaplogroupTraversalStepResponse] = []
    finding_text = ""

    if finding_row and finding_row.detail_json:
        detail = json.loads(finding_row.detail_json)
        traversal_path = [
            HaplogroupTraversalStepResponse(
                haplogroup=step.get("haplogroup", ""),
                snps_present=step.get("snps_present", 0),
                snps_total=step.get("snps_total", 0),
            )
            for step in detail.get("traversal_path", [])
        ]
        finding_text = finding_row.finding_text or ""

    return HaplogroupAssignmentResponse(
        type=ha_row.type,
        haplogroup=ha_row.haplogroup,
        confidence=ha_row.confidence or 0.0,
        defining_snps_present=ha_row.defining_snps_present or 0,
        defining_snps_total=ha_row.defining_snps_total or 0,
        traversal_path=traversal_path,
        finding_text=finding_text,
    )


@router.get("/haplogroups")
def get_haplogroup_assignments(
    sample_id: int = Query(..., description="Sample ID"),
) -> HaplogroupResponse:
    """Get haplogroup assignments for a sample (P3-33).

    Returns mt and/or Y haplogroup assignments with traversal paths
    and confidence scores. Empty list if haplogroup assignment has not
    been run.
    """
    sample_engine = _get_sample_engine(sample_id)

    with sample_engine.connect() as conn:
        ha_rows = conn.execute(
            sa.select(haplogroup_assignments).order_by(haplogroup_assignments.c.type)
        ).fetchall()

        if not ha_rows:
            return HaplogroupResponse(assignments=[])

        assignments = []
        for ha_row in ha_rows:
            category = f"haplogroup_{ha_row.type}"
            finding_row = conn.execute(
                sa.select(findings)
                .where(
                    findings.c.module == "ancestry",
                    findings.c.category == category,
                )
                .order_by(findings.c.id.desc())
                .limit(1)
            ).fetchone()

            assignments.append(_build_haplogroup_assignment_response(ha_row, finding_row))

    return HaplogroupResponse(assignments=assignments)


@router.post("/haplogroups/run")
def run_haplogroup(
    sample_id: int = Query(..., description="Sample ID"),
) -> HaplogroupRunResponse:
    """Run haplogroup assignment for a sample.

    Loads the haplogroup bundle, runs tree-walk assignment for mtDNA
    (and Y-chromosome if XY), and stores results in the
    haplogroup_assignments table and findings.
    """
    sample_engine = _get_sample_engine(sample_id)

    from backend.analysis.ancestry import run_haplogroup_assignment

    results = run_haplogroup_assignment(sample_engine)

    assignments = []
    for result in results:
        if not result.traversal_path:
            logger.debug(
                "haplogroup_result_skipped",
                tree_type=result.tree_type,
                reason="empty_traversal_path",
            )
            continue
        tree_label = "Mitochondrial" if result.tree_type == "mt" else "Y-chromosome"
        finding_text = (
            f"{tree_label} haplogroup: {result.haplogroup} "
            f"({result.defining_snps_present}/{result.defining_snps_total} "
            f"defining SNPs matched, {result.confidence:.0%} confidence)"
        )
        assignments.append(
            HaplogroupAssignmentResponse(
                type=result.tree_type,
                haplogroup=result.haplogroup,
                confidence=result.confidence,
                defining_snps_present=result.defining_snps_present,
                defining_snps_total=result.defining_snps_total,
                traversal_path=[
                    HaplogroupTraversalStepResponse(
                        haplogroup=step.haplogroup,
                        snps_present=step.snps_present,
                        snps_total=step.snps_total,
                    )
                    for step in result.traversal_path
                ],
                finding_text=finding_text,
            )
        )

    return HaplogroupRunResponse(assignments=assignments)


# ── LAI status endpoint ────────────────────────────────────────────────────


@router.get("/lai/status")
def get_lai_status() -> LAIStatusResponse:
    """Check LAI bundle and Java availability.

    Returns whether the LAI bundle is downloaded and extracted,
    whether Java is available, and whether LAI analysis can be run.
    """
    from backend.config import get_settings
    from backend.db.database_registry import detect_java, validate_lai_bundle

    settings = get_settings()
    lai_dir = settings.data_dir / "lai_bundle"
    bundle_downloaded = validate_lai_bundle(lai_dir)
    java_available = detect_java()
    lai_available = bundle_downloaded and java_available

    if lai_available:
        message = "Chromosome painting is available."
    elif not bundle_downloaded and not java_available:
        message = (
            "LAI bundle not downloaded and Java not found. "
            "Download the LAI bundle (~500 MB) and install Java 8+ to enable chromosome painting."
        )
    elif not bundle_downloaded:
        message = "LAI bundle not downloaded. Download it (~500 MB) to enable chromosome painting."
    else:
        message = (
            "Java 8+ is required for chromosome-level ancestry analysis. "
            "Please install Java and restart."
        )

    return LAIStatusResponse(
        bundle_downloaded=bundle_downloaded,
        java_available=java_available,
        lai_available=lai_available,
        message=message,
    )


@router.post("/lai/{sample_id}")
def trigger_lai_analysis(sample_id: int) -> LAITriggerResponse:
    """Trigger LAI analysis for a sample.

    Creates a background job and returns the job_id for progress polling.
    Returns 404 if LAI bundle is not downloaded, 503 if Java unavailable.
    """
    from backend.config import get_settings
    from backend.db.database_registry import detect_java, validate_lai_bundle

    settings = get_settings()
    lai_dir = settings.resolved_lai_bundle_path

    if not validate_lai_bundle(lai_dir):
        raise HTTPException(
            404,
            detail="LAI bundle not downloaded. Download it from Settings to enable.",
        )
    if not detect_java():
        raise HTTPException(
            503,
            detail="Java 8+ is required for LAI analysis. Please install Java.",
        )

    # Verify sample exists
    _get_sample_engine(sample_id)

    from backend.tasks.huey_tasks import create_lai_job, run_lai_task

    try:
        job_id = create_lai_job(sample_id)
    except ValueError as exc:
        raise HTTPException(409, detail=str(exc)) from exc

    run_lai_task(sample_id, job_id)

    return LAITriggerResponse(
        job_id=job_id,
        message="LAI analysis started. Poll /lai/progress for updates.",
    )


@router.get("/lai/{sample_id}/results")
def get_lai_results(sample_id: int) -> LAIResultResponse | None:
    """Get LAI results for a sample.

    Returns the most recent LAI results, or null if LAI has not been run.
    """
    from backend.db.tables import lai_results

    sample_engine = _get_sample_engine(sample_id)

    # Ensure table exists before querying
    lai_results.create(sample_engine, checkfirst=True)

    with sample_engine.connect() as conn:
        row = conn.execute(
            sa.select(lai_results).order_by(lai_results.c.id.desc()).limit(1)
        ).fetchone()

    if row is None:
        return None

    return LAIResultResponse(
        global_ancestry=json.loads(row.global_ancestry_json),
        chromosome_painting=json.loads(row.chromosome_painting_json),
        metadata=json.loads(row.metadata_json),
        created_at=row.created_at.isoformat() if row.created_at else "",
    )


@router.get("/lai/{sample_id}/progress")
def get_lai_progress(sample_id: int) -> LAIProgressResponse | None:
    """Get LAI analysis progress for a sample.

    Returns the most recent LAI job status, or null if no job exists.
    """
    from backend.db.tables import jobs

    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(jobs)
            .where(
                jobs.c.sample_id == sample_id,
                jobs.c.job_type == "lai_analysis",
            )
            .order_by(jobs.c.created_at.desc())
            .limit(1)
        ).fetchone()

    if row is None:
        return None

    return LAIProgressResponse(
        job_id=row.job_id,
        status=row.status,
        progress_pct=row.progress_pct or 0.0,
        message=row.message or "",
        error=row.error,
    )
