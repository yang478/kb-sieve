from __future__ import annotations

import hashlib
import unicodedata


def normalize_canonical_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    if not value.endswith("\n"):
        value += "\n"
    return value


def canonical_text_from_markdown(text: str) -> str:
    return normalize_canonical_text(text)

def canonical_text_sha256(text: str) -> str:
    return hashlib.sha256(normalize_canonical_text(text).encode("utf-8")).hexdigest()
