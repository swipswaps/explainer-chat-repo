explainer-chat-repo
===================

Purpose:
Local SQLite WAL + FTS5 indexer and read-only HTTP query server for project
files and chat transcripts, paired with a GitHub Pages static view.

Quick Start:
1. Initialize schema:
   python3 cli.py init --schema schema.sql
2. Index a project directory:
   python3 cli.py scan-project --root . --label explainer-chat-repo
3. Start the loopback API server:
   python3 cli.py serve
