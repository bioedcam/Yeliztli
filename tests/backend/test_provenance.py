"""Unit tests for per-finding provenance + version pinning (SW-A4 / #8).

Covers the release snapshot, the coverage-bitmask decode, the per-finding
provenance block, and the post-run stamping pass (variation IDs, coverage, and
the full release snapshot stamped onto every finding).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.provenance import (
    build_finding_provenance,
    decode_coverage,
    pipeline_version,
    read_release_snapshot,
    stamp_findings_provenance,
)
from backend.db.database_registry import PIPELINE_GENOME_BUILD
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import annotated_variants, database_versions, findings, reference_metadata


@pytest.fixture
def reference_engine(tmp_path: Path) -> sa.Engine:
    """reference.db with a populated database_versions snapshot."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            database_versions.insert(),
            [
                {"db_name": "clinvar", "version": "2026-05-01", "genome_build": "GRCh37"},
                {"db_name": "gnomad", "version": "r2.1.1", "genome_build": "GRCh37"},
                {"db_name": "dbnsfp", "version": "4.4a", "genome_build": "GRCh38"},
                {"db_name": "cpic", "version": "1.20", "genome_build": "GRCh37"},
            ],
        )
    return engine


@pytest.fixture
def sample_engine(tmp_path: Path) -> sa.Engine:
    """Per-sample DB with three findings exercising the join paths."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'sample_1.db'}")
    create_sample_tables(engine)
    with engine.begin() as conn:
        conn.execute(
            annotated_variants.insert().values(
                rsid="rs80357906",
                chrom="17",
                pos=43_000_000,
                clinvar_accession="VCV000017661",
                # VEP | ClinVar | gnomAD | dbNSFP
                annotation_coverage=0b0001111,
            )
        )
        seed_findings = [
            # Finding linked to an annotated variant (gets variation IDs + coverage).
            {
                "module": "cancer",
                "category": "monogenic_variant",
                "rsid": "rs80357906",
                "finding_text": "BRCA1 Pathogenic",
                "clinvar_significance": "Pathogenic",
            },
            # Finding whose rsid is not annotated (left-join yields NULLs).
            {
                "module": "carrier_status",
                "category": "monogenic_variant",
                "rsid": "rs9999999",
                "finding_text": "CFTR carrier",
            },
            # Finding with no rsid at all (e.g. a pathway/ancestry summary).
            {
                "module": "ancestry",
                "category": "biogeographic",
                "finding_text": "82% European ancestry",
            },
        ]
        # Insert individually — rows have heterogeneous columns (core executemany
        # requires a uniform key set across all parameter dicts).
        for f in seed_findings:
            conn.execute(findings.insert().values(**f))
    return engine


class TestPipelineVersion:
    def test_returns_a_string(self) -> None:
        v = pipeline_version()
        assert isinstance(v, str) and v


class TestDecodeCoverage:
    def test_none_and_zero_decode_empty(self) -> None:
        assert decode_coverage(None) == []
        assert decode_coverage(0) == []

    def test_decodes_individual_bits(self) -> None:
        assert decode_coverage(0b0000010) == ["ClinVar"]
        assert decode_coverage(0b0000110) == ["ClinVar", "gnomAD"]

    def test_decodes_full_mask(self) -> None:
        assert decode_coverage(0b1111111) == [
            "VEP",
            "ClinVar",
            "gnomAD",
            "dbNSFP",
            "gene_phenotype",
            "GWAS",
            "CPIC",
        ]


class TestReadReleaseSnapshot:
    def test_snapshot_shape(self, reference_engine: sa.Engine) -> None:
        snap = read_release_snapshot(reference_engine)
        assert snap["clinvar"] == {"version": "2026-05-01", "genome_build": "GRCh37"}
        assert snap["dbnsfp"]["genome_build"] == "GRCh38"
        assert set(snap) == {"clinvar", "gnomad", "dbnsfp", "cpic"}


class TestBuildFindingProvenance:
    def test_includes_variation_ids_and_coverage(self) -> None:
        snap = {"clinvar": {"version": "x", "genome_build": "GRCh37"}}
        prov = build_finding_provenance(
            snap, rsid="rs1", clinvar_accession="VCV1", coverage_mask=0b0000110
        )
        assert prov["sources"] is snap
        assert prov["variation_ids"] == {"rsid": "rs1", "clinvar_accession": "VCV1"}
        assert prov["annotation_coverage_sources"] == ["ClinVar", "gnomAD"]
        assert prov["pipeline_genome_build"] == PIPELINE_GENOME_BUILD
        assert prov["pipeline_version"]

    def test_omits_absent_variation_ids(self) -> None:
        prov = build_finding_provenance({}, rsid=None, clinvar_accession=None, coverage_mask=None)
        assert prov["variation_ids"] == {}
        assert prov["annotation_coverage"] is None
        assert prov["annotation_coverage_sources"] == []


class TestStampFindingsProvenance:
    def test_stamps_all_findings(
        self, sample_engine: sa.Engine, reference_engine: sa.Engine
    ) -> None:
        count = stamp_findings_provenance(sample_engine, reference_engine)
        assert count == 3

    def test_linked_finding_carries_variation_ids_and_coverage(
        self, sample_engine: sa.Engine, reference_engine: sa.Engine
    ) -> None:
        stamp_findings_provenance(sample_engine, reference_engine)
        prov = self._provenance_by_rsid(sample_engine)["rs80357906"]
        assert prov["variation_ids"] == {
            "rsid": "rs80357906",
            "clinvar_accession": "VCV000017661",
        }
        assert prov["annotation_coverage_sources"] == ["VEP", "ClinVar", "gnomAD", "dbNSFP"]
        # Every finding pins the same full release snapshot.
        assert prov["sources"]["clinvar"]["version"] == "2026-05-01"

    def test_unannotated_rsid_has_rsid_but_no_clinvar_id_or_coverage(
        self, sample_engine: sa.Engine, reference_engine: sa.Engine
    ) -> None:
        stamp_findings_provenance(sample_engine, reference_engine)
        prov = self._provenance_by_rsid(sample_engine)["rs9999999"]
        assert prov["variation_ids"] == {"rsid": "rs9999999"}
        assert prov["annotation_coverage"] is None
        assert prov["annotation_coverage_sources"] == []
        # Snapshot is still pinned even without a variant join.
        assert set(prov["sources"]) == {"clinvar", "gnomad", "dbnsfp", "cpic"}

    def test_finding_without_rsid_has_empty_variation_ids(
        self, sample_engine: sa.Engine, reference_engine: sa.Engine
    ) -> None:
        stamp_findings_provenance(sample_engine, reference_engine)
        # The ancestry finding has no rsid → keyed under None by the helper below.
        prov = self._provenance_by_rsid(sample_engine)[None]
        assert prov["variation_ids"] == {}

    def test_idempotent(self, sample_engine: sa.Engine, reference_engine: sa.Engine) -> None:
        stamp_findings_provenance(sample_engine, reference_engine)
        second = stamp_findings_provenance(sample_engine, reference_engine)
        assert second == 3
        # Still valid JSON with the snapshot after a re-stamp.
        prov = self._provenance_by_rsid(sample_engine)["rs80357906"]
        assert prov["sources"]["gnomad"]["version"] == "r2.1.1"

    def test_empty_sample_returns_zero(self, tmp_path: Path, reference_engine: sa.Engine) -> None:
        empty = sa.create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
        create_sample_tables(empty)
        assert stamp_findings_provenance(empty, reference_engine) == 0

    @staticmethod
    def _provenance_by_rsid(sample_engine: sa.Engine) -> dict:
        with sample_engine.connect() as conn:
            rows = conn.execute(sa.select(findings.c.rsid, findings.c.provenance)).fetchall()
        return {r.rsid: json.loads(r.provenance) for r in rows}
