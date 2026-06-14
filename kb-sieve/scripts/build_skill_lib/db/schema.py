from __future__ import annotations

# ---------------------------------------------------------------------------
# Schema for whole-document indexing (no chunking)
# ---------------------------------------------------------------------------
# Tables:
#   docs        — one row per document (incl. tokenized body for FTS content)
#   doc_fts     — FTS5 external-content virtual table indexing docs.body
#   line_offsets — byte offset → line number mapping for each document
# ---------------------------------------------------------------------------
# doc_fts uses external-content mode (content='docs', content_rowid='doc_row_id')
# so FTS SELECTs only read rowid+rank (O(1)) instead of dragging the entire
# tokenized body out of the FTS row record. This is critical for large docs
# (5MB+ Chinese) where reading the body per query would dominate latency.
# ---------------------------------------------------------------------------

SCHEMA_SCRIPT = """
CREATE TABLE docs (
  doc_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id TEXT NOT NULL,
  doc_title TEXT NOT NULL,
  source_file TEXT NOT NULL,
  source_path TEXT NOT NULL,
  doc_hash TEXT NOT NULL,
  source_version TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  line_count INTEGER NOT NULL DEFAULT 0,
  body TEXT,
  fts_title TEXT,
  UNIQUE (doc_id, source_version)
);

CREATE TABLE line_offsets (
  doc_id TEXT NOT NULL,
  source_version TEXT NOT NULL,
  line_number INTEGER NOT NULL,
  byte_offset INTEGER NOT NULL,
  PRIMARY KEY (doc_id, source_version, line_number)
);

"""

INDEX_SCRIPT = """
CREATE INDEX IF NOT EXISTS idx_docs_doc_id_active ON docs(doc_id, is_active);
CREATE INDEX IF NOT EXISTS idx_line_offsets_doc ON line_offsets(doc_id, source_version);
"""
