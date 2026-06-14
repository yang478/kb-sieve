from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChangeSet:
    changed_doc_ids: set[str] = field(default_factory=set)
    unchanged_doc_ids: set[str] = field(default_factory=set)
    metadata_only_doc_ids: set[str] = field(default_factory=set)
    rebuild_doc_ids: set[str] = field(default_factory=set)
    removed_doc_ids: set[str] = field(default_factory=set)
