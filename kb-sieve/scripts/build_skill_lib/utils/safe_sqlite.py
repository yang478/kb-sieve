"""SQLite safety helpers for pack-builder build-time.

Provides WAL-mode migration (backwards-compatible) and retry-on-locked
decorators.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# WAL mode helpers
# ---------------------------------------------------------------------------

_WAL_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4)


def enable_wal(conn: sqlite3.Connection, *, busy_timeout_ms: int = 5000) -> None:
    """Enable WAL mode with basic retry for 'database is locked'.

    Safe to call on an already-WAL database (idempotent).
    Also sets busy_timeout so readers wait instead of erroring.
    """
    for delay in _WAL_RETRY_DELAYS:
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
            return
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower():
                logger.debug("WAL pragma locked, retry in %.3fs", delay)
                time.sleep(delay)
                continue
            raise
    # Final attempt
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")


def open_db_wal(db_path: Path, *, timeout: float = 30.0) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode and row factory.

    Backwards-compatible: works on existing DELETE-journal DBs and
    newly-created DBs.  The journal_mode change is persistent.
    """
    conn = sqlite3.connect(str(db_path), timeout=timeout)
    conn.row_factory = sqlite3.Row
    enable_wal(conn, busy_timeout_ms=int(timeout * 1000))
    return conn


# Convenience: retry a single sqlite3 call inline
def sqlite3_retry_exec(
    conn: sqlite3.Connection,
    sql: str,
    parameters: tuple[object, ...] | list[object] | None = None,
    *,
    max_retries: int = 4,
    base_delay: float = 0.05,
) -> sqlite3.Cursor:
    """Execute SQL with retry on 'database is locked'."""
    delay = base_delay
    params = parameters or ()
    for attempt in range(max_retries + 1):
        try:
            return conn.execute(sql, params)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            if attempt >= max_retries:
                raise
            time.sleep(delay)
            delay = min(delay * 2.0, 2.0)
    raise sqlite3.OperationalError(f"sqlite3_retry_exec exhausted retries: {sql}")  # pragma: no cover
