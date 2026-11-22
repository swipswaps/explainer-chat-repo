"""SQLite WAL connection lifecycle and explicit transaction boundaries."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3


def connect_db(db_path):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(db_path),
        timeout=15.0,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn


@contextmanager
def tx_cursor(conn):
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    try:
        yield cursor
        cursor.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        cursor.close()


def apply_schema(conn, schema_path):
    """executescript commits pending transactions; BEGIN/COMMIT belong inside."""
    ddl = Path(schema_path).read_text(encoding="utf-8")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executescript("BEGIN IMMEDIATE;\n" + ddl + "\nCOMMIT;\n")
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise


def checkpoint_wal(conn):
    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")


def integrity_check(conn):
    row = conn.execute("PRAGMA quick_check").fetchone()
    return str(row[0]) if row else "unknown"


def rows_as_dicts(cursor):
    try:
        return [dict(row) for row in cursor.fetchall()]
    finally:
        cursor.close()


def one_as_dict(cursor):
    try:
        row = cursor.fetchone()
        return dict(row) if row is not None else None
    finally:
        cursor.close()
