"""Transactional project indexing and idempotent chat synchronization."""

import hashlib
from pathlib import Path
import re

from chat_ingest import parse_chat_log
from db import tx_cursor
from scanner import scan_project_folder


def unique_slug(label, root):
    base = re.sub(r"[^a-zA-Z0-9_-]+", "-", label.strip().lower()).strip("-")
    base = base or "project"
    digest = hashlib.sha256(root.encode("utf-8")).hexdigest()[:8]
    return f"{base}-{digest}"


def index_project_folder(conn, project_root, config, label=None):
    root = Path(project_root).resolve()

    # Scan first. An incomplete scan must never prune existing indexed rows.
    scanned = scan_project_folder(root, config)
    project_label = label or root.name
    slug = unique_slug(project_label, str(root))
    upserted = 0
    deleted = 0

    with tx_cursor(conn) as cur:
        cur.execute(
            """
            INSERT INTO projects(slug, root_path, label, last_scan_at)
            VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(root_path) DO UPDATE SET
                slug = excluded.slug,
                label = excluded.label,
                last_scan_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (slug, str(root), project_label),
        )
        project_id = cur.execute(
            "SELECT id FROM projects WHERE root_path = ?", (str(root),)
        ).fetchone()["id"]

        existing = {
            row["rel_path"]: row["sha256"]
            for row in cur.execute(
                "SELECT rel_path, sha256 FROM project_files WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        }

        active_paths = set()
        for item in scanned:
            active_paths.add(item.rel_path)
            if existing.get(item.rel_path) == item.sha256:
                continue
            cur.execute(
                """
                INSERT INTO project_files(
                    project_id, rel_path, sha256, size_bytes, mime_hint,
                    content_text
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, rel_path) DO UPDATE SET
                    sha256 = excluded.sha256,
                    size_bytes = excluded.size_bytes,
                    mime_hint = excluded.mime_hint,
                    content_text = excluded.content_text,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                (
                    project_id, item.rel_path, item.sha256, item.size_bytes,
                    item.mime_hint, item.content_text,
                ),
            )
            upserted += 1

        for stale in set(existing) - active_paths:
            cur.execute(
                "DELETE FROM project_files WHERE project_id = ? AND rel_path = ?",
                (project_id, stale),
            )
            deleted += 1

    return {
        "project_id": int(project_id),
        "scanned": len(scanned),
        "upserted": upserted,
        "deleted": deleted,
    }


def ingest_chat_session(conn, log_path, config, project_id=None):
    parsed = parse_chat_log(log_path, config)
    count = len(parsed.messages)

    with tx_cursor(conn) as cur:
        cur.execute(
            """
            INSERT INTO chat_sessions(project_id, source_path, session_key, title)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source_path) DO UPDATE SET
                project_id = COALESCE(excluded.project_id, chat_sessions.project_id),
                session_key = excluded.session_key,
                title = excluded.title,
                imported_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (project_id, parsed.source_path, parsed.session_key, parsed.title),
        )
        session_id = cur.execute(
            "SELECT id FROM chat_sessions WHERE source_path = ?",
            (parsed.source_path,),
        ).fetchone()["id"]

        for message in parsed.messages:
            cur.execute(
                """
                INSERT INTO chat_messages(
                    session_id, seq_index, role, timestamp_utc,
                    content_text, content_hash
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, seq_index) DO UPDATE SET
                    role = excluded.role,
                    timestamp_utc = excluded.timestamp_utc,
                    content_text = excluded.content_text,
                    content_hash = excluded.content_hash
                """,
                (
                    session_id, message.seq_index, message.role,
                    message.timestamp_utc, message.content_text,
                    message.content_hash,
                ),
            )

        pruned = cur.execute(
            "DELETE FROM chat_messages WHERE session_id = ? AND seq_index >= ?",
            (session_id, count),
        ).rowcount

    return {
        "session_id": int(session_id),
        "messages_ingested": count,
        "messages_pruned": int(pruned or 0),
    }
