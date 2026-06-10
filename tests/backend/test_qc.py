"""Tests for the sample QC metrics module."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from backend.analysis.qc import (
    compute_qc_metrics,
    het_outlier_zscore,
    sex_check,
    store_qc_metrics,
)
from backend.db.tables import qc_metrics, raw_variants


def _seed(engine: sa.Engine, rows: list[dict]) -> None:
    with engine.begin() as conn:
        conn.execute(sa.insert(raw_variants), rows)


def _v(rsid: str, chrom: str, genotype: str, pos: int = 1000) -> dict:
    return {"rsid": rsid, "chrom": chrom, "pos": pos, "genotype": genotype}


class TestComputeMetrics:
    def test_call_rate_het_and_titv(self, sample_engine: sa.Engine) -> None:
        _seed(
            sample_engine,
            [
                _v("r1", "1", "AG"),  # het, transition (A↔G)
                _v("r2", "2", "CT"),  # het, transition (C↔T)
                _v("r3", "3", "AC"),  # het, transversion
                _v("r4", "4", "AA"),  # hom
                _v("r5", "5", "GG"),  # hom
                _v("r6", "6", "--"),  # no-call
                _v("rx", "X", "AG"),  # called but non-autosomal (excluded from het/Ti-Tv)
            ],
        )
        m = compute_qc_metrics(sample_engine)
        assert m.total_variants == 7
        assert m.nocall_variants == 1
        assert m.called_variants == 6
        assert m.call_rate == pytest.approx(6 / 7, abs=1e-4)
        # autosomal het=3, hom=2 → het rate 0.6
        assert m.heterozygosity_rate == pytest.approx(0.6, abs=1e-4)
        # transitions=2, transversions=1 → Ti/Tv = 2.0
        assert m.ti_tv_ratio == pytest.approx(2.0, abs=1e-4)

    def test_titv_none_when_no_transversions(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_v("r1", "1", "AG"), _v("r2", "2", "CT")])
        m = compute_qc_metrics(sample_engine)
        assert m.ti_tv_ratio is None

    def test_store_is_idempotent(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_v("r1", "1", "AG"), _v("r2", "2", "AA")])
        m = compute_qc_metrics(sample_engine)
        store_qc_metrics(m, sample_engine)
        store_qc_metrics(m, sample_engine)
        with sample_engine.connect() as conn:
            n = conn.execute(sa.select(sa.func.count()).select_from(qc_metrics)).scalar()
        assert n == 1


class TestSexCheck:
    def test_concordant(self) -> None:
        assert sex_check("XX", "XX") == "concordant"
        assert sex_check("XY", "XY") == "concordant"

    def test_discordant(self) -> None:
        assert sex_check("XX", "XY") == "discordant"

    def test_indeterminate(self) -> None:
        assert sex_check("manual_review", "XX") == "indeterminate"
        assert sex_check("XX", None) == "indeterminate"
        assert sex_check("unknown", "XY") == "indeterminate"


class TestHetOutlier:
    def test_insufficient_samples_returns_none(self) -> None:
        assert het_outlier_zscore(0.3, []) is None
        assert het_outlier_zscore(0.3, [0.30, 0.31]) is None

    def test_within_range(self) -> None:
        z = het_outlier_zscore(0.30, [0.30, 0.31, 0.29, 0.30])
        assert z is not None and abs(z) < 3

    def test_outlier(self) -> None:
        z = het_outlier_zscore(0.50, [0.30, 0.31, 0.29, 0.30])
        assert z is not None and abs(z) > 3
