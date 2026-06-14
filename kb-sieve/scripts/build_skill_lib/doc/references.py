from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..ir.io import _ir_node_file_index
from ..render.node import frontmatter_kb_node, render_kb_node_frontmatter, write_doc_metadata, write_structure_report
from ..types import HeadingRow, InputDoc
from ..utils.fs import ConfigError, write_text
from ..utils.text import canonical_text_from_markdown, stable_hash

# ---------------------------------------------------------------------------
# Heading extraction
# ---------------------------------------------------------------------------


@dataclass
class _HeadingEntry:
    title: str
    level: int
    char_start: int
    char_end: int


def _extract_heading_entries(text: str) -> list[_HeadingEntry]:
    """Extract all headings with their character positions from markdown text."""
    entries: list[_HeadingEntry] = []
    cursor = 0
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        m = re.match(r"^(#{1,6})\s+(.+)", stripped)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            entries.append(
                _HeadingEntry(
                    title=title,
                    level=level,
                    char_start=cursor,
                    char_end=cursor + len(line),
                )
            )
        cursor += len(line)
    return entries


# ---------------------------------------------------------------------------
# Whole-document generation (no chunking)
# ---------------------------------------------------------------------------


def generate_doc(
    doc: InputDoc,
    canonical_md: str,
    out_skill_dir: Path,
) -> list[HeadingRow]:
    """Generate a whole-document reference file (no chunking).

    Writes a single references/{doc_id}/doc.md containing the full document.
    Returns heading rows for index building.
    """
    doc_dir = out_skill_dir / "references" / doc.doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)

    write_doc_metadata(doc, doc_dir, active_parser="whole_doc")

    doc_path = doc_dir / "doc.md"
    doc_rel = f"references/{doc.doc_id}/doc.md"

    # Extract headings for TOC
    heading_entries = _extract_heading_entries(canonical_md)

    heading_rows: list[HeadingRow] = []

    # Document-level heading row
    heading_rows.append((doc.title, doc.doc_id, doc.title, "doc", f"{doc.doc_id}:doc", doc_rel))

    # Build doc.md content: frontmatter + title + TOC + full text
    doc_body_lines = [
        f"# {doc.title}\n",
        "",
    ]

    # Add TOC from heading entries
    if heading_entries:
        doc_body_lines.append("## 目录\n")
        for h in heading_entries:
            indent = "  " * (h.level - 1)
            doc_body_lines.append(f"{indent}- {h.title}")
        doc_body_lines.append("")

    # Stats
    line_count = canonical_md.count("\n") + 1
    char_count = len(canonical_md)
    doc_body_lines.append(f"## 统计\n")
    doc_body_lines.append(f"- 行数: {line_count}")
    doc_body_lines.append(f"- 字符数: {char_count}")
    doc_body_lines.append("")

    # Full document text
    doc_body_lines.append("---\n")
    doc_body_lines.append("")
    doc_body_lines.append(canonical_md.rstrip())
    doc_body_lines.append("")

    write_text(
        doc_path,
        frontmatter_kb_node(
            doc,
            node_id=f"{doc.doc_id}:doc",
            kind="doc",
            label=doc.doc_id,
            title=doc.title,
            parent_id="",
            ref_path=doc_rel,
        )
        + "\n".join(doc_body_lines)
        + "\n",
    )

    # Structure report
    stats = {
        "line_count": line_count,
        "char_count": char_count,
        "heading_count": len(heading_entries),
    }
    write_structure_report(
        doc,
        doc_dir,
        selected_parser="whole_doc",
        runner_ups=(),
        selected_report={
            "mode": "whole_document",
            "stats": stats,
        },
    )

    return heading_rows


def generate_doc_from_ir(
    doc: InputDoc,
    nodes: list,
    out_skill_dir: Path,
) -> list[HeadingRow]:
    """Generate reference files from IR nodes (whole-document mode).

    Concatenates all node bodies into a single doc.md.
    """
    from ..types import NodeRecord

    doc_dir = out_skill_dir / "references" / doc.doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)

    write_doc_metadata(doc, doc_dir, active_parser=doc.active_parser or "whole_doc")

    doc_path = doc_dir / "doc.md"
    doc_rel = f"references/{doc.doc_id}/doc.md"

    heading_rows: list[HeadingRow] = []

    # Concatenate all active node bodies
    bodies = []
    for node in nodes:
        if node.doc_id == doc.doc_id and node.is_active:
            body = node.body_md or node.body_plain or ""
            if body.strip():
                bodies.append(body.strip())

    full_text = "\n\n".join(bodies)

    write_text(
        doc_path,
        render_kb_node_frontmatter(doc, NodeRecord(
            node_id=f"{doc.doc_id}:doc",
            doc_id=doc.doc_id,
            doc_title=doc.title,
            kind="doc",
            label=doc.doc_id,
            title=doc.title,
            parent_id=None,
            prev_id=None,
            next_id=None,
            ordinal=0,
            ref_path=doc_rel,
            is_leaf=True,
            body_md=full_text,
            body_plain="",
            source_version=doc.source_version,
        ))
        + full_text.rstrip() + "\n",
    )

    heading_rows.append((doc.title, doc.doc_id, doc.title, "doc", f"{doc.doc_id}:doc", doc_rel))
    return heading_rows
