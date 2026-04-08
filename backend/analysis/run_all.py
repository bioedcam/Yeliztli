"""Post-annotation analysis orchestrator.

Runs all analysis modules after annotation completes, populating the
findings table so the dashboard High-Confidence Findings section has data.

Each module is run independently — a failure in one does not block others.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy import Engine

    from backend.db.connection import DBRegistry

logger = logging.getLogger(__name__)


def run_all_analyses(
    sample_engine: Engine,
    registry: DBRegistry,
    *,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> dict[str, int | str]:
    """Run every analysis module and store findings.

    Args:
        sample_engine: Per-sample SQLite engine with annotated_variants.
        registry: DB registry (provides reference_engine and settings).
        progress_callback: Optional ``(module_name, index, total)`` callback.

    Returns:
        Dict mapping module name to findings count (int) or error string.
    """
    results: dict[str, int | str] = {}
    modules = _get_modules()

    for i, (name, runner) in enumerate(modules):
        if progress_callback:
            progress_callback(name, i, len(modules))
        try:
            count = runner(sample_engine, registry)
            results[name] = count
            logger.info("analysis_module_complete", extra={"module": name, "findings": count})
        except Exception:
            logger.exception("analysis_module_failed", extra={"module": name})
            results[name] = "error"

    return results


def _get_modules() -> list[tuple[str, Callable]]:
    """Return (name, runner_fn) pairs for all analysis modules.

    Each runner takes (sample_engine, registry) and returns findings count.
    Uses lazy imports to avoid circular dependencies and keep startup fast.
    """
    return [
        ("cancer", _run_cancer),
        ("carrier_status", _run_carrier),
        ("cardiovascular", _run_cardiovascular),
        ("pharmacogenomics", _run_pharma),
        ("nutrigenomics", _run_nutrigenomics),
        ("traits", _run_traits),
        ("apoe", _run_apoe),
        ("ancestry", _run_ancestry),
        ("sleep", _run_sleep),
        ("fitness", _run_fitness),
        ("skin", _run_skin),
        ("methylation", _run_methylation),
        ("allergy", _run_allergy),
        ("gene_health", _run_gene_health),
        ("rare_variants", _run_rare_variants),
    ]


# ── Module runners ──────────────────────────────────────────────────
# Each function encapsulates one module's analysis logic,
# mirroring the POST /run endpoint but without HTTP concerns.


def _run_cancer(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.cancer import (
        extract_cancer_variants,
        load_cancer_panel,
        store_cancer_findings,
    )
    from backend.analysis.cancer_prs import (
        load_cancer_prs_weights,
        run_cancer_prs,
        store_cancer_prs_findings,
    )
    from backend.analysis.prs import get_inferred_ancestry

    panel = load_cancer_panel()
    result = extract_cancer_variants(panel, sample_engine)
    count = store_cancer_findings(result, sample_engine)

    weight_sets = load_cancer_prs_weights()
    inferred_ancestry = get_inferred_ancestry(sample_engine)
    prs_result = run_cancer_prs(weight_sets, sample_engine, inferred_ancestry=inferred_ancestry)
    count += store_cancer_prs_findings(prs_result, sample_engine)
    return count


def _run_carrier(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.carrier_status import (
        extract_carrier_variants,
        load_carrier_panel,
        store_carrier_findings,
    )

    panel = load_carrier_panel()
    result = extract_carrier_variants(panel, sample_engine)
    return store_carrier_findings(result, sample_engine)


def _run_cardiovascular(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.cardiovascular import (
        extract_cardiovascular_variants,
        load_cardiovascular_panel,
        store_cardiovascular_findings,
    )

    panel = load_cardiovascular_panel()
    result = extract_cardiovascular_variants(panel, sample_engine)
    return store_cardiovascular_findings(result, sample_engine)


def _run_pharma(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.pharmacogenomics import (
        call_all_star_alleles,
        generate_prescribing_alerts,
        store_prescribing_alerts,
    )

    star_allele_results = call_all_star_alleles(registry.reference_engine, sample_engine)
    alerts = generate_prescribing_alerts(star_allele_results, registry.reference_engine)
    return store_prescribing_alerts(alerts, sample_engine)


def _run_nutrigenomics(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.nutrigenomics import (
        load_nutrigenomics_panel,
        score_nutrigenomics_pathways,
        store_nutrigenomics_findings,
    )

    panel = load_nutrigenomics_panel()
    result = score_nutrigenomics_pathways(panel, sample_engine, registry.reference_engine)
    return store_nutrigenomics_findings(result, sample_engine)


def _run_traits(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.traits import (
        load_traits_panel,
        score_traits_pathways,
        store_traits_findings,
    )

    panel = load_traits_panel()
    result = score_traits_pathways(panel, sample_engine, registry.reference_engine)
    return store_traits_findings(result, sample_engine)


def _run_apoe(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.apoe import (
        determine_apoe_genotype,
        store_apoe_three_findings,
    )

    result = determine_apoe_genotype(sample_engine)
    return store_apoe_three_findings(result, sample_engine)


def _run_ancestry(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.ancestry import run_ancestry_inference

    result = run_ancestry_inference(sample_engine)
    # run_ancestry_inference already calls store_ancestry_findings internally;
    # only count as 1 finding when coverage was sufficient (finding stored)
    return 1 if result.is_sufficient and result.top_population else 0


def _run_sleep(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.sleep import (
        load_sleep_panel,
        score_sleep_pathways,
        store_sleep_findings,
    )

    panel = load_sleep_panel()
    result = score_sleep_pathways(panel, sample_engine, registry.reference_engine)
    return store_sleep_findings(result, sample_engine)


def _run_fitness(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.fitness import (
        load_fitness_panel,
        score_fitness_pathways,
        store_fitness_findings,
    )

    panel = load_fitness_panel()
    result = score_fitness_pathways(panel, sample_engine, registry.reference_engine)
    return store_fitness_findings(result, sample_engine)


def _run_skin(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.skin import (
        load_skin_panel,
        score_skin_pathways,
        store_skin_findings,
    )

    panel = load_skin_panel()
    result = score_skin_pathways(panel, sample_engine, registry.reference_engine)
    return store_skin_findings(result, sample_engine)


def _run_methylation(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.methylation import (
        load_methylation_panel,
        score_methylation_pathways,
        store_methylation_findings,
    )

    panel = load_methylation_panel()
    result = score_methylation_pathways(panel, sample_engine, registry.reference_engine)
    return store_methylation_findings(result, sample_engine)


def _run_allergy(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.allergy import (
        load_allergy_panel,
        score_allergy_pathways,
        store_allergy_findings,
    )

    panel = load_allergy_panel()
    result = score_allergy_pathways(panel, sample_engine, registry.reference_engine)
    return store_allergy_findings(result, sample_engine)


def _run_gene_health(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.gene_health import (
        load_gene_health_panel,
        score_gene_health_pathways,
        store_gene_health_findings,
    )

    panel = load_gene_health_panel()
    result = score_gene_health_pathways(panel, sample_engine, registry.reference_engine)
    return store_gene_health_findings(result, sample_engine)


def _run_rare_variants(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.rare_variant_finder import (
        RareVariantFilter,
        find_rare_variants,
        store_rare_variant_findings,
    )

    result = find_rare_variants(RareVariantFilter(), sample_engine)
    return store_rare_variant_findings(result, sample_engine)
