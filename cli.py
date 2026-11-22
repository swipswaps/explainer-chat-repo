"""Command-line interface for project indexing, chat ingestion and search."""

import argparse
import fnmatch
import json
import os
from pathlib import Path
import sys

from chat_ingest import parse_chat_log
from config import ExplainerConfig, load_config_from_env
from db import (
    apply_schema, checkpoint_wal, connect_db, integrity_check,
    rows_as_dicts,
)
from indexer import index_project_folder, ingest_chat_session
from server import fts_query, run_server


def build_parser():
    parser = argparse.ArgumentParser(prog="explainer-cli")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init")
    init.add_argument("--schema", default="schema.sql")

    scan = commands.add_parser("scan-project")
    scan.add_argument("--root", required=True)
    scan.add_argument("--label", default="")

    tree = commands.add_parser("scan-tree")
    tree.add_argument("--root", required=True)

    chat = commands.add_parser("ingest-chat")
    chat.add_argument("--log", required=True)
    chat.add_argument("--project-id", type=int)

    chat_tree = commands.add_parser("ingest-chat-tree")
    chat_tree.add_argument("--root", required=True)
    chat_tree.add_argument("--pattern", default="*.jsonl")

    dry = commands.add_parser("dry-run-chat")
    dry.add_argument("--log", required=True)

    search = commands.add_parser("search")
    search.add_argument("--q", required=True)

    serve = commands.add_parser("serve")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    return parser


def make_config(args):
    base = load_config_from_env()
    roots = list(base.allowed_roots)

    candidate = getattr(args, "root", None)
    log = getattr(args, "log", None)

    if not roots and candidate:
        roots.append(Path(candidate).expanduser().resolve())
    if not roots and log:
        roots.append(Path(log).expanduser().resolve().parent)

    return ExplainerConfig(
        db_path=base.db_path,
        allowed_roots=tuple(roots),
        host=getattr(args, "host", None) or base.host,
        port=getattr(args, "port", None) or base.port,
        max_file_bytes=base.max_file_bytes,
        max_api_content_chars=base.max_api_content_chars,
        max_chat_message_chars=base.max_chat_message_chars,
        allowed_origins=base.allowed_origins,
        ignored_dirs=base.ignored_dirs,
        allowed_extensions=base.allowed_extensions,
    )


def output(value):
    print(json.dumps(value, indent=2, ensure_ascii=False))


def main(argv=None):
    args = build_parser().parse_args(argv)

    try:
        config = make_config(args)
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if args.command == "dry-run-chat":
        try:
            session = parse_chat_log(Path(args.log), config)
            output({
                "title": session.title,
                "source_path": session.source_path,
                "message_count": len(session.messages),
            })
            return 0
        except Exception as exc:
            print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2

    conn = None
    try:
        if args.command == "init":
            conn = connect_db(config.db_path)
            apply_schema(conn, Path(args.schema).resolve())
            checkpoint_wal(conn)
            output({"status": "initialized", "integrity": integrity_check(conn)})
            return 0

        if args.command == "serve":
            conn = connect_db(config.db_path)
            tables = rows_as_dicts(conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ))
            if not any(row["name"] == "projects" for row in tables):
                raise RuntimeError("Database not initialized. Run init first.")
            conn.close()
            conn = None
            run_server(config)
            return 0

        conn = connect_db(config.db_path)

        if args.command == "scan-project":
            result = index_project_folder(
                conn, Path(args.root), config, label=args.label or None
            )
            checkpoint_wal(conn)
            output(result)
            return 0

        if args.command == "scan-tree":
            root = Path(args.root).resolve()
            if not root.is_dir():
                raise NotADirectoryError(str(root))
            if not config.allowed_roots:
                raise PermissionError("No allowed roots configured")
            results = []
            for child in sorted(root.iterdir()):
                if child.is_symlink() or not child.is_dir():
                    continue
                if child.name in config.ignored_dirs:
                    continue
                results.append(index_project_folder(conn, child, config))
            checkpoint_wal(conn)
            output({"projects": results})
            return 0

        if args.command == "ingest-chat":
            result = ingest_chat_session(
                conn, Path(args.log), config, project_id=args.project_id
            )
            checkpoint_wal(conn)
            output(result)
            return 0

        if args.command == "ingest-chat-tree":
            root = Path(args.root).resolve()
            if not root.is_dir():
                raise NotADirectoryError(str(root))
            if not config.allowed_roots:
                raise PermissionError("No allowed roots configured")

            results = []
            for current, dirnames, filenames in os.walk(
                root, topdown=True, followlinks=False
            ):
                current_path = Path(current)
                dirnames[:] = [
                    name for name in sorted(dirnames)
                    if name not in config.ignored_dirs
                    and not (current_path / name).is_symlink()
                ]
                for filename in sorted(filenames):
                    path = current_path / filename
                    if path.is_symlink() or not fnmatch.fnmatch(filename, args.pattern):
                        continue
                    results.append(ingest_chat_session(conn, path, config))
            checkpoint_wal(conn)
            output({"ingested_sessions": len(results), "results": results})
            return 0

        if args.command == "search":
            expression = fts_query(args.q)
            if not expression:
                raise ValueError("Search query contains no searchable terms")
            files = rows_as_dicts(conn.execute(
                """
                SELECT f.id, f.rel_path,
                       snippet(project_files_fts, 1, '[', ']', '...', 16) AS excerpt
                FROM project_files_fts
                JOIN project_files f ON f.id = project_files_fts.rowid
                WHERE project_files_fts MATCH ?
                LIMIT 25
                """,
                (expression,),
            ))
            chats = rows_as_dicts(conn.execute(
                """
                SELECT m.id, m.session_id, m.seq_index, m.role,
                       snippet(chat_messages_fts, 1, '[', ']', '...', 16) AS excerpt
                FROM chat_messages_fts
                JOIN chat_messages m ON m.id = chat_messages_fts.rowid
                WHERE chat_messages_fts MATCH ?
                LIMIT 25
                """,
                (expression,),
            ))
            output({"query": args.q, "files": files, "chats": chats})
            return 0

        raise ValueError(f"Unsupported command: {args.command}")

    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
