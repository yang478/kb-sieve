"""kbtool read — 知识库原文精读工具

比通用 read 工具强的点：
  1. --around <line>  自动检测自然段落/章节边界，返回完整段落（不用猜 offset/limit）
  2. --sections      列出文档所有章节标题+行号（文档地图，agent 按图索骥）
  3. --find <word>    在指定行之后搜索关键词并返回上下文（grep+read 一体）
  4. --jump           跳读多段不连续行
  5. --tokens         命中标记（哪些行匹配了查询词）
  6. 多文档并行       --doc-id doc1,doc2 一次读多个文档
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from .runtime import resolve_root
from .text import fts_tokens

# Section boundary patterns (heading lines or blank-line-separated paragraphs)
_HEADING_RE = re.compile(r'^#{1,6}\s')
_NUM_SECTION_RE = re.compile(r'^#{0,2}\s*(?:第[一二三四五六七八九十百千\d]+[章节回]|[0-9]+(?:\.[0-9]+)*\s)')


def _resolve_doc_path(skill_root: Path, doc_id: str) -> Path | None:
    p = skill_root / "references" / doc_id / "doc.md"
    return p if p.is_file() else None


def _strip_frontmatter(text: str) -> tuple[int, str]:
    if not text.startswith("---"):
        return 1, text
    end = text.find("\n---", 3)
    if end == -1:
        return 1, text
    body = text[end + 4:].lstrip("\n")
    return text[:end + 4].count("\n") + 1, body


def _detect_section_boundaries(lines: list[str]) -> list[tuple[int, int]]:
    """Return list of (start_line_1based, end_line_1based) for each section.

    A section starts at a heading-like line or after a blank line.
    """
    boundaries = []
    i = 0
    n = len(lines)
    while i < n:
        # Find section start
        start = i
        # Skip blank lines at the top
        while start < n and not lines[start].strip():
            start += 1
        if start >= n:
            break
        # Find section end: next heading, or double-blank-line gap
        end = start + 1
        while end < n:
            line = lines[end]
            # Heading starts a new section
            if _HEADING_RE.match(line) or _NUM_SECTION_RE.match(line):
                break
            # Blank line followed by non-blank = paragraph break, could be boundary
            # But we only break on headings to keep sections large
            end += 1
        boundaries.append((start + 1, end))  # 1-based start, exclusive end
        i = end
    return boundaries


def _find_section_for_line(boundaries: list[tuple[int, int]], line: int) -> tuple[int, int] | None:
    """Find the section containing `line` (1-based). Returns (start, end_1based_inclusive)."""
    for start, end in boundaries:
        if start <= line < end:
            return start, end
    return None


def _extract_sections(body: str) -> list[dict]:
    """Extract all sections with headings for --sections mode."""
    lines = body.splitlines()
    result = []
    boundaries = _detect_section_boundaries(lines)
    for start, end in boundaries:
        first_line = lines[start - 1].strip() if start - 1 < len(lines) else ""
        result.append({
            "line": start,
            "end_line": end,
            "heading": first_line[:120],
            "line_count": end - start + 1,
        })
    return result


def _mark_hits(
    lines_data: list[dict],
    tokens: list[str],
) -> list[dict]:
    """Add hit counts to lines data."""
    token_lowers = [t.lower() for t in tokens if t]
    if not token_lowers:
        return lines_data
    for entry in lines_data:
        line_lower = entry["text"].lower()
        entry["hits"] = sum(1 for t in token_lowers if t in line_lower)
    return lines_data


def _read_range(
    lines: list[str],
    start: int,
    count: int,
) -> list[dict]:
    """Read lines[start-1 : start-1+count], return [{line, text, hits:0}]."""
    result = []
    for i in range(max(0, start - 1), min(len(lines), start - 1 + count)):
        result.append({"line": i + 1, "text": lines[i].rstrip(), "hits": 0})
    return result


def cmd_read(args: argparse.Namespace) -> int:
    from .runtime import print_json

    root = resolve_root(args.root)
    doc_ids = [d.strip() for d in args.doc_id.split(",") if d.strip()]
    tokens_str = args.tokens or ""
    tokens = fts_tokens(tokens_str) if tokens_str else []

    # --sections mode: list document structure
    if args.sections:
        if not doc_ids:
            print_json({"tool": "kbtool", "cmd": "read", "error": "doc_id required"})
            return 1
        doc_path = _resolve_doc_path(root, doc_ids[0])
        if not doc_path:
            print_json({"tool": "kbtool", "cmd": "read", "error": f"Document not found: {doc_ids[0]}"})
            return 1
        raw = doc_path.read_text(encoding="utf-8", errors="replace")
        _, body = _strip_frontmatter(raw)
        sections = _extract_sections(body)
        print_json({
            "tool": "kbtool", "cmd": "read", "mode": "sections",
            "doc_id": doc_ids[0],
            "total_lines": len(body.splitlines()),
            "section_count": len(sections),
            "sections": sections[:200],
        })
        return 0

    # Normal read mode - process each doc_id
    all_results = []
    for doc_id in doc_ids:
        doc_path = _resolve_doc_path(root, doc_id)
        if not doc_path:
            all_results.append({"doc_id": doc_id, "error": f"Document not found: {doc_id}"})
            continue

        raw = doc_path.read_text(encoding="utf-8", errors="replace")
        fm_offset, body = _strip_frontmatter(raw)
        lines = body.splitlines()
        total_lines = len(lines)

        read_lines = []
        read_mode = "range"

        # --around mode: auto-detect section boundary
        if args.around and args.around > 0:
            read_mode = "around"
            boundaries = _detect_section_boundaries(lines)
            target_line = args.around
            # Optionally expand to surrounding sections
            expand = max(0, args.expand or 0)
            section = _find_section_for_line(boundaries, target_line)
            if section:
                sec_start, sec_end = section
                # Expand: include N sections before and after
                if expand > 0:
                    sec_idx = boundaries.index(section)
                    exp_start = boundaries[max(0, sec_idx - expand)][0]
                    exp_end = boundaries[min(len(boundaries) - 1, sec_idx + expand)][1]
                    sec_start, sec_end = exp_start, exp_end
                read_lines = _read_range(lines, sec_start, sec_end - sec_start + 1)
            else:
                # Fallback: ±10 lines
                read_lines = _read_range(lines, max(1, target_line - 10), 20)

        # --find mode: search keyword after a line
        elif args.find:
            read_mode = "find"
            find_lower = args.find.lower()
            after_line = max(0, (args.after or 0))
            context = args.context or 10
            for i in range(after_line, total_lines):
                if find_lower in lines[i].lower():
                    match_line = i + 1
                    ctx_start = max(1, match_line - context)
                    read_lines = _read_range(lines, ctx_start, context * 2 + 1)
                    break
            if not read_lines:
                all_results.append({
                    "doc_id": doc_id, "mode": "find",
                    "find": args.find, "after": after_line,
                    "found": False,
                })
                continue

        # --jump mode: multiple ranges
        elif args.jump:
            read_mode = "jump"
            for part in args.jump.split(","):
                part = part.strip()
                if "-" in part:
                    a, b = part.split("-", 1)
                    read_lines.extend(_read_range(lines, int(a), int(b) - int(a) + 1))
                elif part:
                    read_lines.extend(_read_range(lines, int(part), args.count or 20))

        # Default: simple range read
        else:
            read_lines = _read_range(lines, args.start, args.count)

        # Mark hits
        read_lines = _mark_hits(read_lines, tokens)

        # Summarize: count total hits, find hit-dense zones
        total_hits = sum(ln["hits"] for ln in read_lines)
        hit_lines = [ln["line"] for ln in read_lines if ln["hits"] > 0]

        all_results.append({
            "doc_id": doc_id,
            "mode": read_mode,
            "total_lines": total_lines,
            "read_lines": len(read_lines),
            "total_token_hits": total_hits,
            "hit_lines": hit_lines[:30],
            "lines": read_lines,
        })

    # Build payload
    payload = {
        "tool": "kbtool",
        "cmd": "read",
        "tokens": tokens[:10] if tokens else [],
        "docs": all_results,
    }

    # Write to file if --out
    out_path = args.out or ""
    if out_path:
        out = root / out_path
        out.parent.mkdir(parents=True, exist_ok=True)
        md_lines = []
        for dr in all_results:
            if "error" in dr:
                md_lines.append(f"## Error: {dr['doc_id']}\n{dr['error']}\n")
                continue
            md_lines.append(f"## {dr['doc_id']} ({dr['mode']}, {dr['read_lines']} lines, {dr['total_token_hits']} hits)")
            md_lines.append("")
            for ln in dr.get("lines", []):
                marker = " **← HIT**" if ln["hits"] > 0 else ""
                md_lines.append(f"L{ln['line']}: {ln['text']}{marker}")
            md_lines.append("")
        out.write_text("\n".join(md_lines), encoding="utf-8")
        payload["out_path"] = out_path

    print_json(payload)
    return 0
