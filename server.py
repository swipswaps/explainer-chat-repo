"""Read-only local HTTP API with restricted browser origins."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import sqlite3
import threading
from urllib.parse import parse_qs, urlparse

from db import connect_db, one_as_dict, rows_as_dicts


def fts_query(raw):
    terms = re.findall(r"[^\W_]+(?:[-'][^\W_]+)*", raw, flags=re.UNICODE)
    return " OR ".join('"' + term.replace('"', '""') + '"' for term in terms[:12])


def create_handler(config):
    local = threading.local()

    def get_connection():
        conn = getattr(local, "connection", None)
        if conn is None:
            conn = connect_db(config.db_path)
            local.connection = conn
        return conn

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            return

        def origin_allowed(self):
            origin = self.headers.get("Origin")
            return not origin or origin in config.allowed_origins

        def cors_headers(self):
            origin = self.headers.get("Origin")
            self.send_header("Vary", "Origin")
            if origin and origin in config.allowed_origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                if self.headers.get(
                    "Access-Control-Request-Private-Network"
                ) == "true":
                    self.send_header("Access-Control-Allow-Private-Network", "true")

        def send_json(self, code, payload):
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.cors_headers()
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_OPTIONS(self):
            if not self.origin_allowed():
                self.send_json(403, {"error": "Browser origin not allowed"})
                return
            self.send_response(204)
            self.cors_headers()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def pagination(self, query):
            try:
                limit = int((query.get("limit") or ["25"])[0])
                offset = int((query.get("offset") or ["0"])[0])
            except ValueError as exc:
                raise ValueError("Invalid pagination") from exc
            if not 1 <= limit <= 100:
                raise ValueError("limit must be between 1 and 100")
            if offset < 0:
                raise ValueError("offset cannot be negative")
            return limit, offset

        def do_GET(self):
            if not self.origin_allowed():
                self.send_json(403, {"error": "Browser origin not allowed"})
                return

            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)

            try:
                conn = get_connection()
                path = parsed.path

                if path == "/api/health":
                    counts = {}
                    for table, key in (
                        ("projects", "project_count"),
                        ("project_files", "indexed_file_count"),
                        ("chat_sessions", "session_count"),
                        ("chat_messages", "message_count"),
                    ):
                        row = one_as_dict(
                            conn.execute(f"SELECT COUNT(*) AS n FROM {table}")
                        )
                        counts[key] = row["n"]
                    self.send_json(200, {
                        "status": "ok",
                        "backend": "sqlite-explainer",
                        "sqlite_integrity": one_as_dict(
                            conn.execute("PRAGMA quick_check")
                        )["quick_check"],
                        **counts,
                    })
                    return

                if path == "/api/projects":
                    limit, offset = self.pagination(query)
                    rows = rows_as_dicts(conn.execute(
                        """
                        SELECT p.id, p.slug, p.label, p.last_scan_at,
                               COUNT(f.id) AS file_count
                        FROM projects p
                        LEFT JOIN project_files f ON f.project_id = p.id
                        GROUP BY p.id
                        ORDER BY p.label
                        LIMIT ? OFFSET ?
                        """,
                        (limit, offset),
                    ))
                    self.send_json(200, {"projects": rows, "limit": limit, "offset": offset})
                    return

                if path == "/api/project/files":
                    project_id = (query.get("project_id") or [""])[0]
                    if not project_id.isdigit():
                        raise ValueError("Numeric project_id required")
                    limit, offset = self.pagination(query)
                    rows = rows_as_dicts(conn.execute(
                        """
                        SELECT id, project_id, rel_path, sha256, size_bytes,
                               mime_hint, updated_at
                        FROM project_files
                        WHERE project_id = ?
                        ORDER BY rel_path
                        LIMIT ? OFFSET ?
                        """,
                        (int(project_id), limit, offset),
                    ))
                    self.send_json(200, {"files": rows, "limit": limit, "offset": offset})
                    return

                if path == "/api/file":
                    file_id = (query.get("id") or [""])[0]
                    if not file_id.isdigit():
                        raise ValueError("Numeric id required")
                    row = one_as_dict(conn.execute(
                        """
                        SELECT id, project_id, rel_path, sha256, size_bytes,
                               mime_hint,
                               substr(content_text, 1, ?) AS content_text,
                               updated_at
                        FROM project_files WHERE id = ?
                        """,
                        (config.max_api_content_chars, int(file_id)),
                    ))
                    if row is None:
                        self.send_json(404, {"error": "Not found"})
                    else:
                        self.send_json(200, {"file": row})
                    return

                if path == "/api/chats":
                    limit, offset = self.pagination(query)
                    rows = rows_as_dicts(conn.execute(
                        """
                        SELECT s.id, s.project_id, s.session_key, s.title,
                               s.imported_at, COUNT(m.id) AS message_count
                        FROM chat_sessions s
                        LEFT JOIN chat_messages m ON m.session_id = s.id
                        GROUP BY s.id
                        ORDER BY s.imported_at DESC
                        LIMIT ? OFFSET ?
                        """,
                        (limit, offset),
                    ))
                    self.send_json(200, {"sessions": rows, "limit": limit, "offset": offset})
                    return

                if path == "/api/chat/messages":
                    session_id = (query.get("session_id") or [""])[0]
                    if not session_id.isdigit():
                        raise ValueError("Numeric session_id required")
                    limit, offset = self.pagination(query)
                    rows = rows_as_dicts(conn.execute(
                        """
                        SELECT id, session_id, seq_index, role, timestamp_utc,
                               substr(content_text, 1, ?) AS content_text
                        FROM chat_messages
                        WHERE session_id = ?
                        ORDER BY seq_index
                        LIMIT ? OFFSET ?
                        """,
                        (
                            config.max_chat_message_chars,
                            int(session_id), limit, offset,
                        ),
                    ))
                    self.send_json(200, {"messages": rows, "limit": limit, "offset": offset})
                    return

                if path == "/api/search":
                    raw = (query.get("q") or [""])[0]
                    if not raw.strip() or len(raw) > 256:
                        raise ValueError("q must contain 1 to 256 characters")
                    expression = fts_query(raw)
                    if not expression:
                        raise ValueError("Search query contains no searchable terms")
                    limit, offset = self.pagination(query)
                    files = rows_as_dicts(conn.execute(
                        """
                        SELECT f.id, f.project_id, f.rel_path,
                               snippet(project_files_fts, 1, '[', ']', '...', 16)
                                   AS excerpt
                        FROM project_files_fts
                        JOIN project_files f ON f.id = project_files_fts.rowid
                        WHERE project_files_fts MATCH ?
                        LIMIT ? OFFSET ?
                        """,
                        (expression, limit, offset),
                    ))
                    chats = rows_as_dicts(conn.execute(
                        """
                        SELECT m.id, m.session_id, m.seq_index, m.role,
                               snippet(chat_messages_fts, 1, '[', ']', '...', 16)
                                   AS excerpt
                        FROM chat_messages_fts
                        JOIN chat_messages m ON m.id = chat_messages_fts.rowid
                        WHERE chat_messages_fts MATCH ?
                        LIMIT ? OFFSET ?
                        """,
                        (expression, limit, offset),
                    ))
                    self.send_json(200, {
                        "query": raw,
                        "files": files,
                        "chats": chats,
                        "limit": limit,
                        "offset": offset,
                    })
                    return

                self.send_json(404, {"error": "Not found"})

            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
            except sqlite3.Error:
                self.send_json(500, {"error": "Database operation failed"})
            except Exception:
                self.send_json(500, {"error": "Internal server error"})

    return Handler


def run_server(config):
    server = ThreadingHTTPServer(
        (config.host, config.port),
        create_handler(config),
    )
    server.daemon_threads = True
    server.block_on_close = False
    print(f"Listening on {config.host}:{config.port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
