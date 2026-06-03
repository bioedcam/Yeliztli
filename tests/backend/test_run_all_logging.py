"""Regression guard: ``run_all_analyses`` must not crash under INFO logging.

Both per-module log calls in :func:`backend.analysis.run_all.run_all_analyses`
attach the module name via ``extra=``. The stdlib :class:`logging.LogRecord`
reserves the attribute name ``module``, so passing ``extra={"module": ...}``
raises ``KeyError("Attempt to overwrite 'module' in LogRecord")`` the moment the
record is built — i.e. whenever the logger is enabled at INFO (which the app's
``configure_logging`` does via ``basicConfig(level=logging.INFO)``). That
exception propagated out of ``run_all_analyses`` and made the annotation Huey
task skip writing ``annotation_state`` (vep_bundle_version + coverage), so the
post-annotation staleness gate never cleared. The fix renames the key to
``analysis_module``. These tests fail (KeyError) against the pre-fix code.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from backend.analysis import run_all


def _good(_sample_engine, _registry) -> int:
    return 3


def _bad(_sample_engine, _registry) -> int:
    raise RuntimeError("boom")


def test_run_all_analyses_does_not_crash_under_info_logging(monkeypatch, caplog) -> None:
    """Success path: line that logs ``analysis_module_complete`` must not raise."""
    monkeypatch.setattr(run_all, "_get_modules", lambda: [("good", _good)])
    # Enable INFO so logger.info actually builds a LogRecord (where the old
    # reserved-key collision raised). Suppressing to WARNING would mask the bug.
    caplog.set_level(logging.INFO, logger="backend.analysis.run_all")

    results = run_all.run_all_analyses(MagicMock(), MagicMock())

    assert results == {"good": 3}


def test_run_all_analyses_failure_path_logs_without_crashing(monkeypatch, caplog) -> None:
    """Failure path: the ``except`` branch's ``logger.exception`` must not raise."""
    monkeypatch.setattr(run_all, "_get_modules", lambda: [("bad", _bad)])
    caplog.set_level(logging.INFO, logger="backend.analysis.run_all")

    results = run_all.run_all_analyses(MagicMock(), MagicMock())

    # The module error is swallowed into the results map, not re-raised.
    assert results == {"bad": "error"}
