"""Multi-repo and chat-transcript bridge into SQLite WAL + FTS5.

Corrected relative to PATCH_GAPS_AND_VERIFY:
  - schema_path existence is checked with a typed, labelled error (E3)
  - chat_pattern default matches .jsonl and .json transcripts (E4)
  - rebuild_fts_indexes() checkpoints the WAL first (E6)
  - empty scans are labelled in the return payload (U4)

References:
  FTS5 rebuild command:
    https://www.sqlite.org/fts5.html#the_rebuild_command
  sqlite3 executescript implicit COMMIT:
    https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.executescript
"""
from pathlib import Path
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Tuple

from config import ExplainerConfig
from db import apply_schema, checkpoint_wal, connect_db
from indexer import index_project_folder, ingest_chat_session


DEFAULT_CHAT_PATTERNS: Tuple[str, ...] = ("*.jsonl", "*.json")


def rebuild_fts_indexes(conn: sqlite3.Connection) -> None:
    """Rebuild external-content FTS5 indexes from base tables.

    The WAL is checkpointed first so the rebuild reads against a bounded
    set of frames. This does not change correctness (FTS5 reads through
    the WAL either way) but bounds the rebuild's memory footprint.
    """
    checkpoint_wal(conn)
    conn.execute("INSERT INTO project_files_fts(project_files_fts) VALUES('rebuild');")
    conn.execute("INSERT INTO chat_messages_fts(chat_messages_fts) VALUES('rebuild');")


def _iter_chat_files(chats_dir: Path, patterns: Iterable[str]) -> List[Path]:
    """Union of rglob matches across patterns, sorted, symlink-free."""
    seen: Dict[str, Path] = {}
    for pattern in patterns:
        for path in chats_dir.rglob(pattern):
            if not path.is_file() or path.is_symlink():
                continue
            key = str(path.resolve())
            seen.setdefault(key, path)
    return [seen[k] for k in sorted(seen)]


def sync_repo_and_chats(
    db_path: Path,
    schema_path: Path,
    project_root: Path,
    label: str,
    chats_dir: Optional[Path] = None,
    chat_patterns: Iterable[str] = DEFAULT_CHAT_PATTERNS,
    max_file_bytes: int = 512_000,
) -> Dict[str, Any]:
    """Initialize schema, scan a project, ingest matching chat logs."""
    if not schema_path.exists():
        raise ValueError(f"schema_path does not exist: {schema_path}")

    resolved_root = project_root.resolve()
    if not resolved_root.is_dir():
        raise ValueError(f"project_root is not a directory: {resolved_root}")

    roots: List[Path] = [resolved_root]
    resolved_chats: Optional[Path] = None
    if chats_dir is not None:
        candidate = chats_dir.expanduser().resolve()
        if candidate.is_dir():
            resolved_chats = candidate
            if candidate != resolved_root:
                roots.append(candidate)

    cfg = ExplainerConfig(
        db_path=db_path.resolve(),
        allowed_roots=tuple(roots),
        max_file_bytes=max_file_bytes,
    )
    conn: sqlite3.Connection = connect_db(cfg.db_path)
    try:
        apply_schema(conn, schema_path.resolve())
        scan_stats = index_project_folder(conn, resolved_root, cfg, label=label)

        ingested: List[Dict[str, int]] = []
        if resolved_chats is not None:
            for log_file in _iter_chat_files(resolved_chats, chat_patterns):
                ingested.append(
                    ingest_chat_session(
                        conn, log_file, cfg, project_id=scan_stats["project_id"]
                    )
                )

        rebuild_fts_indexes(conn)
        checkpoint_wal(conn)

        return {
            "project": label,
            "root_path": str(resolved_root),
            "scan": scan_stats,
            "empty_scan": scan_stats.get("scanned", 0) == 0,
            "chat_sessions_ingested": len(ingested),
            "sessions": ingested,
        }
    finally:
        conn.close()


def sync_many_repos(
    db_path: Path,
    schema_path: Path,
    targets: Iterable[Tuple[str, Path, Optional[Path]]],
    chat_patterns: Iterable[str] = DEFAULT_CHAT_PATTERNS,
    max_file_bytes: int = 512_000,
    log: Any = None,
) -> Dict[str, Any]:
    """Sync each (label, project_root, chats_dir) that resolves to a dir.

    `log` is any callable(str); if provided, skip and success events are
    emitted. This addresses E5 (silent skips).
    """
    def _emit(msg: str) -> None:
        if callable(log):
            log(msg)

    results: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    for label, root_path, chats_path in targets:
        resolved = Path(root_path).expanduser().resolve()
        if not resolved.is_dir():
            entry = {"label": label, "root_path": str(resolved), "reason": "missing_dir"}
            skipped.append(entry)
            _emit(f"skip: {label} ({entry['reason']})")
            continue
        try:
            outcome = sync_repo_and_chats(
                db_path=db_path,
                schema_path=schema_path,
                project_root=resolved,
                label=label,
                chats_dir=chats_path,
                chat_patterns=chat_patterns,
                max_file_bytes=max_file_bytes,
            )
            results.append(outcome)
            _emit(f"sync: {label} scanned={outcome['scan']['scanned']} "
                  f"upserted={outcome['scan']['upserted']} "
                  f"chats={outcome['chat_sessions_ingested']}")
        except Exception as exc:
            entry = {"label": label, "root_path": str(resolved),
                     "reason": f"{type(exc).__name__}: {exc}"}
            skipped.append(entry)
            _emit(f"error: {label} — {entry['reason']}")

    return {"synced_count": len(results), "synced": results, "skipped": skipped}
