from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from ..types import InputDoc
from ..utils.fs import DataIntegrityError
from ..utils.safe_sqlite import enable_wal, open_db_wal, sqlite3_retry_exec
from ..utils.text import fts_tokens, markdown_to_plain
from .schema import INDEX_SCRIPT, SCHEMA_SCRIPT

if TYPE_CHECKING:
    from ..incremental.invalidation import ChangeSet


# ---------------------------------------------------------------------------
# FTS5 table creation
# ---------------------------------------------------------------------------


def _create_doc_fts(conn: sqlite3.Connection) -> None:
    """Create doc_fts as external-content FTS5 backing fts_title + body.

    Title is indexed separately so BM25 column weighting (title=10, body=1)
    can boost documents whose title directly matches the query — critical
    for identifier lookups (standard numbers, chapter titles, names) which
    FTS body-only BM25 systematically fails on (high-freq terms → low IDF).
    """
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS doc_fts USING fts5(
          fts_title, body,
          content='docs',
          content_rowid='doc_row_id'
        )
        """
    )


# ---------------------------------------------------------------------------
# Line offset computation
# ---------------------------------------------------------------------------


def _compute_line_offsets(text: str) -> list[tuple[int, int]]:
    """Compute (line_number, byte_offset) pairs for text.

    Line numbers are 1-based. Each entry records the byte offset where the
    line starts.
    """
    offsets: list[tuple[int, int]] = []
    line_num = 1
    byte_pos = 0
    offsets.append((line_num, byte_pos))
    for ch in text:
        byte_pos += 1
        if ch == "\n":
            line_num += 1
            offsets.append((line_num, byte_pos))
    return offsets


# ---------------------------------------------------------------------------
# Record insertion (shared by full and incremental writes)
# ---------------------------------------------------------------------------


def _insert_records(
    conn: sqlite3.Connection,
    docs: Sequence[InputDoc],
    doc_texts: dict[tuple[str, str], tuple[str, str]],
    *,
    base_dir: Path | None = None,
) -> None:
    """Insert document records and build FTS index.

    Args:
        docs: InputDoc instances to insert.
        doc_texts: Mapping of (doc_id, source_version) -> (title, body_plain).
    """
    if not docs:
        return

    doc_rows = []
    offset_rows = []
    # (doc_id, source_version, tokenized_title, tokenized_body) — fed to doc_fts after docs rowids are known
    doc_tokenized: list[tuple[str, str, str, str]] = []

    for d in docs:
        key = (d.doc_id, d.source_version)
        title, body_plain = doc_texts.get(key, (d.title, ""))

        # Compute line count
        line_count = body_plain.count("\n") + 1 if body_plain else 0

        # Pre-tokenize title and body once — reused by both docs columns and doc_fts index
        title_tokenized = fts_tokens(title) if title else ""
        body_tokenized = fts_tokens(body_plain)

        doc_rows.append((
            d.doc_id,
            d.title,
            d.path.name,
            str(d.path),
            d.doc_hash,
            d.source_version,
            1 if d.is_active else 0,
            line_count,
            body_tokenized,
            title_tokenized,
        ))

        doc_tokenized.append((d.doc_id, d.source_version, title_tokenized, body_tokenized))

        # Line offsets
        offsets = _compute_line_offsets(body_plain)
        for line_num, byte_off in offsets:
            offset_rows.append((d.doc_id, d.source_version, line_num, byte_off))

    conn.executemany(
        """
        INSERT INTO docs(
          doc_id, doc_title, source_file, source_path, doc_hash,
          source_version, is_active, line_count, body, fts_title
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        doc_rows,
    )

    # external-content FTS5: rowid must equal docs.doc_row_id so JOINs are O(1)
    if doc_tokenized:
        doc_ids = [dt[0] for dt in doc_tokenized]
        placeholders = ",".join("?" for _ in doc_ids)
        rows = conn.execute(
            f"SELECT doc_id, doc_row_id FROM docs WHERE doc_id IN ({placeholders})",
            doc_ids,
        ).fetchall()
        doc_id_to_rowid = {r[0]: r[1] for r in rows}

        fts_data = [
            (doc_id_to_rowid[did], title_tok, body_tok)
            for did, _sv, title_tok, body_tok in doc_tokenized
            if did in doc_id_to_rowid and (title_tok or body_tok)
        ]
        if fts_data:
            conn.executemany(
                "INSERT INTO doc_fts(rowid, fts_title, body) VALUES (?,?,?)",
                fts_data,
            )
            conn.execute("INSERT INTO doc_fts(doc_fts) VALUES('optimize')")

    if offset_rows:
        conn.executemany(
            """
            INSERT INTO line_offsets(doc_id, source_version, line_number, byte_offset)
            VALUES (?,?,?,?)
            """,
            offset_rows,
        )


# ---------------------------------------------------------------------------
# Full database write
# ---------------------------------------------------------------------------


def write_kb_sqlite_db(
    db_path: Path,
    docs: Sequence[InputDoc],
    doc_texts: dict[tuple[str, str], tuple[str, str]],
    *,
    base_dir: Path | None = None,
) -> None:
    """Create a new SQLite database with whole-document FTS index."""
    tmp_path = db_path.with_suffix(".sqlite.tmp")
    if tmp_path.exists():
        tmp_path.unlink()
    try:
        conn = sqlite3.connect(str(tmp_path))
        try:
            enable_wal(conn)
            conn.execute("PRAGMA temp_store = MEMORY")

            conn.executescript(SCHEMA_SCRIPT)

            try:
                _create_doc_fts(conn)
            except sqlite3.OperationalError as exc:
                raise DataIntegrityError(f"SQLite FTS5 is required but unavailable: {exc}") from exc

            conn.execute("BEGIN")
            _insert_records(conn, docs, doc_texts, base_dir=base_dir)
            conn.commit()

            conn.executescript(INDEX_SCRIPT)
            with suppress(Exception):
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
        import os

        os.replace(str(tmp_path), str(db_path))
    except OSError:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


# ---------------------------------------------------------------------------
# Incremental database update
# ---------------------------------------------------------------------------


def incremental_update_kb_sqlite_db(
    db_path: Path,
    change_set: ChangeSet,
    docs: Sequence[InputDoc],
    doc_texts: dict[tuple[str, str], tuple[str, str]],
    *,
    base_dir: Path | None = None,
) -> None:
    """Apply incremental updates to an existing SQLite database.

    Strategy:
    - removed_docs: DELETE related records
    - rebuild_docs: DELETE old records + INSERT new records
    - metadata_only_docs: UPDATE docs metadata
    - unchanged_docs: no operation
    """
    conn = sqlite3.connect(str(db_path))
    try:
        enable_wal(conn)
        conn.execute("PRAGMA temp_store = MEMORY")
        _create_doc_fts(conn)
        conn.commit()

        dirty_doc_ids = change_set.rebuild_doc_ids | change_set.removed_doc_ids

        # external-content FTS5: doc_fts has no doc_id column, only rowid.
        # Resolve rowids BEFORE deleting docs (they are gone afterwards).
        placeholders = ",".join("?" for _ in dirty_doc_ids)
        dirty_rowids = [
            r[0]
            for r in conn.execute(
                f"SELECT doc_row_id FROM docs WHERE doc_id IN ({placeholders})",
                list(dirty_doc_ids),
            ).fetchall()
        ]

        conn.execute("BEGIN")
        for rowid in dirty_rowids:
            conn.execute("DELETE FROM doc_fts WHERE rowid = ?", (rowid,))
        for doc_id in dirty_doc_ids:
            conn.execute("DELETE FROM docs WHERE doc_id = ?", (doc_id,))
            conn.execute(
                "DELETE FROM line_offsets WHERE doc_id = ?",
                (doc_id,),
            )

        # Insert rebuild docs (only active ones)
        rebuild_doc_set = change_set.rebuild_doc_ids
        rebuild_docs = [d for d in docs if d.doc_id in rebuild_doc_set and d.is_active]

        _insert_records(conn, rebuild_docs, doc_texts, base_dir=base_dir)

        # metadata_only: update docs table
        for doc_id in change_set.metadata_only_doc_ids:
            doc = next((d for d in docs if d.doc_id == doc_id), None)
            if doc is not None:
                is_active_int = 1 if doc.is_active else 0
                conn.execute(
                    """
                    UPDATE docs
                    SET source_path = ?, doc_hash = ?, is_active = ?
                    WHERE doc_id = ? AND source_version = ?
                    """,
                    (str(doc.path), doc.doc_hash, is_active_int, doc.doc_id, doc.source_version),
                )

        conn.executescript(INDEX_SCRIPT)

        with suppress(Exception):
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        with suppress(Exception):
            conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Read existing records (for incremental builds)
# ---------------------------------------------------------------------------


def _fetchall(db_path: Path, sql: str) -> list[sqlite3.Row]:
    if not db_path.exists():
        return []
    conn = open_db_wal(db_path)
    try:
        return sqlite3_retry_exec(conn, sql).fetchall()
    finally:
        conn.close()


def read_existing_docs(db_path: Path) -> list[InputDoc]:
    rows = _fetchall(
        db_path,
        "SELECT doc_id, doc_title, source_path, doc_hash, source_version, is_active "
        "FROM docs ORDER BY doc_id, source_version",
    )
    return [
        InputDoc(
            path=Path(str(row["source_path"])),
            doc_id=str(row["doc_id"]),
            title=str(row["doc_title"]),
            source_version=str(row["source_version"]),
            doc_hash=str(row["doc_hash"]),
            is_active=bool(row["is_active"]),
        )
        for row in rows
    ]
