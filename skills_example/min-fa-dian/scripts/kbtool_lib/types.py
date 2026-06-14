from __future__ import annotations

from dataclasses import dataclass
from typing import NotRequired, TypedDict


# ---------- Line-level match ----------
class LineMatch(TypedDict):
    line: int
    text: str


# ---------- Document-level query result ----------
class DocResult(TypedDict):
    file: str
    doc_id: str
    title: str
    score: float
    matches: list[LineMatch]


# ---------- Query Output Payloads ----------
class CompactPayload(TypedDict):
    tool: str
    cmd: str
    query: str
    status: str
    fts_match: str
    results: list[DocResult]
    next_action: str
    out_path: NotRequired[str]


class FullPayload(TypedDict):
    tool: str
    cmd: str
    query: str
    results: list[DocResult]
    out: str
    out_path: NotRequired[str]


# ---------- Skill Schema (from cli_parser.py --skill) ----------
class SkillArg(TypedDict, total=False):
    flag: str
    choices: list[str]
    type: str
    default: object
    required: bool
    note: str
    positional: bool
    repeatable: bool


class SkillCommand(TypedDict, total=False):
    name: str
    description: str
    args: list[SkillArg]


class SkillPayload(TypedDict):
    tool: str
    schema: str
    description: str
    global_args: list[SkillArg]
    commands: list[SkillCommand]
    workflow: list[str]
    trigger_conditions: dict[str, list[str]]
    presets: dict[str, str]
    error_recovery: dict[str, str]
