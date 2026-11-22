EXPLAINER CHAT + PROJECT INDEXER
================================

Architecture
------------

This repository provides a local-first project and chat indexing backend.

Components:

  schema.sql       SQLite schema and FTS5 synchronization triggers
  config.py        Runtime configuration and canonical path boundaries
  db.py            SQLite WAL lifecycle and transaction management
  scanner.py       Bounded project scanning and file hashing
  chat_ingest.py   JSONL, JSON and role-labelled text ingestion
  indexer.py       Transactional project and chat synchronization
  server.py        Read-only local HTTP API
  cli.py           Initialization, scanning, ingestion and search
  tests/           Regression tests
  Dockerfile       Python application container
  docker-compose.yml
                   Localhost-only API deployment
  index.html       Static frontend for GitHub Pages

Initialize
----------

  python3 cli.py init

Index one project
-----------------

  python3 cli.py scan-project --root /absolute/path/to/project

Index immediate child project directories
------------------------------------------

  python3 cli.py scan-tree --root /absolute/path/to/projects

Ingest one chat export
----------------------

  python3 cli.py dry-run-chat --log /absolute/path/to/chat.jsonl
  python3 cli.py ingest-chat --log /absolute/path/to/chat.jsonl

Ingest JSONL exports recursively
--------------------------------

  python3 cli.py ingest-chat-tree --root /absolute/path/to/chat-exports

Search
------

  python3 cli.py search --q "SQLite transaction"

API
---

  GET /api/health
  GET /api/projects?limit=25&offset=0
  GET /api/project/files?project_id=1&limit=25&offset=0
  GET /api/file?id=1
  GET /api/chats?limit=25&offset=0
  GET /api/chat/messages?session_id=1&limit=25&offset=0
  GET /api/search?q=SQLite&limit=25&offset=0

Operational notes
-----------------

The database is stored in ./data and is excluded from Git.

The Docker container indexes /workspace at startup. It does not automatically
crawl /host_root. Host directories and chat exports are indexed only when an
operator explicitly invokes a CLI command.

Files larger than the configured indexing text limit are hashed and recorded
as metadata, but their contents are not stored for full-text search.

The backend is local. GitHub Pages hosts only the static frontend; it does not
host the SQLite database or the Python backend.

Chat exports may contain sensitive material. Review the source files and the
configured roots before importing them.
