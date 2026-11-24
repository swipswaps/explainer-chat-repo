"""Threaded read-only HTTP server with CORS allowlist and FTS5 search."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Dict
from urllib.parse import parse_qs, urlparse
from config import ExplainerConfig
from db import connect_db, fetch_all_dicts, fetch_one_dict


def fts_query(raw: str) -> str:
    """Convert raw search input into a bounded, quoted FTS5 OR expression."""
    terms = re.findall(r"[^\W_]+(?:[-'][^\W_]+)*", raw, flags=re.UNICODE)
    quoted = ['"' + token.replace('"', '""') + '"' for token in terms[:12]]
    return " OR ".join(quoted)


def create_handler(db_path: Path, config: ExplainerConfig):
    """Build request handler class with per-thread SQLite connections."""
    thread_local_state = threading.local()

    def get_db() -> sqlite3.Connection:
        conn = getattr(thread_local_state, "conn", None)
        if conn is None:
            conn = connect_db(db_path)
            thread_local_state.conn = conn
        return conn

    class ExplainerHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _is_origin_permitted(self) -> bool:
            origin = self.headers.get("Origin")
            if not origin:
                return True
            return origin in config.allowed_origins

        def _write_cors_headers(self) -> None:
            origin = self.headers.get("Origin")
            self.send_header("Vary", "Origin")
            if origin and origin in config.allowed_origins:
                self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            if (
                self.headers.get("Access-Control-Request-Private-Network") == "true"
                and origin in config.allowed_origins
            ):
                self.send_header("Access-Control-Allow-Private-Network", "true")

        def _send_json(self, status_code: int, body: Dict[str, Any]) -> None:
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self._write_cors_headers()
            self.end_headers()
            try:
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                return

        def _parse_pagination(self, params: Dict[str, list]) -> tuple[int, int]:
            try:
                limit = int((params.get("limit") or ["25"])[0])
                offset = int((params.get("offset") or ["0"])[0])
            except ValueError as exc:
                raise ValueError("limit and offset must be integers") from exc
            if not 1 <= limit <= 100:
                raise ValueError("limit must be between 1 and 100")
            if offset < 0:
                raise ValueError("offset must be >= 0")
            return limit, offset

        def do_OPTIONS(self) -> None:
            if not self._is_origin_permitted():
                self._send_json(403, {"error": "Origin not permitted"})
                return
            self.send_response(204)
            self._write_cors_headers()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:
            if not self._is_origin_permitted():
                self._send_json(403, {"error": "Origin not permitted"})
                return

            parsed_url = urlparse(self.path)
            path = parsed_url.path
            params = parse_qs(parsed_url.query)

            try:
                conn = get_db()

                if path == "/api/health":
                    proj_row = fetch_one_dict(conn, "SELECT COUNT(*) AS n FROM projects;")
                    file_row = fetch_one_dict(conn, "SELECT COUNT(*) AS n FROM project_files;")
                    sess_row = fetch_one_dict(conn, "SELECT COUNT(*) AS n FROM chat_sessions;")
                    msg_row = fetch_one_dict(conn, "SELECT COUNT(*) AS n FROM chat_messages;")
                    self._send_json(
                        200,
                        {
                            "status": "ok",
                            "backend": "sqlite-explainer",
                            "sqlite_integrity": "ok",
                            "project_count": proj_row["n"] if proj_row else 0,
                            "indexed_file_count": file_row["n"] if file_row else 0,
                            "session_count": sess_row["n"] if sess_row else 0,
                            "message_count": msg_row["n"] if msg_row else 0,
                            "database": "SQLite WAL + FTS5",
                        },
                    )
                    return

                if path == "/api/projects":
                    limit, offset = self._parse_pagination(params)
                    rows = fetch_all_dicts(
                        conn,
                        """
                        SELECT p.id, p.slug, p.label, p.root_path, p.last_scan_at,
                               COUNT(f.id) AS file_count
                        FROM projects p
                        LEFT JOIN project_files f ON f.project_id = p.id
                        GROUP BY p.id
                        ORDER BY p.label
                        LIMIT ? OFFSET ?;
                        """,
                        (limit, offset),
                    )
                    self._send_json(200, {"projects": rows, "limit": limit, "offset": offset})
                    return

                if path == "/api/project/files":
                    project_id = (params.get("project_id") or [""])[0]
                    if not project_id.isdigit():
                        raise ValueError("Numeric project_id is required")
                    limit, offset = self._parse_pagination(params)
                    rows = fetch_all_dicts(
                        conn,
                        """
                        SELECT id, project_id, rel_path, sha256, size_bytes, mime_hint, updated_at
                        FROM project_files
                        WHERE project_id = ?
                        ORDER BY rel_path
                        LIMIT ? OFFSET ?;
                        """,
                        (int(project_id), limit, offset),
                    )
                    self._send_json(200, {"files": rows})
                    return

                if path == "/api/file":
                    file_id = (params.get("id") or [""])[0]
                    if not file_id.isdigit():
                        raise ValueError("Numeric id is required")
                    row = fetch_one_dict(
                        conn,
                        """
                        SELECT id, project_id, rel_path, sha256, size_bytes, mime_hint,
                               substr(content_text, 1, ?) AS content_text, updated_at
                        FROM project_files
                        WHERE id = ?;
                        """,
                        (config.max_api_content_chars, int(file_id)),
                    )
                    if row is None:
                        self._send_json(404, {"error": "File record not found"})
                        return
                    self._send_json(200, {"file": row})
                    return

                if path == "/api/chats":
                    limit, offset = self._parse_pagination(params)
                    rows = fetch_all_dicts(
                        conn,
                        """
                        SELECT s.id, s.project_id, s.session_key, s.title, s.imported_at,
                               COUNT(m.id) AS message_count
                        FROM chat_sessions s
                        LEFT JOIN chat_messages m ON m.session_id = s.id
                        GROUP BY s.id
                        ORDER BY s.imported_at DESC
                        LIMIT ? OFFSET ?;
                        """,
                        (limit, offset),
                    )
                    self._send_json(200, {"sessions": rows})
                    return

                if path == "/api/chat/messages":
                    session_id = (params.get("session_id") or [""])[0]
                    if not session_id.isdigit():
                        raise ValueError("Numeric session_id is required")
                    limit, offset = self._parse_pagination(params)
                    rows = fetch_all_dicts(
                        conn,
                        """
                        SELECT id, session_id, seq_index, role, timestamp_utc, content_text
                        FROM chat_messages
                        WHERE session_id = ?
                        ORDER BY seq_index
                        LIMIT ? OFFSET ?;
                        """,
                        (int(session_id), limit, offset),
                    )
                    self._send_json(200, {"messages": rows})
                    return

                if path == "/api/search":
                    raw_q = (params.get("q") or [""])[0]
                    match_expr = fts_query(raw_q)
                    if not match_expr:
                        raise ValueError("Search query 'q' must contain searchable tokens")
                    limit, offset = self._parse_pagination(params)
                    file_hits = fetch_all_dicts(
                        conn,
                        """
                        SELECT f.id, f.project_id, f.rel_path,
                               snippet(project_files_fts, 1, '[', ']', '...', 16) AS excerpt
                        FROM project_files_fts
                        JOIN project_files f ON f.id = project_files_fts.rowid
                        WHERE project_files_fts MATCH ?
                        LIMIT ? OFFSET ?;
                        """,
                        (match_expr, limit, offset),
                    )
                    chat_hits = fetch_all_dicts(
                        conn,
                        """
                        SELECT m.id, m.session_id, m.seq_index, m.role,
                               snippet(chat_messages_fts, 1, '[', ']', '...', 16) AS excerpt
                        FROM chat_messages_fts
                        JOIN chat_messages m ON m.id = chat_messages_fts.rowid
                        WHERE chat_messages_fts MATCH ?
                        LIMIT ? OFFSET ?;
                        """,
                        (match_expr, limit, offset),
                    )
                    self._send_json(
                        200,
                        {"query": raw_q, "files": file_hits, "chats": chat_hits},
                    )
                    return

                self._send_json(404, {"error": "Endpoint not found"})
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
            except sqlite3.Error as exc:
                self._send_json(500, {"error": f"SQLite query failure: {exc}"})
            except Exception as exc:
                self._send_json(500, {"error": f"Unexpected server error: {exc}"})

    return ExplainerHandler


def run_server(config: ExplainerConfig) -> None:
    """Start the threaded HTTP server and serve until interrupted."""
    server = ThreadingHTTPServer(
        (config.host, config.port),
        create_handler(config.db_path, config),
    )
    server.daemon_threads = True
    server.block_on_close = False
    print(f"Listening on {config.host}:{config.port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
