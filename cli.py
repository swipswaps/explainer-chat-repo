"""CLI entrypoint for database initialization, scanning, ingestion, search, and serve."""
import argparse
import json
from pathlib import Path
import sys
from typing import List, Optional
from chat_ingest import parse_chat_log
from config import ExplainerConfig, load_config_from_env
from db import apply_schema, checkpoint_wal, connect_db, fetch_all_dicts, integrity_check
from indexer import index_project_folder, ingest_chat_session
from server import fts_query, run_server


def build_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="explainer-cli")
    sub = root.add_subparsers(dest="command", required=True)

    init_cmd = sub.add_parser("init")
    init_cmd.add_argument("--schema", default="schema.sql")

    scan_cmd = sub.add_parser("scan-project")
    scan_cmd.add_argument("--root", required=True)
    scan_cmd.add_argument("--label", default="")

    tree_cmd = sub.add_parser("scan-tree")
    tree_cmd.add_argument("--root", required=True)

    chat_cmd = sub.add_parser("ingest-chat")
    chat_cmd.add_argument("--log", required=True)
    chat_cmd.add_argument("--project-id", type=int)

    chat_tree_cmd = sub.add_parser("ingest-chat-tree")
    chat_tree_cmd.add_argument("--root", required=True)
    chat_tree_cmd.add_argument("--pattern", default="*.jsonl")

    dry_cmd = sub.add_parser("dry-run-chat")
    dry_cmd.add_argument("--log", required=True)

    search_cmd = sub.add_parser("search")
    search_cmd.add_argument("--q", required=True)

    serve_cmd = sub.add_parser("serve")
    serve_cmd.add_argument("--host")
    serve_cmd.add_argument("--port", type=int)
    return root


def resolve_runtime_config(args: argparse.Namespace) -> ExplainerConfig:
    base = load_config_from_env()
    roots = list(base.allowed_roots)
    arg_root = getattr(args, "root", None)
    if arg_root and not roots:
        roots.append(Path(arg_root).resolve())
    arg_log = getattr(args, "log", None)
    if arg_log and not roots:
        roots.append(Path(arg_log).resolve().parent)

    return ExplainerConfig(
        db_path=base.db_path,
        allowed_roots=tuple(roots),
        host=getattr(args, "host", None) or base.host,
        port=getattr(args, "port", None) or base.port,
        max_file_bytes=base.max_file_bytes,
        max_api_content_chars=base.max_api_content_chars,
        allowed_origins=base.allowed_origins,
        ignored_dirs=base.ignored_dirs,
        allowed_extensions=base.allowed_extensions,
    )


def emit_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = resolve_runtime_config(args)
    except ValueError as exc:
        print(f"CONFIG_ERROR: {exc}", file=sys.stderr)
        return 2

    if args.command == "dry-run-chat":
        try:
            session = parse_chat_log(Path(args.log), config)
        except Exception as exc:
            print(f"DRY_RUN_ERROR: {exc}", file=sys.stderr)
            return 2
        emit_json(
            {
                "title": session.title,
                "source_path": session.source_path,
                "message_count": len(session.messages),
            }
        )
        return 0

    conn = None
    try:
        if args.command == "init":
            conn = connect_db(config.db_path)
            apply_schema(conn, Path(args.schema).resolve())
            checkpoint_wal(conn)
            emit_json({"status": "initialized", "integrity": integrity_check(conn)})
            return 0

        if args.command == "serve":
            conn = connect_db(config.db_path)
            tables = fetch_all_dicts(
                conn,
                "SELECT name FROM sqlite_master WHERE type = 'table';",
            )
            if not any(item["name"] == "projects" for item in tables):
                raise RuntimeError("Schema missing; execute 'init' before 'serve'")
            conn.close()
            conn = None
            run_server(config)
            return 0

        conn = connect_db(config.db_path)

        if args.command == "scan-project":
            stats = index_project_folder(conn, Path(args.root), config, label=args.label)
            checkpoint_wal(conn)
            emit_json(stats)
            return 0

        if args.command == "scan-tree":
            root = Path(args.root).resolve()
            if not root.is_dir():
                raise NotADirectoryError(str(root))
            results = []
            for child in sorted(root.iterdir()):
                if (
                    not child.is_dir()
                    or child.is_symlink()
                    or child.name in config.ignored_dirs
                ):
                    continue
                results.append(index_project_folder(conn, child, config))
            checkpoint_wal(conn)
            emit_json({"projects": results})
            return 0

        if args.command == "ingest-chat":
            stats = ingest_chat_session(
                conn,
                Path(args.log),
                config,
                project_id=args.project_id,
            )
            checkpoint_wal(conn)
            emit_json(stats)
            return 0

        if args.command == "ingest-chat-tree":
            root = Path(args.root).resolve()
            if not root.is_dir():
                raise NotADirectoryError(str(root))
            results = []
            for candidate in sorted(root.rglob(args.pattern)):
                if not candidate.is_file() or candidate.is_symlink():
                    continue
                if any(part in config.ignored_dirs for part in candidate.parts):
                    continue
                results.append(ingest_chat_session(conn, candidate, config))
            checkpoint_wal(conn)
            emit_json({"ingested_sessions": len(results), "results": results})
            return 0

        if args.command == "search":
            query_expr = fts_query(args.q)
            if not query_expr:
                raise ValueError("Search query produced no valid FTS5 tokens")
            file_rows = fetch_all_dicts(
                conn,
                """
                SELECT f.id, f.rel_path,
                       snippet(project_files_fts, 1, '[', ']', '...', 16) AS excerpt
                FROM project_files_fts
                JOIN project_files f ON f.id = project_files_fts.rowid
                WHERE project_files_fts MATCH ?
                LIMIT 25;
                """,
                (query_expr,),
            )
            chat_rows = fetch_all_dicts(
                conn,
                """
                SELECT m.id, m.session_id, m.seq_index, m.role,
                       snippet(chat_messages_fts, 1, '[', ']', '...', 16) AS excerpt
                FROM chat_messages_fts
                JOIN chat_messages m ON m.id = chat_messages_fts.rowid
                WHERE chat_messages_fts MATCH ?
                LIMIT 25;
                """,
                (query_expr,),
            )
            emit_json({"query": args.q, "files": file_rows, "chats": chat_rows})
            return 0

    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        if conn is not None:
            conn.close()

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
