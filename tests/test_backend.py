"""Regression tests for schema, FTS5, scanner and chat ingestion."""

import json
from pathlib import Path
import tempfile
import unittest

from config import ExplainerConfig
from db import apply_schema, connect_db, integrity_check
from indexer import index_project_folder, ingest_chat_session
from scanner import scan_project_folder


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.database = self.root / "test.sqlite3"
        self.config = ExplainerConfig(
            db_path=self.database,
            allowed_roots=(self.root.resolve(),),
        )
        self.conn = connect_db(self.database)
        schema = Path(__file__).resolve().parents[1] / "schema.sql"
        apply_schema(self.conn, schema)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_schema_initialization_is_idempotent(self):
        schema = Path(__file__).resolve().parents[1] / "schema.sql"
        apply_schema(self.conn, schema)
        apply_schema(self.conn, schema)
        self.assertEqual(integrity_check(self.conn), "ok")

    def test_fts_insert_update_delete(self):
        self.conn.execute(
            "INSERT INTO projects(slug, root_path, label) VALUES('p', '/p', 'p')"
        )
        project_id = self.conn.execute(
            "SELECT id FROM projects"
        ).fetchone()["id"]
        self.conn.execute(
            """
            INSERT INTO project_files(
                project_id, rel_path, sha256, size_bytes, content_text
            ) VALUES (?, 'a.txt', 'hash1', 5, 'alpha')
            """,
            (project_id,),
        )
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM project_files_fts WHERE project_files_fts MATCH 'alpha'"
        ).fetchone()[0], 1)

        self.conn.execute(
            "UPDATE project_files SET content_text = 'beta' WHERE rel_path = 'a.txt'"
        )
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM project_files_fts WHERE project_files_fts MATCH 'alpha'"
        ).fetchone()[0], 0)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM project_files_fts WHERE project_files_fts MATCH 'beta'"
        ).fetchone()[0], 1)

        self.conn.execute("DELETE FROM project_files WHERE rel_path = 'a.txt'")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM project_files_fts WHERE project_files_fts MATCH 'beta'"
        ).fetchone()[0], 0)

    def test_project_indexing_is_incremental(self):
        source = self.project / "main.py"
        source.write_text("print('alpha')", encoding="utf-8")
        first = index_project_folder(self.conn, self.project, self.config)
        second = index_project_folder(self.conn, self.project, self.config)
        self.assertEqual(first["upserted"], 1)
        self.assertEqual(second["upserted"], 0)
        source.write_text("print('beta')", encoding="utf-8")
        third = index_project_folder(self.conn, self.project, self.config)
        self.assertEqual(third["upserted"], 1)

    def test_chat_import_is_idempotent(self):
        chat = self.root / "conversation.jsonl"
        chat.write_text(
            json.dumps({"role": "user", "content": "hello alpha"}) + "\n" +
            json.dumps({"role": "assistant", "content": "hello beta"}) + "\n",
            encoding="utf-8",
        )
        first = ingest_chat_session(self.conn, chat, self.config)
        second = ingest_chat_session(self.conn, chat, self.config)
        self.assertEqual(first["messages_ingested"], 2)
        self.assertEqual(second["messages_ingested"], 2)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0],
            2,
        )

    def test_chat_outside_allowed_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as outside_dir:
            outside = Path(outside_dir) / "outside.jsonl"
            outside.write_text(
                '{"role":"user","content":"outside"}',
                encoding="utf-8",
            )
            with self.assertRaises(PermissionError):
                ingest_chat_session(self.conn, outside, self.config)

    def test_oversized_chat_is_rejected(self):
        chat = self.root / "large.jsonl"
        chat.write_text("x" * (self.config.max_file_bytes + 1), encoding="utf-8")
        with self.assertRaises(ValueError):
            ingest_chat_session(self.conn, chat, self.config)

    def test_scanner_ignores_symlink(self):
        (self.project / "safe.py").write_text("safe = True", encoding="utf-8")
        external = self.root / "external.py"
        external.write_text("secret = True", encoding="utf-8")
        (self.project / "external.py").symlink_to(external)
        records = scan_project_folder(self.project, self.config)
        names = {item.rel_path for item in records}
        self.assertIn("safe.py", names)
        self.assertNotIn("external.py", names)


if __name__ == "__main__":
    unittest.main()
