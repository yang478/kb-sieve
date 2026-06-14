from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import sys

from pathlib import Path
from typing import NoReturn

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


def die(message: str, code: int = 2) -> NoReturn:  # type: ignore[name-defined]
    logger.error(message)
    raise SystemExit(code)


def print_json(obj: object) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")



def resolve_root(root_arg: str) -> Path:
    if root_arg:
        root = Path(root_arg).resolve()
        if not root.is_dir():
            die(f"Skill root is not a directory: {root}")
        return root
    return Path(".").resolve()


def resolve_db_path(root: Path, db_arg: str) -> Path:
    """Resolve --db argument to an absolute path, enforcing it stays within root.

    Prevents path traversal attacks like --db ../../../etc/passwd.
    """
    db_path = (root / str(db_arg)).resolve()
    try:
        db_path.relative_to(root.resolve())
    except ValueError:
        die(f"Refusing database path outside skill root: --db {db_arg!r}")
    return db_path


def open_db(db_path: Path, *, check_integrity: bool = False) -> sqlite3.Connection:
    """Open kb.sqlite with WAL mode and busy timeout.

    Backwards-compatible: works on DBs created with DELETE journal mode.
    The WAL mode change is persistent.
    """
    if not db_path.exists():
        die(f"Missing kb.sqlite: {db_path} (run build or reindex first)")
    from .safe_sqlite import open_db_wal

    conn = open_db_wal(db_path)
    if check_integrity:
        # PRAGMA quick_check scans the database; keep it opt-in for latency-sensitive searches.
        try:
            ok = conn.execute("PRAGMA quick_check").fetchone()
            if ok and str(ok[0]).lower() != "ok":
                die(
                    f"Database integrity check failed: {ok[0]}\n"
                    f"  File: {db_path}\n"
                    "  Try re-running the build to regenerate kb.sqlite."
                )
        except sqlite3.Error as exc:
            logger.warning("Could not run integrity check: %s", exc)
    return conn




# Filenames that must never be overwritten via --out (security / data integrity).
_PROTECTED_FILENAMES: frozenset[str] = frozenset(
    {
        "kb.sqlite",
        "kb.sqlite-wal",
        "kb.sqlite-shm",
        "kbtool_state.sqlite",
        "kbtool_state.sqlite-wal",
        "kbtool_state.sqlite-shm",
        "corpus_manifest.json",
        "build_state.json",
    }
)


def safe_output_path(root: Path, out_arg: str) -> Path:
    root_resolved = root.resolve()
    out_path = (root / str(out_arg)).resolve()
    try:
        out_path.relative_to(root_resolved)
    except ValueError:
        die(f"Refusing to write outside skill root: --out {out_arg!r}")
    if out_path == root_resolved:
        die(f"Invalid --out (points to skill root directory): --out {out_arg!r}")
    if out_path.name in _PROTECTED_FILENAMES:
        die(f"Refusing to overwrite protected file: --out {out_arg!r}")
    return out_path


def escape_markdown_inline(text: str) -> str:
    """Escape backticks and newlines in user-provided text for safe inline use."""
    return text.replace("`", "'").replace("\n", " ")
