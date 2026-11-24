"""SQLite connection lifecycle, WAL pragmas, and transactional primitives."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Any, Dict, Iterator, List, Optional, Sequence


def connect_db(db_path: Path) -> sqlite3.Connection:
    """Open SQLite in manual-transaction mode with WAL and foreign keys enabled."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=15.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA busy_timeout = 10000;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    return conn


@contextmanager
def tx_cursor(conn: sqlite3.Connection) -> Iterator[sqlite3.Cursor]:
    """Execute DML statements inside an explicit BEGIN IMMEDIATE transaction."""
    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE;")
    try:
        yield cur
        cur.execute("COMMIT;")
    except Exception:
        if conn.in_transaction:
            cur.execute("ROLLBACK;")
        raise
    finally:
        cur.close()


def apply_schema(conn: sqlite3.Connection, schema_path: Path) -> None:
    """Apply DDL inside executescript with BEGIN IMMEDIATE and COMMIT."""
    ddl = schema_path.read_text(encoding="utf-8")
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        conn.executescript("BEGIN IMMEDIATE;\n" + ddl + "\nCOMMIT;\n")
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise


def fetch_all_dicts(
    conn: sqlite3.Connection,
    sql: str,
    params: Sequence[Any] = (),
) -> List[Dict[str, Any]]:
    """Fetch all rows as dictionaries and close the cursor deterministically."""
    cur = conn.execute(sql, params)
    try:
        return [dict(row) for row in cur.fetchall()]
    finally:
        cur.close()


def fetch_one_dict(
    conn: sqlite3.Connection,
    sql: str,
    params: Sequence[Any] = (),
) -> Optional[Dict[str, Any]]:
    """Fetch a single row as a dictionary or None."""
    cur = conn.execute(sql, params)
    try:
        row = cur.fetchone()
        return dict(row) if row is not None else None
    finally:
        cur.close()


def checkpoint_wal(conn: sqlite3.Connection) -> None:
    """Flush WAL frames passively to the main database file."""
    conn.execute("PRAGMA wal_checkpoint(PASSIVE);")


def integrity_check(conn: sqlite3.Connection) -> str:
    """Return SQLite PRAGMA integrity_check status string."""
    row = conn.execute("PRAGMA integrity_check;").fetchone()
    return str(row[0]) if row else "unknown"
