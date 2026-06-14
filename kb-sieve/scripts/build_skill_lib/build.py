from __future__ import annotations

import json
import logging
import re
import shutil
import sys
import tempfile
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import (
    incremental_update_kb_sqlite_db,
    merge_history,
    read_existing_docs,
    write_kb_sqlite_db,
)
from .doc import generate_doc, generate_doc_from_ir
from .extract import extract_to_markdown
from .fingerprint.utils import sha256_text, source_fingerprint_for_path
from .incremental import (
    ARTIFACT_VERSION,
    BUILD_STATE_FILENAME,
    ChangeSet,
    build_state_from_artifact,
    compute_toolchain_checksum,
    write_build_state,
)
from .index import build_keywords_from_title, write_sharded_index
from .ir import read_ir_jsonl, write_phase_a_artifact_export
from .kbtool_assets import (
    maybe_package_kbtool_pyinstaller,
    write_kbtool_script,
    write_kbtool_sha1,
    write_root_kbtool_entrypoints,
)
from .render import render_generated_skill_md
from .types import HeadingRow, InputDoc
from .utils.fs import BuildError, derive_doc_id, derive_doc_title, die, write_text
from .utils.text import (
    canonical_text_from_markdown,
    canonical_text_sha256,
    derive_source_version,
    markdown_to_plain,
    normalize_canonical_text,
    stable_hash,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Manifest & canonical text helpers
# ---------------------------------------------------------------------------


def _write_manifest(out_skill_dir: Path, *, skill_name: str, title: str, docs: Sequence[InputDoc]) -> None:
    payload = {
        "skill_name": skill_name,
        "title": title,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "docs": [
            {
                "doc_id": d.doc_id,
                "title": d.title,
                "source_file": d.path.name,
                "source_path": str(d.path),
                "doc_hash": d.doc_hash,
                "source_version": d.source_version,
                "active_version": bool(d.is_active),
            }
            for d in docs
        ],
    }
    write_text(out_skill_dir / "manifest.json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _safe_canonical_version(source_version: str) -> str:
    value = re.sub(r"[^0-9A-Za-z._-]+", "-", str(source_version or "current")).strip("-")
    return value or "current"


def _canonical_text_rel_path(doc_id: str, source_version: str) -> str:
    return f"canonical_text/{doc_id}--{_safe_canonical_version(source_version)}.txt"


def _load_existing_corpus_manifest(skill_root: Path) -> tuple[str, dict[tuple[str, str], dict[str, str]]]:
    manifest_path = skill_root / "corpus_manifest.json"
    if not manifest_path.exists():
        return "", {}
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return "", {}

    title = str(data.get("title") or "")
    docs = data.get("docs")
    if not isinstance(docs, list):
        return title, {}

    out: dict[tuple[str, str], dict[str, str]] = {}
    for row in docs:
        if not isinstance(row, dict):
            continue
        doc_id = str(row.get("doc_id") or "").strip()
        source_version = str(row.get("source_version") or "current").strip() or "current"
        if not doc_id:
            continue
        out[(doc_id, source_version)] = {
            "title": str(row.get("title") or ""),
            "source_file": str(row.get("source_file") or ""),
            "source_path": str(row.get("source_path") or ""),
            "doc_hash": str(row.get("doc_hash") or ""),
            "canonical_text_path": str(row.get("canonical_text_path") or ""),
        }
    return title, out


def _load_existing_canonical_text(
    skill_root: Path,
    *,
    doc_id: str,
    source_version: str,
    existing_doc: dict[str, str] | None = None,
) -> str | None:
    rel_candidates = []
    if existing_doc is not None:
        existing_rel_path = str(existing_doc.get("canonical_text_path") or "").strip()
        if existing_rel_path:
            rel_candidates.append(existing_rel_path)
    rel_candidates.append(_canonical_text_rel_path(doc_id, source_version))

    for rel_path in dict.fromkeys(rel_candidates):
        if not rel_path:
            continue
        path = skill_root / rel_path
        if path.exists():
            return normalize_canonical_text(path.read_text(encoding="utf-8"))
    return None


def _write_corpus_manifest(
    out_skill_dir: Path,
    *,
    title: str,
    docs: Sequence[InputDoc],
    canonical_texts: dict[tuple[str, str], str],
    existing_root: Path | None = None,
    existing_docs: dict[tuple[str, str], dict[str, str]] | None = None,
) -> None:
    payload_docs = []
    for doc in sorted(docs, key=lambda item: (item.doc_id, item.source_version, 0 if item.is_active else 1)):
        key = (doc.doc_id, doc.source_version)
        canonical_text = canonical_texts.get(key)
        existing_doc = (existing_docs or {}).get(key, {})
        if canonical_text is None and existing_root is not None:
            canonical_text = _load_existing_canonical_text(
                existing_root,
                doc_id=doc.doc_id,
                source_version=doc.source_version,
                existing_doc=existing_doc,
            )
        if canonical_text is None:
            canonical_text = normalize_canonical_text("")

        rel_path = _canonical_text_rel_path(doc.doc_id, doc.source_version)
        write_text(out_skill_dir / rel_path, canonical_text)
        payload_docs.append(
            {
                "doc_id": doc.doc_id,
                "title": doc.title or str(existing_doc.get("title") or doc.doc_id),
                "source_file": doc.path.name if doc.path.name else str(existing_doc.get("source_file") or "(unknown)"),
                "source_path": str(doc.path) if str(doc.path) else str(existing_doc.get("source_path") or ""),
                "doc_hash": doc.doc_hash or str(existing_doc.get("doc_hash") or ""),
                "source_version": doc.source_version,
                "active_version": bool(doc.is_active),
                "canonical_text_path": rel_path,
                "canonical_text_sha256": canonical_text_sha256(canonical_text),
            }
        )

    payload = {
        "title": title,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "docs": payload_docs,
    }
    write_text(out_skill_dir / "corpus_manifest.json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


# ---------------------------------------------------------------------------
# Incremental build helpers
# ---------------------------------------------------------------------------


def _load_build_state(target: Path) -> dict[str, Any]:
    """Read previous build state file, return empty dict on failure."""
    path = target / BUILD_STATE_FILENAME
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _find_previous_state_for_path(
    previous_docs: dict[str, Any], resolved_path: Path
) -> tuple[str | None, str, dict[str, Any] | None]:
    """Find document state for a given path in previous_state."""
    if not isinstance(previous_docs, dict):
        return None, "current", None

    for doc_id, versions in previous_docs.items():
        if not isinstance(versions, dict):
            continue
        if "source_fingerprint" not in versions:
            for sv, state in versions.items():
                if isinstance(state, dict) and state.get("source_path") == str(resolved_path):
                    return doc_id, sv, state
        else:
            if versions.get("source_path") == str(resolved_path):
                return doc_id, "current", versions

    return None, "current", None


def _prepare_incremental_inputs(
    inputs: Sequence[Path],
    previous_state: dict[str, Any],
    pdf_fallback: str,
) -> tuple[ChangeSet, list[InputDoc]]:
    """Prepare InputDoc list and compute change set for incremental builds."""
    previous_docs = previous_state.get("documents", {})
    if not isinstance(previous_docs, dict):
        previous_docs = {}

    changed_doc_ids: set[str] = set()
    unchanged_doc_ids: set[str] = set()
    metadata_only_doc_ids: set[str] = set()
    rebuild_doc_ids: set[str] = set()
    current_doc_ids: set[str] = set()
    docs: list[InputDoc] = []
    used_doc_ids: set[str] = set()

    for p in inputs:
        resolved = p.resolve()
        src_fp = source_fingerprint_for_path(resolved)

        prev_doc_id, prev_sv, prev_state = _find_previous_state_for_path(previous_docs, resolved)

        if prev_state is None or prev_doc_id is None:
            md = extract_to_markdown(resolved, pdf_fallback=pdf_fallback)
            title = derive_doc_title(resolved, md)
            doc_id = derive_doc_id(resolved, used_doc_ids)
            sv = derive_source_version(resolved.stem, title)
            doc_hash = stable_hash(md)
            docs.append(
                InputDoc(
                    path=resolved,
                    doc_id=doc_id,
                    title=title,
                    source_version=sv,
                    doc_hash=doc_hash,
                    active_parser="whole_doc",
                )
            )
            changed_doc_ids.add(doc_id)
            rebuild_doc_ids.add(doc_id)
            current_doc_ids.add(doc_id)
            continue

        doc_id = prev_doc_id
        current_doc_ids.add(doc_id)
        used_doc_ids.add(doc_id)

        if prev_state.get("source_fingerprint") == src_fp:
            unchanged_doc_ids.add(doc_id)
            docs.append(
                InputDoc(
                    path=resolved,
                    doc_id=doc_id,
                    title=prev_state.get("doc_title", derive_doc_title(resolved, "")),
                    source_version=prev_sv,
                    doc_hash=prev_state.get("doc_hash", ""),
                    active_parser=prev_state.get("active_parser", "whole_doc"),
                )
            )
            continue

        md = extract_to_markdown(resolved, pdf_fallback=pdf_fallback)
        text_fp = sha256_text(canonical_text_from_markdown(md))
        prev_text_fp = str(prev_state.get("extracted_text_fingerprint") or "")

        changed_doc_ids.add(doc_id)
        if text_fp == prev_text_fp:
            metadata_only_doc_ids.add(doc_id)
        else:
            rebuild_doc_ids.add(doc_id)

        title = derive_doc_title(resolved, md)
        sv = derive_source_version(resolved.stem, title)
        doc_hash = stable_hash(md)
        docs.append(
            InputDoc(
                path=resolved,
                doc_id=doc_id,
                title=title,
                source_version=sv,
                doc_hash=doc_hash,
                active_parser="whole_doc",
            )
        )

    removed_doc_ids = set(previous_docs.keys()) - current_doc_ids
    rebuild_doc_ids |= removed_doc_ids
    changed_doc_ids |= removed_doc_ids

    change_set = ChangeSet(
        changed_doc_ids=changed_doc_ids,
        unchanged_doc_ids=unchanged_doc_ids,
        metadata_only_doc_ids=metadata_only_doc_ids,
        rebuild_doc_ids=rebuild_doc_ids,
        removed_doc_ids=removed_doc_ids,
    )
    return change_set, docs


# ---------------------------------------------------------------------------
# Phase functions
# ---------------------------------------------------------------------------


def _process_single_full_input(
    p: Path,
    pdf_fallback: str,
    tmp: Path,
    doc_id: str,
) -> tuple[InputDoc, list[HeadingRow], str]:
    """Extract one document and generate whole-doc node — thread-safe.

    Returns (doc, heading_rows, canonical_md).
    """
    resolved = p.resolve()
    md = extract_to_markdown(resolved, pdf_fallback=pdf_fallback)
    canonical_md = canonical_text_from_markdown(md)
    title = derive_doc_title(resolved, md)
    sv = derive_source_version(resolved.stem, title)
    doc = InputDoc(
        path=resolved,
        doc_id=doc_id,
        title=title,
        source_version=sv,
        doc_hash=stable_hash(md),
        active_parser="whole_doc",
    )
    heading_rows = generate_doc(doc, canonical_md, tmp)
    return doc, heading_rows, canonical_md


def _extract_documents(
    inputs: Sequence[Path],
    pdf_fallback: str,
    ir_jsonl: Path | None,
    tmp: Path,
    *,
    change_set: ChangeSet | None = None,
    target: Path | None = None,
    workers: int | None = None,
) -> tuple[list[InputDoc], list[HeadingRow], dict[tuple[str, str], str]]:
    """Phase 1: Extract documents from inputs or IR JSONL.

    Returns (docs, heading_rows, canonical_texts).
    """
    docs: list[InputDoc] = []
    all_heading_rows: list[HeadingRow] = []
    canonical_texts: dict[tuple[str, str], str] = {}

    # -- IR JSONL path (always full) --
    if ir_jsonl is not None:
        ir_docs, ir_nodes = read_ir_jsonl(ir_jsonl)
        for doc in ir_docs:
            key = (doc.doc_id, doc.source_version)
            # Concatenate all node bodies as canonical text
            bodies = [n.body_md or n.body_plain or "" for n in ir_nodes if n.doc_id == doc.doc_id and n.is_active]
            canonical_texts[key] = canonical_text_from_markdown("\n\n".join(bodies))
            heading_rows = generate_doc_from_ir(doc, ir_nodes, tmp)
            all_heading_rows.extend(heading_rows)
        docs = ir_docs
        return docs, all_heading_rows, canonical_texts

    # -- Full build path --
    if change_set is None:
        used_doc_ids: set[str] = set()
        input_doc_ids: list[tuple[Path, str]] = []
        for p in inputs:
            doc_id = derive_doc_id(p.resolve(), used_doc_ids)
            input_doc_ids.append((p, doc_id))

        max_workers = workers if workers else min(8, max(1, len(inputs)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_path: dict = {}
            for p, doc_id in input_doc_ids:
                future = executor.submit(
                    _process_single_full_input,
                    p,
                    pdf_fallback,
                    tmp,
                    doc_id,
                )
                future_to_path[future] = p

            failed_paths: list[Path] = []
            for future in as_completed(future_to_path):
                src_path = future_to_path[future]
                try:
                    doc, heading_rows, ct = future.result()
                except Exception as exc:
                    failed_paths.append(src_path)
                    logger.error("Document extraction failed: %s — %s", src_path, exc)
                    continue
                docs.append(doc)
                all_heading_rows.extend(heading_rows)
                canonical_texts[(doc.doc_id, doc.source_version)] = ct

            if failed_paths:
                raise BuildError(
                    f"{len(failed_paths)} document(s) failed during parallel extraction: "
                    + ", ".join(str(p) for p in failed_paths)
                )

        input_order = {p.resolve(): i for i, (p, _) in enumerate(input_doc_ids)}
        docs.sort(key=lambda d: input_order.get(d.path, 0))

        return docs, all_heading_rows, canonical_texts

    # -- Incremental path --
    assert target is not None, "target is required for incremental builds"
    previous_state = _load_build_state(target)
    previous_docs = previous_state.get("documents", {})
    if not isinstance(previous_docs, dict):
        previous_docs = {}

    reuse_doc_ids = change_set.unchanged_doc_ids | change_set.metadata_only_doc_ids

    used_doc_ids: set[str] = set()
    for p in inputs:
        resolved = p.resolve()
        prev_doc_id, prev_sv, prev_state = _find_previous_state_for_path(previous_docs, resolved)

        if prev_doc_id is None:
            md = extract_to_markdown(resolved, pdf_fallback=pdf_fallback)
            canonical_md = canonical_text_from_markdown(md)
            title = derive_doc_title(resolved, md)
            doc_id = derive_doc_id(resolved, used_doc_ids)
            sv = derive_source_version(resolved.stem, title)
            doc_hash = stable_hash(md)
            doc = InputDoc(
                path=resolved,
                doc_id=doc_id,
                title=title,
                source_version=sv,
                doc_hash=doc_hash,
                active_parser="whole_doc",
            )
            docs.append(doc)
            heading_rows = generate_doc(doc, canonical_md, tmp)
            all_heading_rows.extend(heading_rows)
            canonical_texts[(doc_id, sv)] = canonical_md
            continue

        doc_id = prev_doc_id
        used_doc_ids.add(doc_id)

        if doc_id in reuse_doc_ids:
            doc = InputDoc(
                path=resolved,
                doc_id=doc_id,
                title=prev_state.get("doc_title", derive_doc_title(resolved, "")),
                source_version=prev_sv,
                doc_hash=prev_state.get("doc_hash", ""),
                active_parser=prev_state.get("active_parser", "whole_doc"),
            )
            docs.append(doc)

            # Copy references directory from existing target
            src_ref = target / "references" / doc_id
            dst_ref = tmp / "references" / doc_id
            if src_ref.exists():
                shutil.copytree(src_ref, dst_ref, dirs_exist_ok=True)

            # Load canonical text
            ct = _load_existing_canonical_text(target, doc_id=doc_id, source_version=prev_sv)
            if ct is None:
                ct = normalize_canonical_text("")
            canonical_texts[(doc_id, prev_sv)] = ct

            # Reconstruct heading rows from existing doc
            all_heading_rows.append((doc.title, doc_id, doc.title, "doc", f"{doc_id}:doc", f"references/{doc_id}/doc.md"))
        else:
            md = extract_to_markdown(resolved, pdf_fallback=pdf_fallback)
            canonical_md = canonical_text_from_markdown(md)
            title = derive_doc_title(resolved, md)
            sv = derive_source_version(resolved.stem, title)
            doc_hash = stable_hash(md)
            doc = InputDoc(
                path=resolved,
                doc_id=doc_id,
                title=title,
                source_version=sv,
                doc_hash=doc_hash,
                active_parser="whole_doc",
            )
            docs.append(doc)
            heading_rows = generate_doc(doc, canonical_md, tmp)
            all_heading_rows.extend(heading_rows)
            canonical_texts[(doc_id, sv)] = canonical_md

    return docs, all_heading_rows, canonical_texts


def _write_indexes_and_assets(
    tmp: Path,
    skill_name: str,
    title: str,
    docs: Sequence[InputDoc],
    all_heading_rows: list[HeadingRow],
    package_kbtool: bool,
) -> str:
    """Phase 2: Write manifest, indexes, SKILL.md, scripts."""
    _write_manifest(tmp, skill_name=skill_name, title=title, docs=docs)

    # Global indexes
    all_heading_rows.sort(key=lambda r: r[0])
    headings_rows = [
        (t, doc_id, doc_title, kind, item_id, path) for (t, doc_id, doc_title, kind, item_id, path) in all_heading_rows
    ]
    write_sharded_index(tmp, "headings", headings_rows, header=("title", "doc_id", "doc_title", "type", "id", "path"))

    kw_map: dict[tuple[str, str, str, str, str], tuple[str, ...]] = {}
    for t, doc_id, doc_title, kind, item_id, path in all_heading_rows:
        for kw in build_keywords_from_title(t):
            kw_map[(kw, doc_id, kind, item_id, path)] = (kw, doc_id, doc_title, kind, item_id, path)
    kw_rows = sorted(kw_map.values(), key=lambda r: r[0])
    write_sharded_index(tmp, "kw", kw_rows, header=("keyword", "doc_id", "doc_title", "type", "id", "path"))

    # Generated skill SKILL.md
    write_text(
        tmp / "SKILL.md",
        render_generated_skill_md(skill_name, title, docs),
    )
    write_kbtool_script(tmp)
    kbtool_sha = write_kbtool_sha1(tmp)
    write_root_kbtool_entrypoints(tmp)
    if package_kbtool:
        exe = maybe_package_kbtool_pyinstaller(tmp)
        if exe is not None:
            write_text(exe.parent / "kbtool.sha1", kbtool_sha + "\n")

    return kbtool_sha


def _write_database(
    tmp: Path,
    merged_docs,
    doc_texts: dict[tuple[str, str], tuple[str, str]],
):
    """Phase 3: Write SQLite database with whole-document FTS."""
    db_path = tmp / "kb.sqlite"
    write_kb_sqlite_db(db_path, merged_docs, doc_texts, base_dir=tmp)
    return db_path


def _write_database_incremental(
    tmp: Path,
    target: Path,
    change_set: ChangeSet,
    merged_docs,
    doc_texts: dict[tuple[str, str], tuple[str, str]],
):
    """Phase 3 (incremental): Copy existing DB and apply incremental updates."""
    db_path = tmp / "kb.sqlite"
    target_db = target / "kb.sqlite"

    if not target_db.exists():
        return _write_database(tmp, merged_docs, doc_texts)

    shutil.copy2(str(target_db), str(db_path))
    for suffix in ("-wal", "-shm"):
        src_wal = target_db.parent / (target_db.name + suffix)
        if src_wal.exists():
            dst_wal = db_path.parent / (db_path.name + suffix)
            shutil.copy2(str(src_wal), str(dst_wal))

    incremental_update_kb_sqlite_db(
        db_path,
        change_set,
        merged_docs,
        doc_texts,
        base_dir=tmp,
    )

    import sqlite3
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("INSERT INTO doc_fts(doc_fts) VALUES('optimize')")
    finally:
        conn.close()
    return db_path


def _write_final_artifacts(
    tmp: Path,
    target: Path,
    force: bool,
    title: str,
    merged_docs,
    canonical_texts: dict[tuple[str, str], str],
) -> None:
    """Phase 4: Write corpus manifest, phase A export, and build state."""
    should_merge = target.exists() and force
    existing_title, existing_corpus_docs = _load_existing_corpus_manifest(target) if should_merge else ("", {})
    _write_corpus_manifest(
        tmp,
        title=title or existing_title,
        docs=merged_docs,
        canonical_texts=canonical_texts,
        existing_root=target if should_merge else None,
        existing_docs=existing_corpus_docs,
    )
    write_phase_a_artifact_export(
        tmp,
        docs=merged_docs,
    )
    write_build_state(
        tmp / BUILD_STATE_FILENAME,
        build_state_from_artifact(
            root=tmp,
            docs=merged_docs,
            canonical_texts=canonical_texts,
        ),
    )


def _atomic_replace(tmp: Path, target: Path) -> Path:
    """Phase 5: Atomic-ish directory swap with backup & restore."""
    from .utils.signals import raise_if_shutdown

    raise_if_shutdown()

    db_path_wal = tmp / "kb.sqlite"
    if db_path_wal.exists():
        with suppress(Exception):
            import sqlite3
            conn = sqlite3.connect(str(db_path_wal))
            try:
                for _ in range(3):
                    try:
                        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                        break
                    except sqlite3.OperationalError:
                        continue
            finally:
                with suppress(Exception):
                    conn.close()

    backup = target.parent / (target.name + ".old")
    if target.exists():
        if backup.exists():
            shutil.rmtree(backup)
        target.rename(backup)

    try:
        shutil.move(str(tmp), str(target))
    except OSError:
        if backup.exists():
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            shutil.move(str(backup), str(target))
        raise

    if backup.exists():
        shutil.rmtree(backup)
    return target


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _build_summary(skill_dir):
    import sqlite3

    db_path = str(skill_dir / "kb.sqlite")
    conn = sqlite3.connect(db_path)
    try:
        docs = conn.execute("SELECT COUNT(*) FROM docs WHERE is_active=1").fetchone()[0]
        tokens = conn.execute("SELECT COUNT(*) FROM doc_fts_data").fetchone()[0]
        return f"{docs} doc(s), {tokens} FTS token(s)"
    finally:
        conn.close()


def _build_doc_texts(
    docs: Sequence[InputDoc],
    canonical_texts: dict[tuple[str, str], str],
) -> dict[tuple[str, str], tuple[str, str]]:
    """Build the doc_texts mapping for database insertion.

    Returns: {(doc_id, source_version): (title, body_plain)}
    """
    result: dict[tuple[str, str], tuple[str, str]] = {}
    for doc in docs:
        key = (doc.doc_id, doc.source_version)
        canonical = canonical_texts.get(key, "")
        body_plain = markdown_to_plain(canonical)
        result[key] = (doc.title, body_plain)
    return result


def build_skill(
    skill_name: str,
    title: str,
    inputs: Sequence[Path],
    out_dir: Path,
    force: bool,
    *,
    pdf_fallback: str = "none",
    ir_jsonl: Path | None = None,
    package_kbtool: bool = False,
    incremental: bool = False,
    workers: int | None = None,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / skill_name

    from .utils.signals import graceful_shutdown_context

    if target.exists() and not force and not incremental:
        die(f"Output already exists: {target} (use --force to overwrite or --incremental to update)")

    with graceful_shutdown_context():  # noqa: SIM117
        with tempfile.TemporaryDirectory(prefix=f".{skill_name}.tmp-", dir=out_dir) as tmp_name:
            tmp = Path(tmp_name)

            # Preserve user-managed files when rebuilding
            if target.exists():
                for keep in ("bin", "hooks"):
                    src = target / keep
                    if not src.exists():
                        continue
                    shutil.copytree(src, tmp / keep, dirs_exist_ok=True)

            # Determine build mode
            previous_state = _load_build_state(target)
            is_incremental = (
                incremental
                and target.exists()
                and previous_state.get("artifact_version") == ARTIFACT_VERSION
                and (target / "kb.sqlite").exists()
            )

            change_set: ChangeSet | None = None

            if is_incremental:
                previous_checksum = str(previous_state.get("build_toolchain_checksum") or "")
                current_checksum = compute_toolchain_checksum(tmp)
                if previous_checksum != current_checksum:
                    print("[incremental] Toolchain changed, falling back to full rebuild.")
                    is_incremental = False
                elif not inputs and ir_jsonl is None:
                    print("[incremental] No inputs, falling back to full rebuild.")
                    is_incremental = False
                else:
                    change_set, _ = _prepare_incremental_inputs(inputs, previous_state, pdf_fallback)
                    if not change_set.changed_doc_ids and not change_set.removed_doc_ids:
                        print("[incremental] No changes detected. Skipping rebuild.")
                        return target
                    print(
                        f"[incremental] {len(change_set.rebuild_doc_ids)} rebuild, "
                        f"{len(change_set.metadata_only_doc_ids)} metadata-only, "
                        f"{len(change_set.unchanged_doc_ids)} unchanged, "
                        f"{len(change_set.removed_doc_ids)} removed"
                    )

            # Phase 1: Extract documents
            logger.info("[1/5] Extracting documents from %d inputs...", len(inputs))
            docs, all_heading_rows, canonical_texts = _extract_documents(
                inputs,
                pdf_fallback,
                ir_jsonl,
                tmp,
                change_set=change_set if is_incremental else None,
                target=target if is_incremental else None,
                workers=workers,
            )
            logger.info("[1/5] Done: %d docs", len(docs))

            # Phase 2: Write indexes and assets
            logger.info("[2/5] Writing indexes and assets for %s...", skill_name)
            _write_indexes_and_assets(tmp, skill_name, title, docs, all_heading_rows, package_kbtool)
            logger.info("[2/5] Done: indexes and assets written")

            # Phase 3: Write database
            logger.info("[3/5] Writing database...")
            merged_docs = merge_history(
                read_existing_docs(target / "kb.sqlite") if (target.exists() and (force or is_incremental)) else [],
                docs,
                key_fn=lambda record: (record.doc_id, record.source_version),
                sort_key=lambda record: (record.doc_id, record.source_version, 0 if record.is_active else 1),
            )
            doc_texts = _build_doc_texts(merged_docs, canonical_texts)

            if is_incremental and change_set is not None:
                _write_database_incremental(tmp, target, change_set, merged_docs, doc_texts)
            else:
                _write_database(tmp, merged_docs, doc_texts)
            logger.info("[3/5] Done: database written")

            # Phase 4: Write final artifacts
            logger.info("[4/5] Writing final artifacts...")
            _write_final_artifacts(
                tmp,
                target,
                force or is_incremental,
                title,
                merged_docs,
                canonical_texts,
            )
            logger.info("[4/5] Done: final artifacts written")

            # Phase 5: Atomic replace
            logger.info("[5/5] Atomic replace %s -> %s...", tmp, target)
            result = _atomic_replace(tmp, target)
            summary = _build_summary(out_dir / skill_name)
            logger.info("Build summary: %s", summary)
            print(f"Build complete: {summary}", file=sys.stderr)
            return result
