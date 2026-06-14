from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path

from .runtime import escape_markdown_inline, resolve_root, safe_output_path
from .text import fts_tokens
from .types import CompactPayload, DocResult, FullPayload, LineMatch

# ---------------------------------------------------------------------------
# Tunable constants (named, not magic numbers)
# ---------------------------------------------------------------------------

# BM25 column weights: title 权重高于 body，让 title 直接命中标识符的文档排前面。
# 10:1 是经验值——title 通常比 body 短，权重 10 让 title 命中能压过 body 的 tf 优势。
TITLE_WEIGHT = 10.0
BODY_WEIGHT = 1.0

# 精确标识符匹配的 score boost（极小值，让精确匹配排第一）。
# 归一化后会变成 -1.0（与 FTS top1 同值），但精确匹配已先于 FTS 返回。
EXACT_MATCH_BOOST = -100.0

# 标识符最短长度（normalize 后）——太短的 query 不做精确匹配（避免误命中）。
MIN_IDENTIFIER_LEN = 4

# 匹配行上限（compact stdout 与 runs/*.md 一致）。
MAX_MATCH_LINES = 10

# Query-time window density reranking.
#
# 当 query 含多个 ASCII token（≥ MIN_ASCII_TOKENS_FOR_WINDOW）且文档集是多文档时，
# 全局 BM25 可能让"含全部核心词但被长度归一化削弱的文档"排不到前面，
# 被一个"偶然含 1 个稀有词的文档"压过。这是 BM25 在窄领域多文档场景下的
# 结构性缺陷（核心概念词 df=100%，idf≈0；query 装饰词反而成区分信号）。
#
# 修复：对每个候选文档，扫描原文找含最多 query token 的滑动窗口，
# 按窗口密度排序。这是 chunk-level retrieval 的"懒加载"版本——
# 不预先 chunk，query 时动态定位最高密度窗口。
#
# 触发条件：query 含 ≥ 4 个 ASCII token 且候选文档数 > 1。
# 只看 ASCII token 是因为 CJK 文本被切成 2-gram，4 字 CJK query 就切出 5 个 token，
# 但实际语义内容只有 4 个字——不应触发窗口密度（CJK 短 query 上 BM25 已足够，
# 强行重排反而劣化结果，在红楼梦/民法典等数据集上验证过）。
WINDOW_SIZE_DEFAULT = 20            # 行
WINDOW_SCAN_STEP = 5                # 行
MIN_ASCII_TOKENS_FOR_WINDOW = 4     # ASCII token 数下限


# ---------------------------------------------------------------------------
# FTS5 search — whole-document BM25
# ---------------------------------------------------------------------------


def _normalize_identifier(s: str) -> str:
    """标识符归一化：复用 tokenizer_core 的通解 normalize_alias_text。

    "BS EN 1992-1-1：2004" → "bsen1992112004"
    "第三十三回 ..." → "第三十三回..."

    使用 NFKC 归一化 + 仅保留 CJK/ASCII 字母数字 —— 单一真相源在 tokenizer_core，
    避免维护独立的标点黑名单（漂移风险）。
    """
    from .tokenizer_core import normalize_alias_text
    return normalize_alias_text(s)


def _exact_identifier_match(
    conn: sqlite3.Connection,
    raw_query: str,
    *,
    top_k: int = 10,
    doc_ids: list[str] | None = None,
) -> list[tuple[str, str, str, float]]:
    """query 原文（normalize 后）与 doc_id/doc_title 做精确匹配。

    解决 FTS 字面分词丢失精确标识符的问题：
    - "BS EN 1992-1-1" 切出 ['bs','en','1992']，FTS 无法区分 1992-1-1 vs 1992-3
    - 用原文 normalize 后做双向 substring 匹配

    匹配规则：query 是 doc 标识符的子串，或 doc 标识符是 query 的子串。
    通解，不依赖具体标准号/章节模式。

    返回 [(doc_id, doc_title, source_file, score), ...]，score 用 EXACT_MATCH_BOOST。
    """
    q_norm = _normalize_identifier(raw_query)
    if len(q_norm) < MIN_IDENTIFIER_LEN:
        return []

    doc_filter = ""
    params: list = []
    if doc_ids:
        placeholders = ",".join("?" for _ in doc_ids)
        doc_filter = f" AND doc_id IN ({placeholders})"
        params.extend(doc_ids)

    rows = conn.execute(
        f"""
        SELECT doc_id, doc_title, source_file FROM docs
        WHERE is_active = 1{doc_filter}
        """,
        params,
    ).fetchall()

    matches = []
    for r in rows:
        doc_id_norm = _normalize_identifier(str(r["doc_id"]))
        doc_title_norm = _normalize_identifier(str(r["doc_title"]))
        # 双向 substring：query 是 doc 标识符的子串，或 doc 标识符是 query 的子串
        if (doc_id_norm and (doc_id_norm in q_norm or q_norm in doc_id_norm)) or \
           (doc_title_norm and (doc_title_norm in q_norm or q_norm in doc_title_norm)):
            matches.append((str(r["doc_id"]), str(r["doc_title"]), str(r["source_file"]), EXACT_MATCH_BOOST))
        if len(matches) >= top_k:
            break
    return matches


def _window_density_score(
    text: str,
    query_tokens: set[str],
    *,
    window_size: int = WINDOW_SIZE_DEFAULT,
    scan_step: int = WINDOW_SCAN_STEP,
) -> int:
    """扫描文档，找含最多 query token 的滑动窗口。返回最大窗口的匹配数。

    纯词法、确定性、O(|D|/scan_step) 复杂度。
    """
    if not query_tokens or not text:
        return 0
    lines = text.split("\n")
    if not lines:
        return 0
    if len(lines) <= window_size:
        # 文档短于窗口：扫描整个文档
        window = text.lower()
        window_tokens = set(re.findall(r"[a-z]+", window))
        # CJK 文本 re.findall(r"[a-z]+") 只匹配 ASCII；CJK token 已被 fts_tokens 切为 2-gram，
        # 此处直接做子串匹配
        cjk_matched = sum(1 for t in query_tokens if t and t in window)
        ascii_matched = len(query_tokens & window_tokens)
        return max(cjk_matched, ascii_matched)

    best = 0
    for start in range(0, len(lines) - window_size + 1, scan_step):
        window = "\n".join(lines[start:start + window_size]).lower()
        # ASCII tokens: regex match
        window_ascii_tokens = set(re.findall(r"[a-z]+", window))
        ascii_matched = len(query_tokens & window_ascii_tokens)
        # CJK tokens: substring match
        cjk_matched = sum(1 for t in query_tokens if t and len(t) >= 2 and not t.isascii() and t in window)
        matched = ascii_matched + cjk_matched
        if matched > best:
            best = matched
    return best


def _apply_window_density(
    conn: sqlite3.Connection,
    merged: list[tuple[str, str, str, float]],
    query_tokens: set[str],
    *,
    window_size: int = WINDOW_SIZE_DEFAULT,
    scan_step: int = WINDOW_SCAN_STEP,
) -> list[tuple[str, str, str, float]]:
    """对 BM25 候选应用窗口密度重排。返回重排后的列表。

    前提：merged 已按 BM25 排好序。本函数按窗口密度（max matched tokens
    in any sliding window）重新排序；窗口密度相同时保留原 BM25 顺序。
    """
    if not merged or not query_tokens:
        return merged

    scored: list[tuple[int, int, tuple[str, str, str, float]]] = []
    # 预加载 source_path
    source_files = [m[2] for m in merged]
    path_rows = conn.execute(
        f"SELECT source_file, source_path FROM docs WHERE source_file IN "
        f"({','.join('?' for _ in source_files)}) AND is_active = 1",
        source_files,
    ).fetchall()
    path_map = {str(r[0]): str(r[1]) for r in path_rows}

    for bm25_rank, item in enumerate(merged):
        source_file = item[2]
        path = path_map.get(source_file)
        if not path:
            scored.append((0, bm25_rank, item))
            continue
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except Exception:
            scored.append((0, bm25_rank, item))
            continue
        density = _window_density_score(text, query_tokens, window_size=window_size, scan_step=scan_step)
        scored.append((density, bm25_rank, item))

    # 排序：density 降序，BM25 rank 升序（保留原顺序）
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [item for _, _, item in scored]


def _search_docs(
    conn: sqlite3.Connection,
    raw_query: str,
    *,
    top_k: int = 10,
    doc_ids: list[str] | None = None,
    window_density: bool = True,
    window_size: int = WINDOW_SIZE_DEFAULT,
) -> list[tuple[str, str, str, float]]:
    """Search doc_fts and return top-K documents with BM25 scores.

    Returns: [(doc_id, doc_title, source_file, bm25_score), ...]

    When ``window_density=True`` (default) and the query has ≥ MIN_TOKENS_FOR_WINDOW
    tokens, applies query-time window-density reranking to better handle long
    queries on multi-document narrow-domain corpora. This is a no-op for short
    queries (e.g. CJK short queries), preserving BM25 behavior unchanged.
    """
    # 先尝试精确标识符匹配（解决 FTS 切词丢失标识符的问题）
    exact_matches = _exact_identifier_match(conn, raw_query, top_k=top_k, doc_ids=doc_ids)

    tokens = fts_tokens(raw_query)
    if not tokens:
        return exact_matches

    # Build FTS match expression: OR of all tokens
    match_expr = " OR ".join('"' + t.replace('"', '""') + '"' for t in tokens[:64])

    doc_filter_sql = ""
    doc_filter_params: tuple = ()
    if doc_ids:
        filtered = [str(d).strip() for d in doc_ids if str(d).strip()]
        if filtered:
            placeholders = ",".join("?" for _ in filtered)
            doc_filter_sql = f" AND d.doc_id IN ({placeholders})"
            doc_filter_params = tuple(filtered)

    # 取 top_k * 3 作为候选池，给 window density 更多重排空间
    candidate_k = top_k * 3 if window_density else top_k

    rows = conn.execute(
        f"""
        SELECT d.doc_id, d.doc_title, d.source_file, bm25(doc_fts, ?, ?) AS rank
        FROM doc_fts
        JOIN docs d ON d.doc_row_id = doc_fts.rowid
        WHERE doc_fts MATCH ? AND d.is_active = 1{doc_filter_sql}
        ORDER BY rank
        LIMIT ?
        """,
        (TITLE_WEIGHT, BODY_WEIGHT, match_expr) + doc_filter_params + (candidate_k,),
    ).fetchall()

    fts_results = [
        (str(r["doc_id"]), str(r["doc_title"]), str(r["source_file"]), float(r["rank"]))
        for r in rows
    ]

    # 合并：精确匹配优先，FTS 结果补充（去重）
    if exact_matches:
        exact_ids = {m[0] for m in exact_matches}
        fts_results = [r for r in fts_results if r[0] not in exact_ids]
        merged = exact_matches + fts_results[: max(0, candidate_k - len(exact_matches))]
    else:
        merged = fts_results

    # Score 归一化：top1 = -1.0，其他按比例。
    # 解决跨库 BM25 score 数量级差异（单文档库 -1e-5，多文档库 -1e+0），
    # 让模型看到的 score 跨库一致。不影响 out_of_scope（用 long_token 命中）
    # 或 status（用 matches 行数）——它们都不依赖绝对 score。
    if merged:
        top1_abs = abs(merged[0][3])
        if top1_abs > 0:
            merged = [(d[0], d[1], d[2], d[3] / top1_abs) for d in merged]

    # Query-time window density reranking (新增)。
    # 仅在 query 含 ≥ 4 个 ASCII token 且多候选时触发；
    # CJK 短 query（被切为 2-gram token）不触发，BM25 已足够。
    if window_density and len(merged) > 1:
        ascii_tokens = {t for t in tokens if t.isascii()}
        if len(ascii_tokens) >= MIN_ASCII_TOKENS_FOR_WINDOW:
            merged = _apply_window_density(
                conn, merged, ascii_tokens, window_size=window_size
            )

    return merged[:top_k]


# ---------------------------------------------------------------------------
# Line-level match location
# ---------------------------------------------------------------------------


def _find_matching_lines(
    body_text: str,
    tokens: list[str],
    *,
    max_matches: int = 10,
) -> list[LineMatch]:
    """Find lines containing any search token, with 1-based line numbers.

    Returns up to max_matches matches, sorted by hit count (lines matching
    more tokens rank first), then by line number for ties.
    """
    if not body_text or not tokens:
        return []

    token_lowers = [t.lower() for t in tokens if t]
    raw: list[tuple[int, int, str]] = []  # (hit_count, line_num, text)

    for line_num, line in enumerate(body_text.splitlines(), start=1):
        line_lower = line.lower()
        hit_count = sum(1 for t in token_lowers if t in line_lower)
        if hit_count > 0:
            raw.append((hit_count, line_num, line.rstrip()))

    # Sort: more token hits first, then earlier lines first
    raw.sort(key=lambda x: (-x[0], x[1]))

    return [{"line": ln, "text": text} for _, ln, text in raw[:max_matches]]


def _get_doc_body(conn: sqlite3.Connection, doc_id: str, skill_root: Path) -> str:
    """Retrieve full document body text from the original references/ files.

    docs.body in SQLite stores pre-tokenized text (n-gram joined by spaces)
    for FTS indexing only — not usable for line-level matching. Original
    text lives in references/{doc_id}/doc.md, preserving full semantic
    integrity and accurate line numbers.
    """
    # references/{doc_id}/doc.md contains the original text
    ref_path = skill_root / "references" / doc_id / "doc.md"
    if ref_path.is_file():
        text = ref_path.read_text(encoding="utf-8", errors="replace")
        # Strip YAML frontmatter
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end != -1:
                text = text[end + 4:]
        return text.strip()

    # No fallback: tokenized docs.body is not usable for line matching.
    # Missing references file is a build-time integrity error, not a runtime
    # degradation case.
    return ""


# ---------------------------------------------------------------------------
# Line offset lookup (unused — kept for reference)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _render_query_markdown(results: list[DocResult], query: str) -> str:
    """Render query results as markdown for AI consumption."""
    lines: list[str] = [
        "# Query Results\n\n",
        f"- query: `{escape_markdown_inline(query)}`\n",
        f"- documents: {len(results)}\n\n",
    ]

    if not results:
        lines.append("*No matching documents found.*\n\n")
        return "".join(lines)

    for doc_result in results:
        file_name = str(doc_result.get("file") or "")
        title = str(doc_result.get("title") or "")
        score = doc_result.get("score")
        matches = doc_result.get("matches") or []

        lines.append(f"## {escape_markdown_inline(title)} (`{escape_markdown_inline(file_name)}`)\n\n")
        if score is not None:
            lines.append(f"- bm25_score: {score:.4f}\n")
        if matches:
            lines.append(f"- matches: {len(matches)} line(s)\n")
        lines.append("\n")

        if matches:
            lines.append("| line | text |\n|---|---|\n")
            for m in matches:
                line_text = escape_markdown_inline(str(m.get("text") or ""))
                lines.append(f"| {m.get('line', '?')} | {line_text} |\n")
            lines.append("\n")

    return "".join(lines)


def _render_evidence_summary_markdown(
    *,
    results: list[DocResult],
    query: str,
    match: str,
    out_of_scope: bool = False,
) -> str:
    """Render evidence summary for compact mode."""
    if out_of_scope:
        return (
            "# Evidence Summary\n\n"
            "## Decision Summary\n\n"
            f"- query: `{escape_markdown_inline(query)}`\n"
            "- status: `no_hits`\n"
            "- reason: `out_of_scope` — 查询中的 3+gram 短语均未出现，"
            "FTS 命中来自 2-gram 字符偶合（如\"悟空\"匹配\"自色悟空\"），非真实证据。\n"
            f"- fts_match: `{escape_markdown_inline(match)}`\n"
            "- next_action: `out_of_scope`\n\n"
            "## Matching Documents\n\n"
            "*查询超出本知识库范围。请确认问题是否属于本文档集，"
            "或换用文档中存在的术语重试。*\n"
        )

    lines: list[str] = [
        "# Evidence Summary\n\n",
        "## Decision Summary\n\n",
        f"- query: `{escape_markdown_inline(query)}`\n",
        f"- status: `{'high_confidence' if results else 'no_hits'}`\n",
    ]
    if match:
        lines.append(f"- fts_match: `{escape_markdown_inline(match)}`\n")

    if results:
        lines.append(f"- next_action: `read_file_at_lines`\n")
    else:
        lines.append("- next_action: `retry_broader_or_stop`\n")

    lines.append("\n## Matching Documents\n\n")

    if not results:
        lines.append("*No matching documents found.*\n\n")
        return "".join(lines)

    for doc_result in results:
        file_name = str(doc_result.get("file") or "")
        title = str(doc_result.get("title") or "")
        score = doc_result.get("score")
        matches = doc_result.get("matches") or []

        lines.append(f"### {escape_markdown_inline(title)}\n\n")
        if score is not None:
            lines.append(f"- score: {score:.4f}\n")
        lines.append(f"- file: `{escape_markdown_inline(file_name)}`\n")
        if matches:
            lines.append(f"- matching_lines: {len(matches)}\n")
            for m in matches[:10]:
                lines.append(f"  - L{m.get('line', '?')}: {escape_markdown_inline(str(m.get('text') or ''))}\n")
        lines.append("\n")

    return "".join(lines)


# ---------------------------------------------------------------------------
# Compact stdout payload
# ---------------------------------------------------------------------------


def _should_compact_stdout(args: argparse.Namespace) -> bool:
    stdout_mode = str(getattr(args, "stdout", "auto") or "auto").strip().lower()
    if stdout_mode not in {"auto", "compact", "full"}:
        from .runtime import die
        die("--stdout must be auto/compact/full")
    return stdout_mode == "compact" or (
        stdout_mode == "auto" and bool(str(getattr(args, "out", "") or "").strip())
    )


def _compact_empty_payload(query: str, out_path: str | None = None, *, out_of_scope: bool = False) -> CompactPayload:
    payload: CompactPayload = {
        "tool": "kbtool",
        "cmd": "query",
        "query": query,
        "status": "no_hits",
        "fts_match": "",
        "results": [],
        "next_action": "out_of_scope" if out_of_scope else "retry_broader_or_stop",
    }
    if out_path:
        payload["out_path"] = out_path
    return payload


def _has_long_token_hit(tokens: list[str], matches: list[LineMatch]) -> bool:
    """单信号：3+gram 在匹配行直接出现 = in-domain。

    FTS5 在 CJK 2-gram 切分下会因字符偶合命中 out-of-domain 查询
    （如"悟空"匹配"自色悟空"）。但 3+gram 的字面连续命中极难偶合：
    query "孙悟空三打白骨精" 的 3-gram 在红楼梦里全部 0 命中。

    短 query（无 3+gram，如"贾瑞"2字）不做此检查，返回 True。
    """
    long_tokens = [t for t in tokens if len(t) >= 3]
    if not long_tokens:
        return True
    for m in matches:
        text_lower = m.get("text", "").lower()
        if not text_lower:
            continue
        for t in long_tokens:
            if t in text_lower:
                return True
    return False


def _empty_query_payload(query: str, render_fn) -> FullPayload:
    return {
        "tool": "kbtool",
        "cmd": "query",
        "query": query,
        "results": [],
        "out": render_fn(query),
    }


def _write_query_output(root: Path, args: argparse.Namespace, payload: FullPayload) -> FullPayload:
    out_arg = str(getattr(args, "out", "") or "").strip()
    if not out_arg:
        return payload

    out_path = safe_output_path(root, out_arg)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(str(payload.get("out") or ""), encoding="utf-8", newline="\n")
    payload["out_path"] = str(out_path.relative_to(root)).replace("\\", "/")
    return payload


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def cmd_query(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    state_conn: sqlite3.Connection | None = None,
) -> CompactPayload | FullPayload:
    """Whole-document query: FTS5 BM25 search + line-level matching.

    Flow:
    1. FTS5 + BM25 ranking -> top-K matching documents
    2. For each document, find exact matching lines
    3. Return: [(file_path, line_number, matching_line_text, bm25_score), ...]
    """
    root = resolve_root(getattr(args, "root", "") or "")
    raw_query = str(getattr(args, "query", "") or "").strip()
    compact_stdout = _should_compact_stdout(args)
    top_k = max(1, int(getattr(args, "limit", 10) or 10))

    # Doc ID filter
    doc_ids_raw = str(getattr(args, "doc_ids", "") or "").strip()
    doc_ids = [d.strip() for d in doc_ids_raw.split(",") if d.strip()] if doc_ids_raw else None

    if not raw_query:
        payload = _write_query_output(
            root,
            args,
            _empty_query_payload(
                "",
                lambda q: _render_evidence_summary_markdown(results=[], query=q, match="")
                if compact_stdout
                else _render_query_markdown([], q),
            ),
        )
        if compact_stdout:
            return _compact_empty_payload("", str(payload.get("out_path") or "") or None)
        return payload

    # 1. FTS5 BM25 search
    tokens = fts_tokens(raw_query)
    search_results = _search_docs(conn, raw_query, top_k=top_k, doc_ids=doc_ids)

    # No LIKE fallback: external-content FTS5 has no doc_fts_content table,
    # and FTS misses are handled by the out_of_scope rejection gate below
    # (long-token signal) plus the agent's retry-with-different-keywords flow.

    if not search_results:
        payload = _write_query_output(
            root,
            args,
            _empty_query_payload(
                raw_query,
                lambda q: _render_evidence_summary_markdown(results=[], query=q, match="")
                if compact_stdout
                else _render_query_markdown([], q),
            ),
        )
        if compact_stdout:
            return _compact_empty_payload(raw_query, str(payload.get("out_path") or "") or None)
        return payload

    # 2. Build results with line-level matches
    results: list[DocResult] = []
    for doc_id, doc_title, source_file, score in search_results:
        body = _get_doc_body(conn, doc_id, root)
        matching_lines = _find_matching_lines(body, tokens, max_matches=MAX_MATCH_LINES)
        results.append({
            "file": source_file,
            "doc_id": doc_id,
            "title": doc_title,
            "score": score,
            "matches": matching_lines,
        })

    # 3. Build FTS match expression for reporting
    match_expr = " OR ".join('"' + t.replace('"', '""') + '"' for t in tokens[:64]) if tokens else ""

    # 4. Out-of-domain rejection: query 必须在匹配行中展现 in-domain 信号
    #    （3+gram 字面命中，或核心 bigram 在多行命中），否则视为 out-of-domain。
    long_tokens = [t for t in tokens if len(t) >= 3]
    is_out_of_scope = bool(long_tokens) and not _has_long_token_hit(tokens, [m for r in results for m in r["matches"]])
    if is_out_of_scope:
        # Drop the noise matches so the agent is not misled.
        results = []

    # 5. Determine status and next action
    if is_out_of_scope:
        status = "no_hits"
        next_action = "out_of_scope"
    else:
        has_strong_match = any(len(r["matches"]) >= 2 for r in results[:2])
        status = "high_confidence" if has_strong_match else "needs_verification"
        next_action = "read_file_at_lines" if results else "retry_broader_or_stop"

    # 6. Markdown rendering
    md = (
        _render_evidence_summary_markdown(
            results=results, query=raw_query, match=match_expr, out_of_scope=is_out_of_scope
        )
        if compact_stdout
        else _render_query_markdown(results, raw_query)
    )

    full_payload: FullPayload = {
        "tool": "kbtool",
        "cmd": "query",
        "query": raw_query,
        "results": results,
        "out": md,
    }

    payload = _write_query_output(root, args, full_payload)

    if compact_stdout:
        compact: CompactPayload = {
            "tool": "kbtool",
            "cmd": "query",
            "query": raw_query,
            "status": status,
            "fts_match": match_expr,
            "results": results[:5],
            "next_action": next_action,
        }
        out_path = str(payload.get("out_path") or "") or None
        if out_path:
            compact["out_path"] = out_path
        return compact

    return payload
