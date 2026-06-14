"""
Build-time text utilities — thin proxy over the shared tokenizer_core module.

All tokenization functions delegate to tokenizer_core.py, which is the SINGLE
source of truth for both build-time (indexing) and runtime (query) behavior.
The same tokenizer_core.py is copied into generated skills during build.

See also: tokenizer_core.py for the actual implementations.
"""

from __future__ import annotations

from types import ModuleType

from .. import templates_dir
from .. import tokenizer_core as _tokenizer_core
from .registry import load_template_module

# Re-export tokenizer_core symbols for backwards compatibility.  Build-time
# differs only in `fts_tokens`, which returns a space-joined string for SQLite.
is_cjk = _tokenizer_core.is_cjk
tokenize_cjk_ngram = _tokenizer_core.tokenize_cjk_ngram
build_match_query = _tokenizer_core.build_match_query
build_match_all = _tokenizer_core.build_match_all
build_match_expression = _tokenizer_core.build_match_expression
query_terms = _tokenizer_core.query_terms
count_occurrences = _tokenizer_core.count_occurrences
extract_window = _tokenizer_core.extract_window
markdown_to_plain = _tokenizer_core.markdown_to_plain
parse_frontmatter = _tokenizer_core.parse_frontmatter
strip_frontmatter = _tokenizer_core.strip_frontmatter
stable_hash = _tokenizer_core.stable_hash
stable_hash_sha1 = _tokenizer_core.stable_hash_sha1
node_key = _tokenizer_core.node_key
derive_source_version = _tokenizer_core.derive_source_version
normalize_alias_text = _tokenizer_core.normalize_alias_text
build_punctuation_tolerant_regex = _tokenizer_core.build_punctuation_tolerant_regex
extract_keywords = _tokenizer_core.extract_keywords


def fts_tokens(text: str) -> str:
    """Build-time wrapper: returns space-joined FTS tokens (string for SQLite).

    Runtime kbtool uses ``tokenizer_core.fts_tokens`` directly (list form).
    Build-time callers (crud.py, cooccurrence.py) expect a string.
    """
    tokens = _tokenizer_core.fts_tokens(text)
    return " ".join(tokens)


_CANONICAL_TEXT_MODULE: ModuleType | None = None


def _load_canonical_text_module() -> ModuleType:
    global _CANONICAL_TEXT_MODULE
    if _CANONICAL_TEXT_MODULE is not None:
        return _CANONICAL_TEXT_MODULE
    module_path = templates_dir() / "kbtool_lib" / "canonical_text.py"
    _CANONICAL_TEXT_MODULE = load_template_module("pack_builder_kbtool_canonical_text", module_path)
    return _CANONICAL_TEXT_MODULE


def normalize_canonical_text(text: str) -> str:
    return _load_canonical_text_module().normalize_canonical_text(text)


def canonical_text_from_markdown(text: str) -> str:
    return _load_canonical_text_module().canonical_text_from_markdown(text)


def canonical_text_sha256(text: str) -> str:
    return _load_canonical_text_module().canonical_text_sha256(text)
