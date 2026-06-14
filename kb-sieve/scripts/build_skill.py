#!/usr/bin/env python3
from __future__ import annotations

from build_skill_lib.cli import main as cli_main


def main(argv: list[str] | None = None) -> int:
    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
