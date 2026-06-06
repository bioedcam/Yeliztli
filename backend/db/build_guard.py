"""Process-global guard serializing concurrent builds of the same database.

Two builds of the same SQLite file running at once — a duplicate setup-wizard
download (the in-flight dedup in ``trigger_download`` has a check-then-act gap),
a wizard build racing an auto-update (which builds standalone DBs on its own
engine, bypassing the wizard's path), or an orphaned build thread after a
restart — open two independent write connections through the shared engine
pool.  On a multi-GB load they contend for the WAL write lock long enough that
``busy_timeout`` expires and one batch ``INSERT`` fails with
``OperationalError: database is locked``.

:func:`build_lock` serializes builds **per database name** with a blocking
lock, so only one writer is ever active for a given DB while different DBs
still build in parallel.  Callers should re-check whether the DB is already
present after acquiring (a concurrent build may have just finished it) to avoid
a redundant rebuild.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

# Guards the ``_locks`` registry itself (NOT held during a build).
_registry_lock = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def _lock_for(db_name: str) -> threading.Lock:
    """Return the (lazily created) per-database lock for ``db_name``."""
    with _registry_lock:
        lock = _locks.get(db_name)
        if lock is None:
            lock = threading.Lock()
            _locks[db_name] = lock
        return lock


@contextmanager
def build_lock(db_name: str) -> Iterator[None]:
    """Block until this thread owns the build slot for ``db_name``, then release.

    Same-DB builds run one at a time; different DBs are unaffected and keep
    building concurrently.
    """
    lock = _lock_for(db_name)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()
