"""Tests for backend.db.build_guard (per-database build serialization)."""

from __future__ import annotations

import threading
import time

from backend.db.build_guard import build_lock


class TestBuildLock:
    def test_serializes_same_database(self) -> None:
        """A second build of the same DB blocks until the first releases."""
        events: list[str] = []
        first_holding = threading.Event()
        release_first = threading.Event()

        def first() -> None:
            with build_lock("dbnsfp"):
                events.append("first-acquired")
                first_holding.set()
                release_first.wait(timeout=5)
                events.append("first-releasing")

        def second() -> None:
            first_holding.wait(timeout=5)  # ensure first holds the lock
            events.append("second-trying")
            with build_lock("dbnsfp"):
                events.append("second-acquired")

        t1 = threading.Thread(target=first)
        t2 = threading.Thread(target=second)
        t1.start()
        t2.start()

        # Give the second thread time to block on the lock.
        time.sleep(0.2)
        assert "second-trying" in events
        assert "second-acquired" not in events  # still blocked

        release_first.set()
        t1.join(timeout=5)
        t2.join(timeout=5)

        # second must only acquire after first released.
        assert events.index("first-releasing") < events.index("second-acquired")

    def test_different_databases_do_not_block(self) -> None:
        """Holding the lock for one DB must not block a different DB."""
        gnomad_acquired = threading.Event()
        hold_dbnsfp = threading.Event()

        def hold_dbnsfp_lock() -> None:
            with build_lock("dbnsfp"):
                hold_dbnsfp.wait(timeout=5)

        def acquire_gnomad() -> None:
            with build_lock("gnomad"):
                gnomad_acquired.set()

        t1 = threading.Thread(target=hold_dbnsfp_lock)
        t2 = threading.Thread(target=acquire_gnomad)
        t1.start()
        t2.start()

        # gnomad should acquire promptly despite dbnsfp being held.
        assert gnomad_acquired.wait(timeout=5)

        hold_dbnsfp.set()
        t1.join(timeout=5)
        t2.join(timeout=5)

    def test_lock_released_on_exception(self) -> None:
        """An exception inside the guard still releases the lock."""
        try:
            with build_lock("cpic"):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        # Re-acquiring must not deadlock.
        acquired = threading.Event()

        def reacquire() -> None:
            with build_lock("cpic"):
                acquired.set()

        t = threading.Thread(target=reacquire)
        t.start()
        assert acquired.wait(timeout=5)
        t.join(timeout=5)
