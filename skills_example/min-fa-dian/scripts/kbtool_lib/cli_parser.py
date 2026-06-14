from __future__ import annotations

import argparse

from .cli_commands import cmd_query, cmd_read
from .types import SkillArg, SkillCommand, SkillPayload

_PUBLIC_SKILL_ARGS = {
    "query": {"--query", "--preset", "--out"},
    "read": {"--doc-id", "--start", "--count", "--jump", "--tokens", "--out", "--around", "--sections", "--find", "--expand", "--after", "--context"},
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="KB tool for generated skills (whole-document FTS5).")
    p.add_argument("--root", default="", help="Skill root directory (default: auto-detect).")
    p.add_argument("--db", default="kb.sqlite", help="SQLite DB path relative to root (default: kb.sqlite).")
    p.add_argument("--check-db", action="store_true", help="Run SQLite quick_check on open (slower; off by default).")
    p.add_argument("--skill", action="store_true", help="Print JSON tool usage for LLMs and exit.")
    sub = p.add_subparsers(dest="cmd", metavar="{query,read}")
    sub.required = True

    q = sub.add_parser("query", help="Search the knowledge base and return document locations with line numbers.")
    q.add_argument("--query", required=True, help="User query.")
    q.add_argument(
        "--preset",
        choices=["quick", "standard"],
        default="quick",
        help="Output budget preset (default: quick).",
    )
    q.add_argument("--out", default="runs/query.md", help="Output markdown path (relative to root).")
    q.add_argument(
        "--stdout",
        choices=["auto", "compact", "full"],
        default="auto",
        help=(
            "Stdout payload mode: auto=compact when --out is set, full otherwise; "
            "compact=agent summary; full=include match details."
        ),
    )
    q.add_argument("--limit", type=int, default=10, help="Max documents to return.")
    q.add_argument("--doc-ids", default="", dest="doc_ids", help="Comma-separated list of doc_ids to restrict search to.")
    q.set_defaults(func=cmd_query)

    # --- read subcommand ---
    r = sub.add_parser("read", help="Read original document text with smart navigation.")
    r.add_argument("--doc-id", required=True, dest="doc_id", help="Document ID(s), comma-separated for multi-doc.")
    r.add_argument("--start", type=int, default=1, help="Start line number (1-based, default: 1).")
    r.add_argument("--count", type=int, default=20, help="Number of lines to read (default: 20).")
    r.add_argument("--around", type=int, default=0, help="Auto-detect section around this line number.")
    r.add_argument("--expand", type=int, default=0, help="Expand --around by N surrounding sections.")
    r.add_argument("--sections", action="store_true", help="List all sections with headings and line numbers.")
    r.add_argument("--find", default="", help="Search keyword after --after line, return context.")
    r.add_argument("--after", type=int, default=0, help="Start search from this line (used with --find).")
    r.add_argument("--context", type=int, default=10, help="Context lines around --find hit (default: 10).")
    r.add_argument("--jump", default="", help="Jump-read ranges, e.g. '210-230,450-460' (overrides --start/--count).")
    r.add_argument("--tokens", default="", help="Query tokens for hit marking (from previous query).")
    r.add_argument("--out", default="", help="Output markdown path (relative to root).")
    r.set_defaults(func=cmd_read)

    return p


def _build_skill_payload(parser: argparse.ArgumentParser) -> SkillPayload:
    def _arg(action: argparse.Action) -> SkillArg | None:
        if isinstance(action, (argparse._HelpAction, argparse._SubParsersAction)) or action.help == argparse.SUPPRESS:
            return None
        arg: SkillArg = {"flag": action.dest if not action.option_strings else action.option_strings[0]}
        if action.choices:
            arg["choices"] = list(action.choices)
        arg["type"] = (
            "enum"
            if action.choices
            else "bool"
            if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction))
            else "list"
            if isinstance(action, argparse._AppendAction) or action.nargs in ("+", "*")
            else "int"
            if action.type is int
            else "string"
        )
        if action.default is not None:
            arg["default"] = action.default
        if action.required:
            arg["required"] = True
        if action.help:
            arg["note"] = action.help
        if not action.option_strings:
            arg["positional"] = True
            if action.nargs in ("*", "+"):
                arg["repeatable"] = True
        return arg

    global_args = [a for a in [_arg(a) for a in parser._actions] if a is not None]
    commands: list[SkillCommand] = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            help_map = {ca.dest: ca.help for ca in action._choices_actions}
            for name, sub in action._name_parser_map.items():
                cmd: SkillCommand = {"name": name, "description": str(help_map.get(name, ""))}
                public = _PUBLIC_SKILL_ARGS.get(name, set())
                args = [
                    a
                    for a in [_arg(a) for a in sub._actions]
                    if a is not None and str(a.get("flag", "")) in public
                ]
                if args:
                    cmd["args"] = args
                commands.append(cmd)
            break

    return {
        "tool": "kbtool",
        "schema": "kbtool.skill.v1",
        "description": "知识库文档检索工具。返回文档位置 + 行号，Agent 通过 read(file, start_line, end_line) 读原文。",
        "global_args": global_args,
        "commands": commands,
        "workflow": [
            "【核心原则】查询是查询，生成是生成。不要用直觉代替证据。",
            "",
            "【唯一检索入口】运行 `query --query ... --out runs/r1.md`；",
            "stdout 是 compact 路由摘要（文档 + 行号），完整证据在 runs 文件。",
            "先读 `runs/*.md`，再决定是否补查。",
            "",
            "【精读原文 — 用 kbtool read】从 query 结果获取 doc_id + line 后，",
            "`./kbtool read --doc-id <id> --around <line> --tokens \"关键词\"` 自动定位完整段落。",
            "`./kbtool read --doc-id <id> --sections` 列出文档所有章节标题+行号（文档地图）。",
            "`./kbtool read --doc-id <id> --find \"词\" --after <line>` grep+read 一体搜索。",
            "`./kbtool read --doc-id <id> --jump '210-230,450-460'` 跳读多段不连续行。",
            "`./kbtool read --doc-id doc1,doc2` 多文档并行读取。",
            "kbtool read 自动标记命中行，比通用 read 工具更强。",
            "禁止使用平台自带的 read 工具读 references/ 文件。",
            "",
            "【推理链查询】当问题包含多跳关系（A→B→C→...→答案）时，禁止一次性查询所有关键词。",
            "  1. 从起点开始，每轮只查询 1-2 个环节的关键词。",
            "  2. 读完 query 输出后，从命中行中挑选下一跳实体名。",
            "  3. 用新实体名作为下一轮查询词，逐步推进到答案。",
            "  4. 每轮使用新的 `--out` 文件名（r1, r2, r3...），保留审计轨迹。",
            "",
            "【停止规则】最多 3 轮 query。连续 2 轮无命中时停止。",
        ],
        "trigger_conditions": {
            "match": [
                "用户提问涉及知识库文档内容（概念/定义/流程/数据等）",
                "用户需要从文档中检索证据并给出引用依据",
            ],
            "do_not_match": [
                "与文档无关的通用知识问题",
                "创意写作等与文档无关的任务",
            ],
        },
        "presets": {
            "quick": "--preset quick",
            "standard": "--preset standard",
        },
        "error_recovery": {
            "no_hits": "尝试更宽泛关键词，或从已命中行中换一个实体名继续 query。",
            "needs_verification": "根据返回的 file + line 信息，读取原文确认。",
        },
    }
