"""Tests for drug lookup API (P3-05) and gene results endpoint.

T3-05: Drug lookup for "clopidogrel" returns CYP2C19 with correct genotype,
recommendation, and call confidence.

Covers:
  - GET /api/analysis/pharma/drugs — List all CPIC drugs
  - GET /api/analysis/pharma/drug/{drug_name} — Drug detail with user genotype
  - GET /api/analysis/pharma/genes?sample_id=N — Per-gene star-allele results
  - Case-insensitive drug name matching
  - Missing drug returns 404
  - Gene with no sample finding (Insufficient / not yet run)
  - Gene with finding includes full effect detail
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import DBRegistry, reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    cpic_guidelines,
    findings,
    reference_metadata,
    samples,
)
from backend.disclaimers import DPYD_FLUOROPYRIMIDINE_CAVEAT

# ── Test data ────────────────────────────────────────────────────────

CPIC_GUIDELINES_DATA = [
    {
        "gene": "CYP2C19",
        "drug": "clopidogrel",
        "phenotype": "Normal Metabolizer",
        "recommendation": "Use label-recommended dosing.",
        "classification": "A",
        "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-clopidogrel-and-cyp2c19/",
    },
    {
        "gene": "CYP2C19",
        "drug": "clopidogrel",
        "phenotype": "Intermediate Metabolizer",
        "recommendation": "Consider alternative antiplatelet therapy.",
        "classification": "A",
        "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-clopidogrel-and-cyp2c19/",
    },
    {
        "gene": "CYP2C19",
        "drug": "clopidogrel",
        "phenotype": "Poor Metabolizer",
        "recommendation": "Use alternative antiplatelet therapy.",
        "classification": "A",
        "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-clopidogrel-and-cyp2c19/",
    },
    {
        "gene": "CYP2D6",
        "drug": "codeine",
        "phenotype": "Normal Metabolizer",
        "recommendation": "Use label-recommended age- or weight-specific dosing.",
        "classification": "A",
        "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-codeine-and-cyp2d6/",
    },
    {
        "gene": "CYP2D6",
        "drug": "codeine",
        "phenotype": "Poor Metabolizer",
        "recommendation": "Avoid codeine use. Alternative analgesics recommended.",
        "classification": "A",
        "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-codeine-and-cyp2d6/",
    },
    {
        "gene": "CYP2C9",
        "drug": "warfarin",
        "phenotype": "Normal Metabolizer",
        "recommendation": "Use label-recommended dosing algorithm.",
        "classification": "A",
        "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-warfarin-and-cyp2c9-and-vkorc1/",
    },
]

# Findings stored by the pharmacogenomics module (P3-04)
SAMPLE_FINDINGS = [
    {
        "module": "pharmacogenomics",
        "category": "prescribing_alert",
        "evidence_level": 4,
        "gene_symbol": "CYP2C19",
        "diplotype": "*1/*2",
        "metabolizer_status": "Intermediate Metabolizer",
        "drug": "clopidogrel",
        "finding_text": (
            "CYP2C19 *1/*2: Intermediate Metabolizer"
            " -- clopidogrel: Consider alternative antiplatelet therapy."
        ),
        "detail_json": json.dumps(
            {
                "recommendation": "Consider alternative antiplatelet therapy.",
                "classification": "A",
                "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-clopidogrel-and-cyp2c19/",
                "call_confidence": "Complete",
                "confidence_note": "All defining rsids present and genotyped.",
                "activity_score": 0.5,
                "ehr_notation": "Intermediate Metabolizer",
                "involved_rsids": ["rs4244285"],
            }
        ),
    },
    {
        "module": "pharmacogenomics",
        "category": "prescribing_alert",
        "evidence_level": 4,
        "gene_symbol": "CYP2D6",
        "diplotype": "*1/*4",
        "metabolizer_status": "Intermediate Metabolizer",
        "drug": "codeine",
        "finding_text": (
            "CYP2D6 *1/*4: Intermediate Metabolizer -- codeine: Use label-recommended dosing."
        ),
        "detail_json": json.dumps(
            {
                "recommendation": "Use label-recommended dosing.",
                "classification": "A",
                "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-codeine-and-cyp2d6/",
                "call_confidence": "Partial",
                "confidence_note": (
                    "SNP-based alleles called; structural variants cannot be excluded."
                ),
                "activity_score": 1.0,
                "ehr_notation": "Intermediate Metabolizer",
                "involved_rsids": ["rs3892097"],
            }
        ),
    },
]


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "samples").mkdir()
    return data_dir


def _setup_client(
    tmp_data_dir: Path,
    guidelines: list[dict],
    sample_findings: list[dict] | None = None,
) -> Generator[tuple[TestClient, int], None, None]:
    """Create TestClient with CPIC guidelines and optional sample findings."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)
    with ref_engine.begin() as conn:
        result = conn.execute(
            samples.insert().values(
                name="test_pharma",
                db_path="samples/sample_1.db",
                file_format="23andme_v5",
                file_hash="hash_pharma",
            )
        )
        sample_id = result.lastrowid

        if guidelines:
            conn.execute(cpic_guidelines.insert(), guidelines)
    ref_engine.dispose()

    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)
    if sample_findings:
        with sample_engine.begin() as conn:
            conn.execute(findings.insert(), sample_findings)
    sample_engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
        patch("backend.api.routes.pharma.get_registry") as mock_reg,
        patch("backend.api.routes.variant_detail.get_registry") as mock_reg2,
        patch("backend.api.routes.annotations_api.get_registry") as mock_reg3,
        patch("backend.api.routes.variants.get_registry") as mock_reg4,
        patch("backend.api.routes.ingest.get_registry") as mock_reg5,
        patch("backend.api.routes.samples.get_registry") as mock_reg6,
    ):
        reset_registry()
        registry = DBRegistry(settings)
        mock_reg.return_value = registry
        mock_reg2.return_value = registry
        mock_reg3.return_value = registry
        mock_reg4.return_value = registry
        mock_reg5.return_value = registry
        mock_reg6.return_value = registry

        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc, sample_id

        registry.dispose_all()
        reset_registry()


@pytest.fixture
def client(tmp_data_dir: Path) -> Generator[tuple[TestClient, int], None, None]:
    """Client with CPIC guidelines and sample findings."""
    yield from _setup_client(tmp_data_dir, CPIC_GUIDELINES_DATA, SAMPLE_FINDINGS)


@pytest.fixture
def client_no_findings(tmp_data_dir: Path) -> Generator[tuple[TestClient, int], None, None]:
    """Client with CPIC guidelines but no sample findings."""
    yield from _setup_client(tmp_data_dir, CPIC_GUIDELINES_DATA)


@pytest.fixture
def client_no_guidelines(tmp_data_dir: Path) -> Generator[tuple[TestClient, int], None, None]:
    """Client with no CPIC guidelines loaded."""
    yield from _setup_client(tmp_data_dir, [])


# ═══════════════════════════════════════════════════════════════════════
# GET /api/analysis/pharma/drugs — List all drugs
# ═══════════════════════════════════════════════════════════════════════


class TestListDrugs:
    def test_returns_all_drugs(self, client: tuple[TestClient, int]):
        tc, _ = client
        resp = tc.get("/api/analysis/pharma/drugs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        drug_names = [item["drug"] for item in data["items"]]
        assert "clopidogrel" in drug_names
        assert "codeine" in drug_names
        assert "warfarin" in drug_names

    def test_drug_has_genes(self, client: tuple[TestClient, int]):
        tc, _ = client
        resp = tc.get("/api/analysis/pharma/drugs")
        data = resp.json()
        clopidogrel = next(i for i in data["items"] if i["drug"] == "clopidogrel")
        assert clopidogrel["genes"] == ["CYP2C19"]
        assert clopidogrel["classification"] == "A"

    def test_codeine_gene(self, client: tuple[TestClient, int]):
        tc, _ = client
        resp = tc.get("/api/analysis/pharma/drugs")
        data = resp.json()
        codeine = next(i for i in data["items"] if i["drug"] == "codeine")
        assert codeine["genes"] == ["CYP2D6"]

    def test_empty_when_no_guidelines(self, client_no_guidelines: tuple[TestClient, int]):
        tc, _ = client_no_guidelines
        resp = tc.get("/api/analysis/pharma/drugs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []


# ═══════════════════════════════════════════════════════════════════════
# GET /api/analysis/pharma/drug/{drug_name} — Drug lookup
# ═══════════════════════════════════════════════════════════════════════


class TestDrugLookup:
    """T3-05: Drug lookup for clopidogrel returns CYP2C19 with correct
    genotype, recommendation, and call confidence."""

    def test_clopidogrel_returns_cyp2c19(self, client: tuple[TestClient, int]):
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/drug/clopidogrel?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["drug"] == "clopidogrel"
        assert len(data["gene_effects"]) == 1
        effect = data["gene_effects"][0]
        assert effect["gene"] == "CYP2C19"
        assert effect["diplotype"] == "*1/*2"
        assert effect["metabolizer_status"] == "Intermediate Metabolizer"
        assert effect["recommendation"] == "Consider alternative antiplatelet therapy."
        assert effect["classification"] == "A"
        assert effect["call_confidence"] == "Complete"
        assert effect["evidence_level"] == 4
        assert effect["activity_score"] == 0.5
        assert effect["involved_rsids"] == ["rs4244285"]

    def test_codeine_returns_cyp2d6(self, client: tuple[TestClient, int]):
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/drug/codeine?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["drug"] == "codeine"
        assert len(data["gene_effects"]) == 1
        effect = data["gene_effects"][0]
        assert effect["gene"] == "CYP2D6"
        assert effect["diplotype"] == "*1/*4"
        assert effect["call_confidence"] == "Partial"

    def test_case_insensitive(self, client: tuple[TestClient, int]):
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/drug/Clopidogrel?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["drug"] == "clopidogrel"

    def test_unknown_drug_404(self, client: tuple[TestClient, int]):
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/drug/nonexistent_drug?sample_id={sample_id}")
        assert resp.status_code == 404

    def test_unknown_sample_404(self, client: tuple[TestClient, int]):
        tc, _ = client
        resp = tc.get("/api/analysis/pharma/drug/clopidogrel?sample_id=9999")
        assert resp.status_code == 404

    def test_missing_sample_id(self, client: tuple[TestClient, int]):
        tc, _ = client
        resp = tc.get("/api/analysis/pharma/drug/clopidogrel")
        assert resp.status_code == 422  # FastAPI validation error

    def test_drug_without_sample_finding(self, client: tuple[TestClient, int]):
        """Warfarin has guidelines but no sample findings → gene with no user data."""
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/drug/warfarin?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["drug"] == "warfarin"
        assert len(data["gene_effects"]) == 1
        effect = data["gene_effects"][0]
        assert effect["gene"] == "CYP2C9"
        # No sample finding → fields should be None
        assert effect["diplotype"] is None
        assert effect["metabolizer_status"] is None
        assert effect["call_confidence"] is None
        # But should still have guideline metadata
        assert effect["classification"] == "A"

    def test_no_findings_at_all(self, client_no_findings: tuple[TestClient, int]):
        """Sample has no PGx findings — genes returned with guideline info only."""
        tc, sample_id = client_no_findings
        resp = tc.get(f"/api/analysis/pharma/drug/clopidogrel?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["gene_effects"]) == 1
        effect = data["gene_effects"][0]
        assert effect["gene"] == "CYP2C19"
        assert effect["diplotype"] is None
        assert effect["classification"] == "A"


class TestDrugLookupGuideline:
    def test_guideline_url_present(self, client: tuple[TestClient, int]):
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/drug/clopidogrel?sample_id={sample_id}")
        data = resp.json()
        effect = data["gene_effects"][0]
        assert effect["guideline_url"].startswith("https://cpicpgx.org/")

    def test_confidence_note(self, client: tuple[TestClient, int]):
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/drug/clopidogrel?sample_id={sample_id}")
        data = resp.json()
        effect = data["gene_effects"][0]
        assert effect["confidence_note"] is not None
        assert len(effect["confidence_note"]) > 0

    def test_ehr_notation(self, client: tuple[TestClient, int]):
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/drug/clopidogrel?sample_id={sample_id}")
        data = resp.json()
        effect = data["gene_effects"][0]
        assert effect["ehr_notation"] == "Intermediate Metabolizer"


# ═══════════════════════════════════════════════════════════════════════
# GET /api/analysis/pharma/genes — Per-gene star-allele results
# ═══════════════════════════════════════════════════════════════════════


class TestGeneResults:
    """Per-gene metabolizer card endpoint returns grouped findings."""

    def test_returns_all_genes(self, client: tuple[TestClient, int]):
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/genes?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        genes = [item["gene"] for item in data["items"]]
        assert "CYP2C19" in genes
        assert "CYP2D6" in genes

    def test_cyp2c19_detail(self, client: tuple[TestClient, int]):
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/genes?sample_id={sample_id}")
        data = resp.json()
        cyp2c19 = next(i for i in data["items"] if i["gene"] == "CYP2C19")
        assert cyp2c19["diplotype"] == "*1/*2"
        assert cyp2c19["phenotype"] == "Intermediate Metabolizer"
        assert cyp2c19["call_confidence"] == "Complete"
        assert cyp2c19["confidence_note"] == "All defining rsids present and genotyped."
        assert cyp2c19["activity_score"] == 0.5
        assert cyp2c19["ehr_notation"] == "Intermediate Metabolizer"
        assert cyp2c19["evidence_level"] == 4
        assert cyp2c19["involved_rsids"] == ["rs4244285"]

    def test_cyp2d6_detail(self, client: tuple[TestClient, int]):
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/genes?sample_id={sample_id}")
        data = resp.json()
        cyp2d6 = next(i for i in data["items"] if i["gene"] == "CYP2D6")
        assert cyp2d6["diplotype"] == "*1/*4"
        assert cyp2d6["phenotype"] == "Intermediate Metabolizer"
        assert cyp2d6["call_confidence"] == "Partial"
        assert cyp2d6["activity_score"] == 1.0
        assert cyp2d6["involved_rsids"] == ["rs3892097"]

    def test_drugs_populated_from_cpic(self, client: tuple[TestClient, int]):
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/genes?sample_id={sample_id}")
        data = resp.json()
        cyp2c19 = next(i for i in data["items"] if i["gene"] == "CYP2C19")
        assert "clopidogrel" in cyp2c19["drugs"]
        cyp2d6 = next(i for i in data["items"] if i["gene"] == "CYP2D6")
        assert "codeine" in cyp2d6["drugs"]

    def test_empty_when_no_findings(self, client_no_findings: tuple[TestClient, int]):
        tc, sample_id = client_no_findings
        resp = tc.get(f"/api/analysis/pharma/genes?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_unknown_sample_404(self, client: tuple[TestClient, int]):
        tc, _ = client
        resp = tc.get("/api/analysis/pharma/genes?sample_id=9999")
        assert resp.status_code == 404

    def test_missing_sample_id(self, client: tuple[TestClient, int]):
        tc, _ = client
        resp = tc.get("/api/analysis/pharma/genes")
        assert resp.status_code == 422

    def test_items_sorted_by_gene(self, client: tuple[TestClient, int]):
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/genes?sample_id={sample_id}")
        data = resp.json()
        genes = [item["gene"] for item in data["items"]]
        assert genes == sorted(genes)

    def test_caveat_absent_when_finding_has_none(self, client: tuple[TestClient, int]):
        """The route passes through gene_caveat=None when the finding has none.

        CYP2C19 has no gene-specific caveat (unlike DPYD's fatal-toxicity caveat or
        CYP2D6's copy-number caveat, which are injected at store time), so its card
        must expose gene_caveat=None.
        """
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/genes?sample_id={sample_id}")
        assert resp.status_code == 200
        items = resp.json()["items"]
        cyp2c19 = next(i for i in items if i["gene"] == "CYP2C19")
        assert cyp2c19.get("gene_caveat") is None


# ── DPYD fluoropyrimidine caveat surfacing (SW-E5) ────────────────────


_DPYD_GUIDELINE = {
    "gene": "DPYD",
    "drug": "fluorouracil",
    "phenotype": "Intermediate Metabolizer",
    "recommendation": "Reduce starting dose by 50%, then titrate.",
    "classification": "A",
    "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-fluoropyrimidines-and-dpyd/",
}

_DPYD_FINDING = {
    "module": "pharmacogenomics",
    "category": "prescribing_alert",
    "evidence_level": 4,
    "gene_symbol": "DPYD",
    "diplotype": "*1/*2A",
    "metabolizer_status": "Intermediate Metabolizer",
    "drug": "fluorouracil",
    "finding_text": "DPYD *1/*2A: Intermediate Metabolizer -- fluorouracil: Reduce dose.",
    "detail_json": json.dumps(
        {
            "recommendation": "Reduce starting dose by 50%, then titrate.",
            "classification": "A",
            "guideline_url": _DPYD_GUIDELINE["guideline_url"],
            "call_confidence": "Complete",
            "confidence_note": "All defining positions assessed.",
            "activity_score": 1.0,
            "ehr_notation": "DPYD Intermediate Metabolizer",
            "involved_rsids": ["rs3918290"],
            "gene_caveat": DPYD_FLUOROPYRIMIDINE_CAVEAT,
        }
    ),
}


@pytest.fixture
def dpyd_client(tmp_data_dir: Path) -> Generator[tuple[TestClient, int], None, None]:
    yield from _setup_client(
        tmp_data_dir, CPIC_GUIDELINES_DATA + [_DPYD_GUIDELINE], [_DPYD_FINDING]
    )


class TestDpydCaveatSurfacing:
    def test_genes_endpoint_surfaces_caveat(self, dpyd_client: tuple[TestClient, int]):
        tc, sample_id = dpyd_client
        resp = tc.get(f"/api/analysis/pharma/genes?sample_id={sample_id}")
        assert resp.status_code == 200
        dpyd = next(i for i in resp.json()["items"] if i["gene"] == "DPYD")
        assert dpyd["gene_caveat"] == DPYD_FLUOROPYRIMIDINE_CAVEAT

    def test_drug_endpoint_surfaces_caveat(self, dpyd_client: tuple[TestClient, int]):
        tc, sample_id = dpyd_client
        resp = tc.get(f"/api/analysis/pharma/drug/fluorouracil?sample_id={sample_id}")
        assert resp.status_code == 200
        dpyd = next(e for e in resp.json()["gene_effects"] if e["gene"] == "DPYD")
        assert dpyd["gene_caveat"] == DPYD_FLUOROPYRIMIDINE_CAVEAT
