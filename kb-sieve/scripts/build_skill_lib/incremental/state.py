from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import templates_dir
from ..fingerprint.utils import sha256_text, source_fingerprint
from ..types import InputDoc
from ..utils.contract import (
    empty_build_state,
    export_sha_by_doc,
    index_binding_payload,
)
from ..utils.registry import canonical_model_registry_json

logger = logging.getLogger(__name__)

ARTIFACT_VERSION = "kbtool.artifact.v3"
BUILD_STATE_FILENAME = "build_state.json"
DEFAULT_MODEL_REGISTRY_SHA256 = sha256_text(canonical_model_registry_json())


def write_build_state(path: Path, state: Mapping[str, Any]) -> None:
    """Atomically write build_state: write to temp file, then replace."""
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    tmp_path.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    from ..utils.contract import read_json
    return read_json(path)


def _export_sha_by_doc(root: Path) -> dict[tuple[str, str], str]:
    payload = _read_json(root / "phase_a_artifact.json")
    return export_sha_by_doc(payload)


def build_state_from_artifact(
    *,
    root: Path,
    docs: Sequence[InputDoc],
    canonical_texts: Mapping[tuple[str, str], str],
) -> dict[str, Any]:
    """Build build_state.json from artifact data (whole-document mode).

    Only document-level fingerprints are retained for incremental change detection.
    """
    state = empty_build_state()
    manifest_path = root / "corpus_manifest.json"
    export_sha_by_doc_map = _export_sha_by_doc(root)

    state["artifact_version"] = ARTIFACT_VERSION
    state["created_at"] = datetime.now(timezone.utc).isoformat()
    state["corpus_manifest_sha256"] = (
        sha256_text(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else ""
    )
    state["model_registry_sha256"] = DEFAULT_MODEL_REGISTRY_SHA256

    active_docs = [doc for doc in docs if doc.is_active]

    # documents_state: doc_id -> source_version -> state
    documents_state: dict[str, dict[str, dict[str, Any]]] = {}
    for doc in sorted(active_docs, key=lambda item: (item.doc_id, item.source_version)):
        key = (doc.doc_id, doc.source_version)
        canonical_text = str(canonical_texts.get(key) or "")
        doc_versions = documents_state.setdefault(doc.doc_id, {})
        doc_versions[doc.source_version] = {
            "source_path": str(doc.path),
            "source_fingerprint": source_fingerprint(doc.path, doc.doc_hash),
            "extracted_text_fingerprint": sha256_text(canonical_text),
            "active_parser": str(getattr(doc, "active_parser", "") or "") or "whole_doc",
            "export_sha256": export_sha_by_doc_map.get(key, ""),
            "doc_title": doc.title,
            "doc_hash": doc.doc_hash,
        }
    state["documents"] = documents_state

    # Simplified indexes binding — doc-level only
    active_doc_rows = [
        {
            "doc_id": doc.doc_id,
            "source_version": doc.source_version,
            "doc_hash": doc.doc_hash,
        }
        for doc in active_docs
    ]
    state["indexes"] = {
        "sqlite": index_binding_payload("sqlite", active_doc_rows),
        "fts": index_binding_payload("fts", active_doc_rows),
    }

    # Toolchain checksum
    state["build_toolchain_checksum"] = compute_toolchain_checksum(root)

    return state


def compute_toolchain_checksum(root: Path) -> str:
    """Compute SHA256 of key build configuration files and source code."""
    h = hashlib.sha256()

    # Include build source code
    builder_hash = hashlib.sha256()
    try:
        import build_skill_lib

        builder_dir = Path(build_skill_lib.__file__).resolve().parent
        for pyfile in sorted(builder_dir.rglob("*.py")):
            if pyfile.name.startswith("test_"):
                continue
            rel = pyfile.relative_to(builder_dir)
            if rel.parts[0] == "utils" and pyfile.name in {
                "signals.py", "safe_subprocess.py", "safe_sqlite.py",
            }:
                continue
            with pyfile.open("rb") as f:
                while chunk := f.read(65536):
                    builder_hash.update(chunk)
    except Exception:
        logger.warning("compute_toolchain_checksum: failed to hash build_skill_lib source", exc_info=True)
    h.update(builder_hash.digest())

    # Include runtime template code
    tmpl_hash = hashlib.sha256()
    try:
        tmpl_dir = templates_dir() / "kbtool_lib"
        if tmpl_dir.exists():
            for pyfile in sorted(tmpl_dir.rglob("*.py")):
                with pyfile.open("rb") as f:
                    while chunk := f.read(65536):
                        tmpl_hash.update(chunk)
    except Exception:
        logger.warning("compute_toolchain_checksum: failed to hash template source", exc_info=True)
    h.update(tmpl_hash.digest())

    return h.hexdigest()
