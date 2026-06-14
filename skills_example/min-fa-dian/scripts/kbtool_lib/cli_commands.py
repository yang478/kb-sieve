from __future__ import annotations

import argparse


def cmd_query(args: argparse.Namespace) -> int:
    from .query_engine import cmd_query as _cmd_query
    from .runtime import open_db, print_json, resolve_db_path, resolve_root

    root = resolve_root(args.root)
    conn = open_db(resolve_db_path(root, args.db), check_integrity=args.check_db)
    try:
        result = _cmd_query(conn, args)
        print_json(result)
        return 0
    finally:
        conn.close()


def cmd_read(args: argparse.Namespace) -> int:
    from .read_engine import cmd_read as _cmd_read

    return _cmd_read(args)
