#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import json
import math
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


def _load_record(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        return json.loads(value)
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError(f"Unsupported eval record type: {type(value).__name__}")


def _matches_any(node_id: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(node_id, pattern) for pattern in patterns)


def _rank_of_first_match(hits: list[str], patterns: list[str]) -> int:
    for index, node_id in enumerate(hits, start=1):
        if _matches_any(node_id, patterns):
            return index
    return 0


def _nearest_rank(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return float(ordered[index])


def evaluate_records(records: Iterable[object]) -> dict[str, Any]:
    loaded = [_load_record(record) for record in records]
    total = len(loaded)
    if total == 0:
        return {
            "cases": 0,
            "hit_at_1": 0.0,
            "hit_at_3": 0.0,
            "hit_at_5": 0.0,
            "mrr": 0.0,
            "latency_ms": {"p50": 0.0, "p95": 0.0, "max": 0.0},
            "bundle_chars": {"avg": 0.0, "max": 0},
            "failures": [],
        }

    hit_at_1 = 0
    hit_at_3 = 0
    hit_at_5 = 0
    reciprocal_sum = 0.0
    latencies: list[float] = []
    bundle_sizes: list[int] = []
    failures: list[dict[str, Any]] = []

    for record in loaded:
        result = record.get("result")
        if not isinstance(result, Mapping):
            result = {}
        hits = [str(hit) for hit in result.get("hits", []) if str(hit).strip()]
        patterns = [str(p) for p in record.get("expected_node_patterns", []) if str(p).strip()]
        rank = _rank_of_first_match(hits, patterns) if patterns else 0

        if rank == 1:
            hit_at_1 += 1
        if 0 < rank <= 3:
            hit_at_3 += 1
        if 0 < rank <= 5:
            hit_at_5 += 1
        if rank:
            reciprocal_sum += 1.0 / float(rank)
        else:
            failures.append(
                {
                    "query": str(record.get("query", "")),
                    "expected_node_patterns": patterns,
                    "hits": hits[:10],
                }
            )

        if "elapsed_ms" in result:
            latencies.append(float(result.get("elapsed_ms") or 0.0))
        if "bundle_chars" in result:
            bundle_sizes.append(int(result.get("bundle_chars") or 0))

    return {
        "cases": total,
        "hit_at_1": hit_at_1 / total,
        "hit_at_3": hit_at_3 / total,
        "hit_at_5": hit_at_5 / total,
        "mrr": reciprocal_sum / total,
        "latency_ms": {
            "p50": _nearest_rank(latencies, 0.50),
            "p95": _nearest_rank(latencies, 0.95),
            "max": max(latencies) if latencies else 0.0,
        },
        "bundle_chars": {
            "avg": (sum(bundle_sizes) / len(bundle_sizes)) if bundle_sizes else 0.0,
            "max": max(bundle_sizes) if bundle_sizes else 0,
        },
        "failures": failures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate deterministic kbtool retrieval JSONL results.")
    parser.add_argument("jsonl", help="JSONL file with query, expected_node_patterns, and result fields.")
    args = parser.parse_args(argv)

    path = Path(args.jsonl)
    records = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    sys.stdout.write(json.dumps(evaluate_records(records), ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
