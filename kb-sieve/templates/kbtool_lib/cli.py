from __future__ import annotations

import os
import traceback
from collections.abc import Sequence

from .runtime import (
    configure_logging,
    die,
    print_json,
)

__all__ = [
    "build_parser",
    "cmd_query",
    "configure_logging",
    "die",
    "main",
    "print_json",
    "_build_skill_payload",
]


def main(argv: Sequence[str] | None = None) -> int:
    # 延迟 import：cli_commands 和 cli_parser 仅在 main() 调用时加载，
    # 避免顶部 import 触发完整的 types/cli_parser/argparse 链。
    from .cli_commands import cmd_query
    from .cli_parser import build_parser, _build_skill_payload

    configure_logging()
    parser = build_parser()
    if argv is None:
        import sys
        argv = sys.argv[1:]
    try:
        if any(arg == "--skill" for arg in argv):
            print_json(_build_skill_payload(parser))
            return 0

        args = parser.parse_args(argv)
        fn = getattr(args, "func", None)

        # Redirect to module-level names so test patches apply
        if fn is cmd_query:
            fn = cmd_query

        return int(fn(args))
    except SystemExit:
        raise
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        if os.environ.get("KBTOOL_TRACEBACK") or os.environ.get("KBTOOL_DEBUG"):
            detail += "\n" + traceback.format_exc()
        die(detail)
