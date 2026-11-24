"""Transactional project and chat indexing with scan-before-write ordering."""
import hashlib
from pathlib import Path
import re
import sqlite3
from typing import Dict, Optional
from chat_ingest import parse_chat_log
from config import ExplainerConfig
from db import tx_cursor
from scanner import scan_project_folder


def unique_slug(label: str, root: str) -> str:
    """Create a deterministic slug combining normalized label and root hash."""
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", label.strip().lower()).strip("-") or "project"
    root_hash = hashlib.sha256(root.encode("utf-8")).hexdigest()[:8]
    return f"{cleaned}-{root_hash}"


def index_project_folder(
    conn: sqlite3.Connection,
    project_root: Path,
    config: ExplainerConfig,
    label: Optional[str] = None,
) -> Dict[str, int]:
    """Scan project files first, then atomically upsert project and file records."""
    resolved = project_root.resolve()
    scanned_items = scan_project_folder(resolved, config)
    project_label = (label or "").strip() or resolved.name
    slug = unique_slug(project_label, str(resolved))
    upserted = 0
    deleted = 0

    with tx_cursor(conn) as cur:
        cur.execute(
            """
            INSERT INTO projects(slug, root_path, label, last_scan_at)
            VALUES(?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(root_path) DO UPDATE SET
                slug = excluded.slug,
                label = excluded.label,
                last_scan_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now');
            """,
            (slug, str(resolved), project_label),
        )
        row = cur.execute(
            "SELECT id FROM projects WHERE root_path = ?;",
            (str(resolved),),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"Failed to resolve project id for {resolved}")
        project_id = int(row["id"])

        existing = {
            item["rel_path"]: item["sha256"]
            for item in cur.execute(
                "SELECT rel_path, sha256 FROM project_files WHERE project_id = ?;",
                (project_id,),
            ).fetchall()
        }
        seen_paths = set()
        for entry in scanned_items:
            seen_paths.add(entry.rel_path)
            if existing.get(entry.rel_path) == entry.sha256:
                continue
            cur.execute(
                """
                INSERT INTO project_files(
                    project_id, rel_path, sha256, size_bytes, mime_hint, content_text
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, rel_path) DO UPDATE SET
                    sha256 = excluded.sha256,
                    size_bytes = excluded.size_bytes,
                    mime_hint = excluded.mime_hint,
                    content_text = excluded.content_text,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now');
                """,
                (
                    project_id,
                    entry.rel_path,
                    entry.sha256,
                    entry.size_bytes,
                    entry.mime_hint,
                    entry.content_text,
                ),
            )
            upserted += 1

        for stale_rel in set(existing) - seen_paths:
            cur.execute(
                "DELETE FROM project_files WHERE project_id = ? AND rel_path = ?;",
                (project_id, stale_rel),
            )
            deleted += 1

    return {
        "project_id": project_id,
        "scanned": len(scanned_items),
        "upserted": upserted,
        "deleted": deleted,
    }


def ingest_chat_session(
    conn: sqlite3.Connection,
    log_path: Path,
    config: ExplainerConfig,
    project_id: Optional[int] = None,
) -> Dict[str, int]:
    """Parse a chat transcript and atomically upsert session and messages."""
    parsed = parse_chat_log(log_path, config)
    msg_count = len(parsed.messages)

    with tx_cursor(conn) as cur:
        cur.execute(
            """
            INSERT INTO chat_sessions(project_id, source_path, session_key, title)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source_path) DO UPDATE SET
                project_id = COALESCE(excluded.project_id, chat_sessions.project_id),
                session_key = excluded.session_key,
                title = excluded.title,
                imported_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now');
            """,
            (project_id, parsed.source_path, parsed.session_key, parsed.title),
        )
        row = cur.execute(
            "SELECT id FROM chat_sessions WHERE source_path = ?;",
            (parsed.source_path,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"Failed to resolve chat session id for {parsed.source_path}")
        session_id = int(row["id"])

        for msg in parsed.messages:
            cur.execute(
                """
                INSERT INTO chat_messages(
                    session_id, seq_index, role, timestamp_utc, content_text, content_hash
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, seq_index) DO UPDATE SET
                    role = excluded.role,
                    timestamp_utc = excluded.timestamp_utc,
                    content_text = excluded.content_text,
                    content_hash = excluded.content_hash;
                """,
                (
                    session_id,
                    msg.seq_index,
                    msg.role,
                    msg.timestamp_utc,
                    msg.content_text,
                    msg.content_hash,
                ),
            )
        pruned = cur.execute(
            "DELETE FROM chat_messages WHERE session_id = ? AND seq_index >= ?;",
            (session_id, msg_count),
        ).rowcount

    return {
        "session_id": session_id,
        "messages_ingested": msg_count,
        "messages_pruned": int(pruned or 0),
    }
