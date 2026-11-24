"""Unit tests covering SQLite schema initialization, FTS5 search, and chat ingestion."""
from pathlib import Path
import tempfile
import unittest
from chat_ingest import parse_chat_log
from config import ExplainerConfig
from db import apply_schema, connect_db, fetch_all_dicts, integrity_check
from indexer import index_project_folder, ingest_chat_session


class BackendTests(unittest.TestCase):
    def test_schema_scan_and_chat_ingest(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proj_dir = tmp_path / "sample_proj"
            chats_dir = tmp_path / "chats"
            proj_dir.mkdir()
            chats_dir.mkdir()
            (proj_dir / "notes.txt").write_text("Deterministic FTS5 indexing test.", encoding="utf-8")

            chat_file = chats_dir / "session.jsonl"
            chat_file.write_text(
                '{"role":"user","content":"Audit WAL transaction boundaries"}\n',
                encoding="utf-8",
            )

            cfg = ExplainerConfig(
                db_path=tmp_path / "test.sqlite3",
                allowed_roots=(proj_dir, chats_dir),
            )
            conn = connect_db(cfg.db_path)
            try:
                apply_schema(conn, repo_root / "schema.sql")
                apply_schema(conn, repo_root / "schema.sql")
                self.assertEqual(integrity_check(conn), "ok")

                scan_stats = index_project_folder(conn, proj_dir, cfg, label="sample")
                self.assertEqual(scan_stats["scanned"], 1)

                chat_stats = ingest_chat_session(
                    conn, chat_file, cfg, project_id=scan_stats["project_id"]
                )
                self.assertEqual(chat_stats["messages_ingested"], 1)

                hits = fetch_all_dicts(
                    conn,
                    "SELECT rowid FROM project_files_fts WHERE project_files_fts MATCH '\"FTS5\"';",
                )
                self.assertEqual(len(hits), 1)

                parsed = parse_chat_log(chat_file, cfg)
                self.assertEqual(len(parsed.messages), 1)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
