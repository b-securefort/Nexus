"""
sqlite-vec extension loader for SQLAlchemy engines.

Attaches a `connect` event listener that:
  1. Calls `sqlite_vec.load()` on every new DBAPI connection so vec0 virtual
     tables become queryable.
  2. Enables WAL journal mode so the periodic KB re-indexer can write without
     blocking in-flight chat reads.

If the underlying Python build was compiled without `enable_load_extension`
(some Anaconda / minimal builds), we don't crash the app — we mark the
hybrid-retrieval feature as unavailable and let the search tool return a
clear error instead of a cryptic OperationalError.
"""

from __future__ import annotations

import logging
import sqlite3

from sqlalchemy import event

logger = logging.getLogger(__name__)

# Set once at engine-init time when sqlite-vec cannot be loaded.
# Not re-evaluated per request — mid-run .so deletion requires a process restart.
_vec_load_failed: bool = False
_vec_load_failed_reason: str = ""


def hybrid_disabled() -> bool:
    return _vec_load_failed


def disabled_reason() -> str:
    return _vec_load_failed_reason


def _mark_disabled(reason: str) -> None:
    global _vec_load_failed, _vec_load_failed_reason
    _vec_load_failed = True
    _vec_load_failed_reason = reason


def attach_sqlite_vec(engine) -> None:
    """Register the per-connection sqlite-vec loader on a SQLAlchemy engine.

    Idempotent: safe to call multiple times in tests.
    """
    import sqlite_vec  # imported here so unit tests can import this module without the extension

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _connection_record):
        try:
            dbapi_conn.enable_load_extension(True)
            sqlite_vec.load(dbapi_conn)
            dbapi_conn.enable_load_extension(False)
        except AttributeError as e:
            _mark_disabled(
                "Python sqlite3 was built without enable_load_extension. "
                "Rebuild Python with sqlite extensions enabled, or use the "
                "python.org Windows installer (not Anaconda)."
            )
            logger.error("sqlite-vec load failed: %s — %s", e, _disabled_reason)
            return
        except sqlite3.OperationalError as e:
            _mark_disabled(f"sqlite_vec.load failed at runtime: {e}")
            logger.error("sqlite-vec load failed: %s", e)
            return

        # WAL lets the reindex writer run without blocking chat reads
        try:
            dbapi_conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError as e:
            logger.warning("Failed to enable WAL mode: %s", e)
