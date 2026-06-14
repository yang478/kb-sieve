"""
Runtime text utilities — re-exports from the shared tokenizer_core module.

tokenizer_core.py is the SINGLE source of truth for tokenization. It is
copied from the build tree during pack-builder generation. This module
re-exports everything so existing imports from kbtool_lib.text continue
to work unchanged.
"""

from __future__ import annotations

from . import tokenizer_core as _tokenizer_core

# Re-export tokenizer_core symbols so existing imports from kbtool_lib.text
# continue to work unchanged.
is_cjk = _tokenizer_core.is_cjk
tokenize_cjk_ngram = _tokenizer_core.tokenize_cjk_ngram
fts_tokens = _tokenizer_core.fts_tokens
build_match_expression = _tokenizer_core.build_match_expression
query_terms = _tokenizer_core.query_terms

extract_window = _tokenizer_core.extract_window
markdown_to_plain = _tokenizer_core.markdown_to_plain
parse_frontmatter = _tokenizer_core.parse_frontmatter
strip_frontmatter = _tokenizer_core.strip_frontmatter
stable_hash = _tokenizer_core.stable_hash
node_key = _tokenizer_core.node_key
derive_source_version = _tokenizer_core.derive_source_version
normalize_alias_text = _tokenizer_core.normalize_alias_text

extract_keywords = _tokenizer_core.extract_keywords
