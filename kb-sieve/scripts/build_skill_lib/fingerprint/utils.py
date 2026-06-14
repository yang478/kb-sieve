from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_text(text: str) -> str:
    """对文本内容做 SHA-256 摘要（UTF-8 编码）。"""
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    """对原始字节做 SHA-256 摘要。"""
    return hashlib.sha256(data).hexdigest()


def source_fingerprint_for_path(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def source_fingerprint(path: Path, fallback: str) -> str:
    try:
        return sha256_bytes(path.read_bytes())
    except OSError:
        return sha256_text(fallback)
