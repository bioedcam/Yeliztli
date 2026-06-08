"""Meta-guards: keep the live path the path under test.

The defect class behind the whole validation effort is *orphaned correct code* —
a carriage-aware writer that exists, is unit-tested, and is wired to nothing,
while the live engine stays genotype-agnostic and the green suite never notices.
These structural guards make that class of bug visible:

* ``test_live_engine_computes_carriage`` asserts the live engine actually
  references the carriage classifier (``xfail`` until Phase C1 wires it).
* ``test_validation_suite_never_calls_orphan_writers`` asserts this suite drives
  the live pipeline, never the standalone ``annotate_sample_*`` writers (a
  standing guard — it must pass today and forever).
"""

from __future__ import annotations

from pathlib import Path

import backend.annotation.engine as engine_mod

_ENGINE_SRC = Path(engine_mod.__file__).read_text(encoding="utf-8")
_SUITE_DIR = Path(__file__).resolve().parent


def test_live_engine_computes_carriage() -> None:
    """``run_annotation``'s module must use the carriage classifier."""
    assert "classify_zygosity" in _ENGINE_SRC or "CARRIED_ZYGOSITIES" in _ENGINE_SRC


def test_validation_suite_never_calls_orphan_writers() -> None:
    """No test in this package may reach for the orphaned annotate_sample_* path.

    Those standalone writers are unit-tested elsewhere; the live-path contract
    must be exercised through ``run_annotation`` / ``run_all_analyses`` only.
    """
    # The orphaned writer names, assembled so this guard file does not match
    # its own scan.
    orphans = ("annotate_sample_" + "clinvar", "annotate_sample_" + "dbsnp")
    offenders: list[str] = []
    for path in _SUITE_DIR.glob("test_*.py"):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8")
        if any(name in text for name in orphans):
            offenders.append(path.name)
    assert offenders == [], f"validation tests must use the live path, not: {offenders}"
