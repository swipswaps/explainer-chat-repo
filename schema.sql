PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT
        (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    root_path TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    last_scan_at TEXT,
    created_at TEXT NOT NULL DEFAULT
        (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS project_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    rel_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    mime_hint TEXT NOT NULL DEFAULT 'text/plain',
    content_text TEXT,
    updated_at TEXT NOT NULL DEFAULT
        (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(project_id, rel_path)
);

CREATE INDEX IF NOT EXISTS idx_project_files_project
    ON project_files(project_id);

CREATE INDEX IF NOT EXISTS idx_project_files_hash
    ON project_files(sha256);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    source_path TEXT NOT NULL UNIQUE,
    session_key TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT 'Untitled Session',
    imported_at TEXT NOT NULL DEFAULT
        (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_project
    ON chat_sessions(project_id);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    seq_index INTEGER NOT NULL CHECK(seq_index >= 0),
    role TEXT NOT NULL CHECK(role IN ('system', 'user', 'assistant', 'tool')),
    timestamp_utc TEXT,
    content_text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    UNIQUE(session_id, seq_index)
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session
    ON chat_messages(session_id);

CREATE VIRTUAL TABLE IF NOT EXISTS project_files_fts USING fts5(
    rel_path,
    content_text,
    content='project_files',
    content_rowid='id',
    tokenize='unicode61'
);

CREATE VIRTUAL TABLE IF NOT EXISTS chat_messages_fts USING fts5(
    role,
    content_text,
    content='chat_messages',
    content_rowid='id',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS trg_project_files_ai
AFTER INSERT ON project_files
BEGIN
    INSERT INTO project_files_fts(rowid, rel_path, content_text)
    VALUES(new.id, new.rel_path, COALESCE(new.content_text, ''));
END;

CREATE TRIGGER IF NOT EXISTS trg_project_files_ad
AFTER DELETE ON project_files
BEGIN
    INSERT INTO project_files_fts(
        project_files_fts, rowid, rel_path, content_text
    )
    VALUES(
        'delete', old.id, old.rel_path, COALESCE(old.content_text, '')
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_project_files_au
AFTER UPDATE ON project_files
BEGIN
    INSERT INTO project_files_fts(
        project_files_fts, rowid, rel_path, content_text
    )
    VALUES(
        'delete', old.id, old.rel_path, COALESCE(old.content_text, '')
    );
    INSERT INTO project_files_fts(rowid, rel_path, content_text)
    VALUES(new.id, new.rel_path, COALESCE(new.content_text, ''));
END;

CREATE TRIGGER IF NOT EXISTS trg_chat_messages_ai
AFTER INSERT ON chat_messages
BEGIN
    INSERT INTO chat_messages_fts(rowid, role, content_text)
    VALUES(new.id, new.role, new.content_text);
END;

CREATE TRIGGER IF NOT EXISTS trg_chat_messages_ad
AFTER DELETE ON chat_messages
BEGIN
    INSERT INTO chat_messages_fts(
        chat_messages_fts, rowid, role, content_text
    )
    VALUES('delete', old.id, old.role, old.content_text);
END;

CREATE TRIGGER IF NOT EXISTS trg_chat_messages_au
AFTER UPDATE ON chat_messages
BEGIN
    INSERT INTO chat_messages_fts(
        chat_messages_fts, rowid, role, content_text
    )
    VALUES('delete', old.id, old.role, old.content_text);
    INSERT INTO chat_messages_fts(rowid, role, content_text)
    VALUES(new.id, new.role, new.content_text);
END;

INSERT OR IGNORE INTO schema_migrations(version, description)
VALUES(1, 'Projects, project files, chat sessions, chat messages and FTS5');
