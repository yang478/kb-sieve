"""
Canonical tokenization core — shared between build-time and runtime.

This is the SINGLE source of truth for all tokenization, normalization,
and hashing functions used by both build_skill (indexing) and kbtool (query).
Any change here affects both build and runtime identically.

IMPORTANT: This module has zero external dependencies. Do NOT add imports
from other pack-builder or kbtool modules — that would break the contract.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence

# ---------------------------------------------------------------------------
# CJK detection & ranges
# ---------------------------------------------------------------------------

# Complete Unicode 15.1 CJK Unified Ideograph ranges (Extensions A–I),
# plus CJK Compatibility Ideographs and Compatibility Ideographs Supplement.
_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs (most frequent — checked first)
    (0x3400, 0x4DBF),  # CJK Unified Ideographs Extension A
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
    (0x20000, 0x2A6DF),  # CJK Unified Ideographs Extension B
    (0x2A700, 0x2B73F),  # CJK Unified Ideographs Extension C
    (0x2B740, 0x2B81F),  # CJK Unified Ideographs Extension D
    (0x2B820, 0x2CEAF),  # CJK Unified Ideographs Extension E
    (0x2CEB0, 0x2EBEF),  # CJK Unified Ideographs Extension F
    (0x2EBF0, 0x2EE5F),  # CJK Unified Ideographs Extension I
    (0x30000, 0x3134F),  # CJK Unified Ideographs Extension G
    (0x31350, 0x323AF),  # CJK Unified Ideographs Extension H
    (0x2F800, 0x2FA1F),  # CJK Compatibility Ideographs Supplement
)


# Pre-built regex character class covering all CJK ranges above.
# Used by normalize_alias_text so that it stays in sync with is_cjk().
_CJK_RE_CHAR_CLASS = (
    r"0-9a-z"
    r"\u3400-\u4DBF"
    r"\u4E00-\u9FFF"
    r"\uF900-\uFAFF"
    r"\U00020000-\U0002A6DF"
    r"\U0002A700-\U0002B73F"
    r"\U0002B740-\U0002B81F"
    r"\U0002B820-\U0002CEAF"
    r"\U0002CEB0-\U0002EBEF"
    r"\U0002EBF0-\U0002EE5F"
    r"\U00030000-\U0003134F"
    r"\U00031350-\U000323AF"
    r"\U0002F800-\U0002FA1F"
)


def is_cjk(ch: str) -> bool:
    """Return True if *ch* is a CJK unified ideograph or compatibility ideograph."""
    o = ord(ch)
    # Fast path: ~97 % of everyday CJK text falls in the Unified block.
    if 0x4E00 <= o <= 0x9FFF:
        return True
    return any(lo <= o <= hi for lo, hi in _CJK_RANGES[1:])


# ---------------------------------------------------------------------------
# Tokenization for FTS5
# ---------------------------------------------------------------------------

# Maximum length of a CJK run that is emitted as a full-word token in addition
# to its constituent 2-grams.  Aligns with _MAX_CJK_KEYWORD_LEN so that common
# 5-6 character technical/legal terms (e.g. "钢筋混凝土") are preserved for
# exact-match ranking boosts.
_MAX_SHORT_WORD_LEN = 6

_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9_]{2,}")


def tokenize_cjk_ngram(text: str) -> list[str]:
    """Tokenize CJK runs into overlapping 2-gram, 3-gram, and 4-gram tokens.

    Runs whose length is between 2 and ``_MAX_SHORT_WORD_LEN`` (inclusive) are
    emitted as a full-word token *in addition to* their n-grams.  This gives
    exact-match phrases a BM25 score boost while the 2/3/4-gram mix provides
    much better discrimination than pure 2-gram (e.g. "灵宝" noise is reduced
    because longer n-grams carry more context).

    Tokens are deduplicated within each run — the same substring appears only
    once even if it can be formed at multiple n-gram levels.
    """
    tokens: list[str] = []
    run: list[str] = []

    def flush() -> None:
        n = len(run)
        if n == 0:
            return
        seen: set[str] = set()
        # Emit the full run for short phrases (avoids the old n=4→5 cliff).
        if 2 <= n <= _MAX_SHORT_WORD_LEN:
            full = "".join(run)
            tokens.append(full)
            seen.add(full)
        # Emit overlapping 2-gram, 3-gram, and 4-gram (deduplicated).
        if n >= 2:
            for gram_len in (2, 3, 4):
                if gram_len > n:
                    break
                for i in range(n - gram_len + 1):
                    gram = "".join(run[i : i + gram_len])
                    if gram not in seen:
                        seen.add(gram)
                        tokens.append(gram)
        elif n == 1:
            tokens.append(run[0])
        run.clear()

    for ch in text:
        if is_cjk(ch):
            run.append(ch)
        else:
            flush()
    flush()
    return tokens


# CJK punctuation normalization: map full-width and variant punctuation
# to their ASCII/half-width equivalents before tokenization.
_CJK_PUNCT_TRANS = str.maketrans(
    "，、：；（）「」『』【】《》〈〉…——～·",
    ",,:;()[][][]《》〈〉…——~·",
)


def fts_tokens(text: str) -> list[str]:
    text = text.translate(_CJK_PUNCT_TRANS)
    tokens: list[str] = []
    tokens.extend(tokenize_cjk_ngram(text))
    tokens.extend(m.group(0).lower() for m in _ASCII_WORD_RE.finditer(text))
    # Query-time stop-word filtering: safe because the index remains complete.
    tokens = [t for t in tokens if t not in _CJK_STOP_WORDS and t not in _EN_STOP_WORDS]
    return tokens


# ---------------------------------------------------------------------------
# FTS match expression builders
# ---------------------------------------------------------------------------


def _build_match_clauses(tokens: Sequence[str], *, max_tokens: int, warn: bool = False) -> list[str]:
    safe: list[str] = []
    seen: set[str] = set()
    for t in tokens:
        t = t.replace('"', '""')
        if not t or t in seen:
            continue
        seen.add(t)
        safe.append(f'"{t}"')
        if len(safe) >= max_tokens:
            if warn:
                import sys

                print(f"[WARN] FTS query truncated: {len(tokens)} tokens, using first {max_tokens}", file=sys.stderr)
            break
    return safe


def build_match_query(tokens: Sequence[str], *, max_tokens: int = 64) -> str:
    clauses = _build_match_clauses(tokens, max_tokens=max_tokens, warn=True)
    return " OR ".join(clauses) if clauses else ""


def build_match_all(tokens: Sequence[str], *, max_tokens: int = 16) -> str:
    clauses = _build_match_clauses(tokens, max_tokens=max_tokens)
    return " AND ".join(clauses) if clauses else ""


def query_terms(raw_query: str) -> list[str]:
    q = raw_query.strip()
    if not q:
        return []
    parts = [p.strip() for p in re.split(r"\s+", q) if p.strip()]
    if parts:
        return parts
    return [q]


def build_match_expression(
    raw_query: str,
    *,
    query_mode: str,
    must_terms: Sequence[str],
    max_tokens: int = 64,
) -> str:
    must_clauses: list[str] = []
    for t in must_terms:
        clause = build_match_all(fts_tokens(t), max_tokens=16)
        if clause:
            must_clauses.append(f"({clause})" if " AND " in clause else clause)

    query_clause = ""
    if query_mode == "and":
        parts = query_terms(raw_query)
        q_clauses: list[str] = []
        for p in parts:
            clause = build_match_all(fts_tokens(p), max_tokens=16)
            if not clause:
                continue
            q_clauses.append(f"({clause})" if " AND " in clause else clause)
        query_clause = " AND ".join(q_clauses)
    else:
        query_clause = build_match_query(fts_tokens(raw_query), max_tokens=max_tokens)

    clauses = [c for c in must_clauses + ([query_clause] if query_clause else []) if c]
    if not clauses:
        return ""
    if len(clauses) == 1:
        return clauses[0]
    return " AND ".join(f"({c})" if (" OR " in c or " AND " in c) else c for c in clauses)


# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------


def count_occurrences(haystack: str, needle: str) -> int:
    if not haystack or not needle:
        return 0
    return haystack.count(needle)


def extract_window(text: str, terms: Sequence[str], max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    s = text
    idx = -1
    for t in terms:
        if not t:
            continue
        j = s.find(t)
        if j != -1 and (idx == -1 or j < idx):
            idx = j
    if idx == -1:
        return s[:max_chars]
    start = max(0, idx - max_chars // 3)
    end = min(len(s), start + max_chars)
    if end - start < max_chars:
        start = max(0, end - max_chars)
    snippet = s[start:end]
    if start > 0:
        snippet = "… " + snippet
    if end < len(s):
        snippet = snippet + " …"
    return snippet


# ---------------------------------------------------------------------------
# Markdown → plain text
# ---------------------------------------------------------------------------


def markdown_to_plain(md: str) -> str:
    out_lines: list[str] = []
    for raw in md.splitlines():
        line = raw.strip()
        if not line:
            out_lines.append("")
            continue
        line = re.sub(r"^#{1,6}\s+", "", line)
        line = re.sub(r"`([^`]*)`", r"\1", line)
        line = re.sub(r"\[(.*?)\]\((.*?)\)", r"[\1]", line)
        line = line.replace("**", "").replace("__", "").replace("*", "")
        out_lines.append(line)
    return "\n".join(out_lines).strip() + "\n"


# ---------------------------------------------------------------------------
# Markdown frontmatter
# ---------------------------------------------------------------------------


def parse_frontmatter(md: str) -> dict[str, str]:
    if not md.startswith("---"):
        return {}
    parts = md.split("---", 2)
    if len(parts) < 3:
        return {}
    fm: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        fm[k.strip()] = v.strip().strip('"')
    return fm


def strip_frontmatter(md: str) -> str:
    if not md.startswith("---"):
        return md
    parts = md.split("---", 2)
    if len(parts) < 3:
        return md
    body = parts[2]
    return body.lstrip("\r\n")


# ---------------------------------------------------------------------------
# Hashing & identifiers
# ---------------------------------------------------------------------------


def stable_hash(text: str) -> str:
    """Return a SHA-256 hex digest of *text* (64 chars).

    SHA-256 replaces the legacy SHA-1 implementation to mitigate known
    collision attacks.  Old skills that were built with SHA-1 are not
    affected because each skill carries its own copy of tokenizer_core.py.
    """
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def stable_hash_sha1(text: str) -> str:
    """Return the legacy SHA-1 hex digest (40 chars).

    Use this *only* when you need to verify hashes produced by older
    versions of the tokenizer (e.g. migration scripts).
    """
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()


def node_key(node_id: str, source_version: str) -> str:
    return f"{node_id}@{source_version}"


# ---------------------------------------------------------------------------
# Punctuation-tolerant regex (for search fallback)
# ---------------------------------------------------------------------------

_REGEX_META = set(r".^$*+?{}[]\|()")

_PUNCT_EQUIV_GROUPS: list[set[str]] = [
    {",", "，"},
    {".", "。"},
    {":", "："},
    {";", "；"},
    {"?", "？"},
    {"!", "！"},
]
_PUNCT_EQUIV: dict[str, set[str]] = {ch: group for group in _PUNCT_EQUIV_GROUPS for ch in group}


def _escape_char_class(ch: str) -> str:
    if ch in {"\\", "]", "-", "^"}:
        return "\\" + ch
    return ch


def build_punctuation_tolerant_regex(literal: str) -> str:
    text = str(literal or "")
    if not text:
        return ""
    out: list[str] = []
    for ch in text:
        group = _PUNCT_EQUIV.get(ch)
        if group:
            ordered = [ch] + [c for c in sorted(group) if c != ch]
            out.append("[" + "".join(_escape_char_class(c) for c in ordered) + "]")
            continue
        if ch in _REGEX_META:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------

_VERSION_RE = re.compile(r"\bV(?P<num>\d+)\b", re.IGNORECASE)


def derive_source_version(name: str, title: str) -> str:
    match = _VERSION_RE.search(title) or _VERSION_RE.search(name)
    if match:
        return f"v{match.group('num')}"
    return "current"


def normalize_alias_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return re.sub(rf"[^{_CJK_RE_CHAR_CLASS}]+", "", text)


# ---------------------------------------------------------------------------
# Stop words
# ---------------------------------------------------------------------------

# Module-level frozensets — built once, O(1) lookup, shared across calls.
_CJK_STOP_WORDS: frozenset[str] = frozenset(
    {
        # --- 核心语法虚词 ---
        "的", "了", "在", "是", "有", "和", "就", "不", "都", "也",
        "着", "与", "及", "等", "或", "但", "而", "为", "以", "于",
        "被", "把", "让", "给", "向", "从", "对", "将", "还", "只",
        "又", "再", "更", "最", "之", "地", "得",
        # --- 人称代词 ---
        "我", "你", "他", "她", "它", "我们", "你们", "他们", "她们", "它们",
        "自己", "大家", "别人",
        # --- 疑问词 ---
        "什么", "怎么", "为什么", "哪里", "谁", "多少", "怎样", "如何", "何时",
        # --- 核心连词 ---
        "因为", "所以", "因此", "于是", "但是", "然而", "不过",
        "如果", "那么", "虽然", "尽管", "即使", "除非", "除了",
        "无论", "不管", "不论", "而且", "并且", "以及", "或者", "还是",
        "否则", "不然", "不但", "不仅", "因而", "从而",
        # --- 核心介词 ---
        "关于", "对于", "至于", "由于", "鉴于", "基于",
        # --- 助动词/情态词 ---
        "可以", "可能", "应该", "能够", "需要", "必须",
        "不可", "不能", "不会", "不得", "不要", "不必",
        # --- 否定词 ---
        "没有", "未", "别",
        # --- 指示代词 ---
        "这", "那", "这个", "那个", "这些", "那些", "这样", "那样",
        "如此", "一些", "其",
        # --- 程度副词(核心) ---
        "很", "太", "非常", "十分", "更",
        # --- 时间词(核心) ---
        "已经", "正在", "曾经", "现在", "然后", "以后", "之后", "之前",
        "一直", "始终",
        # --- 趋向动词(核心) ---
        "起来", "下去", "出来", "过去", "下来", "上去", "进来",
        # --- 其他高频虚词 ---
        "便", "却", "到", "会", "要", "能",
        "跟", "同", "并", "按", "当", "比",
        "为了", "作为", "第",
    }
)

_EN_STOP_WORDS: frozenset[str] = frozenset(
    {
        "a",
        "about",
        "above",
        "after",
        "again",
        "against",
        "all",
        "am",
        "an",
        "and",
        "any",
        "are",
        "aren't",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "but",
        "by",
        "can't",
        "cannot",
        "could",
        "couldn't",
        "did",
        "didn't",
        "do",
        "does",
        "doesn't",
        "doing",
        "don't",
        "down",
        "during",
        "each",
        "few",
        "for",
        "from",
        "further",
        "had",
        "hadn't",
        "has",
        "hasn't",
        "have",
        "haven't",
        "having",
        "he",
        "he'd",
        "he'll",
        "he's",
        "her",
        "here",
        "here's",
        "hers",
        "herself",
        "him",
        "himself",
        "his",
        "how",
        "how's",
        "i",
        "i'd",
        "i'll",
        "i'm",
        "i've",
        "if",
        "in",
        "into",
        "is",
        "isn't",
        "it",
        "it's",
        "its",
        "itself",
        "let's",
        "me",
        "more",
        "most",
        "mustn't",
        "my",
        "myself",
        "no",
        "nor",
        "not",
        "of",
        "off",
        "on",
        "once",
        "only",
        "or",
        "other",
        "ought",
        "our",
        "ours",
        "ourselves",
        "out",
        "over",
        "own",
        "same",
        "shan't",
        "she",
        "she'd",
        "she'll",
        "she's",
        "should",
        "shouldn't",
        "so",
        "some",
        "such",
        "than",
        "that",
        "that's",
        "the",
        "their",
        "theirs",
        "them",
        "themselves",
        "then",
        "there",
        "there's",
        "these",
        "they",
        "they'd",
        "they'll",
        "they're",
        "they've",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "until",
        "up",
        "very",
        "was",
        "wasn't",
        "we",
        "we'd",
        "we'll",
        "we're",
        "we've",
        "were",
        "weren't",
        "what",
        "what's",
        "when",
        "when's",
        "where",
        "where's",
        "which",
        "while",
        "who",
        "who's",
        "whom",
        "why",
        "why's",
        "with",
        "won't",
        "would",
        "wouldn't",
        "you",
        "you'd",
        "you'll",
        "you're",
        "you've",
        "your",
        "yours",
        "yourself",
        "yourselves",
        # Legacy short forms that were present in the original inline set
        "boy",
        "can",
        "day",
        "first",
        "get",
        "good",
        "great",
        "just",
        "know",
        "like",
        "long",
        "make",
        "many",
        "may",
        "might",
        "much",
        "never",
        "new",
        "now",
        "old",
        "one",
        "said",
        "say",
        "see",
        "shall",
        "still",
        "take",
        "tell",
        "think",
        "time",
        "try",
        "two",
        "use",
        "want",
        "way",
        "well",
        "will",
    }
)

_MAX_CJK_KEYWORD_LEN = 6


def _extract_cjk_runs(text: str) -> list[str]:
    runs: list[str] = []
    run: list[str] = []
    for ch in text:
        if is_cjk(ch):
            run.append(ch)
        else:
            if len(run) >= 2:
                runs.append("".join(run))
            run = []
    if len(run) >= 2:
        runs.append("".join(run))
    return runs


def _substrings_of_run(run: str, min_len: int = 2, max_len: int = 6) -> list[str]:
    substrings: list[str] = []
    n = len(run)
    for length in range(min_len, min(max_len + 1, n + 1)):
        for i in range(n - length + 1):
            substrings.append(run[i : i + length])
    return substrings


def extract_keywords(text: str, *, top_k: int = 8, min_freq: int = 2) -> list[str]:
    """Extract keywords from *text* using frequency-ranked CJK substrings and ASCII words.

    Args:
        text: Source text.
        top_k: Maximum number of keywords to return.
        min_freq: Minimum occurrence count for a candidate to be considered.
            The default is 2 to avoid single-occurrence noise.
    """
    text = str(text or "").strip()
    if not text:
        return []

    counter: Counter[str] = Counter()

    # 1. CJK continuous runs → substrings
    for run in _extract_cjk_runs(text):
        for sub in _substrings_of_run(run, min_len=2, max_len=_MAX_CJK_KEYWORD_LEN):
            if sub in _CJK_STOP_WORDS:
                continue
            counter[sub] += 1

    # 2. ASCII words (2+ chars)
    for m in _ASCII_WORD_RE.finditer(text):
        word = m.group(0).lower()
        if word in _EN_STOP_WORDS:
            continue
        counter[word] += 1

    # Filter by minimum frequency — respect the caller's parameter.
    candidates = [(token, count) for token, count in counter.items() if count >= min_freq]
    candidates.sort(key=lambda x: (-x[1], -len(x[0]), x[0]))

    # Deduplicate: skip shorter tokens that are fully contained in an already-selected longer token.
    result: list[str] = []
    for token, _count in candidates:
        skip = False
        for selected in result:
            if token in selected and len(selected) > len(token):
                skip = True
                break
        if skip:
            continue
        result.append(token)
        if len(result) >= top_k:
            break

    return result


# ---------------------------------------------------------------------------
# Shared utility
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

# All valid NodeRecord.kind values across the system.
# Structured nodes (document hierarchy, not searchable):
#   doc     - document root
#   chapter - chapter heading
#   section - section heading
#   article - legal article
#   item    - list item
#   block   - generic block
#   clause  - legal clause
#   table   - table node
#   figure  - figure node
# Searchable leaf node (participates in FTS5):
#   chunk   - text chunk from recursive character splitter
KNOWN_NODE_KINDS: frozenset[str] = frozenset(
    {
        "doc",
        "chapter",
        "section",
        "article",
        "item",
        "block",
        "clause",
        "table",
        "figure",
        "chunk",
    }
)
