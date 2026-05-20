"""LAI runner: pre-Phase-3 sample DB backward compat (Step 22a; Plan §6.6).

Pre-Phase-3 sample DBs predate the ``raw_variants.source`` column (added in
step 63). On those DBs, ``_read_sample_genotypes`` defaults every genotype's
``source`` to ``""`` and the runner derives the vendor key from
``file_format.split("_", 1)[0].lower()`` — collapsing to a single-key
``{vendor: {hits, drops}}`` telemetry payload.

This test locks the derivation by:

1. Building an in-memory sample DB on the *current* schema (no ``source``
   column on ``raw_variants``).
2. Reading genotypes via ``_read_sample_genotypes`` and asserting every entry
   has ``source=""``.
3. Running the runner's filter + per-source accumulator and asserting the
   resulting telemetry is single-key with the expected vendor.

Parametrized over both shipped vendors so regressions to either derivation
trip immediately:

- ``23andme_v5`` → ``"23andme"``
- ``ancestrydna_v2.0`` → ``"ancestrydna"``
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import sqlalchemy as sa

from backend.analysis.lai import _read_sample_file_format, _read_sample_genotypes
from backend.analysis.lai_runner import LAIRunner
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import raw_variants, sample_metadata_table

# ── Shared payload ───────────────────────────────────────────────────────


# Three autosomal hits, one autosomal off-bundle drop. All single-source so
# the empty-string accumulator bucket carries every genotype.
_VENDOR_NEUTRAL_ROWS = [
    {"rsid": "rs_hit_1", "chrom": "1", "pos": 1001, "genotype": "AG"},
    {"rsid": "rs_hit_2", "chrom": "2", "pos": 2001, "genotype": "GG"},
    {"rsid": "rs_hit_3", "chrom": "22", "pos": 22001, "genotype": "CT"},
    {"rsid": "rs_off_bundle", "chrom": "5", "pos": 50000, "genotype": "TT"},
]


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture()
def runner() -> LAIRunner:
    instance = LAIRunner.__new__(LAIRunner)
    instance.rsid_lookup = {
        "rs_hit_1": ("chr1", 1001),
        "rs_hit_2": ("chr2", 2001),
        "rs_hit_3": ("chr22", 22001),
    }
    return instance


def _build_pre_phase3_engine(file_format: str) -> sa.Engine:
    """Build an in-memory sample DB on the current (pre-step-63) schema.

    ``raw_variants`` has no ``source`` column on the current schema —
    ``_read_sample_genotypes`` must default ``source`` to ``""``.
    """
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    with engine.begin() as conn:
        conn.execute(
            sample_metadata_table.insert().values(
                id=1,
                name=f"compat_{file_format}",
                file_format=file_format,
                file_hash="compat_hash",
            )
        )
        conn.execute(raw_variants.insert(), _VENDOR_NEUTRAL_ROWS)
    return engine


# ── Tests ─────────────────────────────────────────────────────────────────


class TestPrePhase3ReadDefaults:
    """`_read_sample_genotypes` defaults source to '' when column is absent."""

    @pytest.mark.parametrize(
        "file_format,expected_vendor",
        [
            ("23andme_v5", "23andme"),
            ("ancestrydna_v2.0", "ancestrydna"),
        ],
    )
    def test_source_defaults_to_empty_string(self, file_format, expected_vendor):
        engine = _build_pre_phase3_engine(file_format)
        # Sanity: confirm the source column genuinely isn't on raw_variants.
        cols = {c["name"] for c in sa.inspect(engine).get_columns("raw_variants")}
        assert "source" not in cols

        genotypes = _read_sample_genotypes(engine)
        assert len(genotypes) == len(_VENDOR_NEUTRAL_ROWS)
        assert all(gt["source"] == "" for gt in genotypes)

        # file_format roundtrips for downstream vendor derivation
        assert _read_sample_file_format(engine) == file_format
        assert file_format.split("_", 1)[0].lower() == expected_vendor


class TestPrePhase3SingleKeyTelemetry:
    """Single-key telemetry derives from ``file_format`` on pre-Phase-3 DBs."""

    @pytest.mark.parametrize(
        "file_format,expected_vendor",
        [
            ("23andme_v5", "23andme"),
            ("ancestrydna_v2.0", "ancestrydna"),
        ],
    )
    def test_pre_phase3_collapses_to_single_vendor_key(
        self, runner, tmp_path, file_format, expected_vendor
    ):
        engine = _build_pre_phase3_engine(file_format)
        file_format_read = _read_sample_file_format(engine)
        genotypes = _read_sample_genotypes(engine)
        filtered = runner._filter_genotypes(genotypes)

        with patch.object(LAIRunner, "_write_single_vcf", lambda *a, **k: None):
            _, total, per_source = runner._write_per_chrom_vcfs(filtered, tmp_path)

        # Accumulator only sees the empty-source bucket — no S1/S2/both leakage
        assert set(per_source.keys()) == {""}
        assert total == 3
        assert per_source[""] == {"hits": 3, "drops": 1}

        telemetry = LAIRunner._build_coverage_telemetry(per_source, file_format_read)
        assert set(telemetry.keys()) == {expected_vendor}
        assert telemetry[expected_vendor] == {"hits": 3, "drops": 1}


class TestVendorDerivationLocksSplitContract:
    """Lock the exact ``file_format.split("_", 1)[0].lower()`` derivation."""

    @pytest.mark.parametrize(
        "file_format,expected_vendor",
        [
            ("23andme_v5", "23andme"),
            ("23andme_v4", "23andme"),
            ("23andme_v3", "23andme"),
            ("ancestrydna_v2.0", "ancestrydna"),
            # Hypothetical case-insensitive vendor strings still normalize via
            # .lower() to guard against future producer changes.
            ("ANCESTRYDNA_v2.0", "ancestrydna"),
            ("23andMe_v5", "23andme"),
        ],
    )
    def test_build_coverage_telemetry_derives_vendor(
        self, file_format, expected_vendor
    ):
        per_source = {"": {"hits": 5, "drops": 1}}
        telemetry = LAIRunner._build_coverage_telemetry(per_source, file_format)
        assert set(telemetry.keys()) == {expected_vendor}
        assert telemetry[expected_vendor] == {"hits": 5, "drops": 1}
