SECURITY AND DATA BOUNDARIES
============================

1. Docker publishes the backend only on 127.0.0.1.
2. The browser API accepts only configured origins.
3. The API exposes read-only GET and OPTIONS operations.
4. SQL values are parameterized.
5. Filesystem paths are canonicalized and checked against allowlisted roots.
6. Project scanning does not follow symbolic-link directories or files.
7. Chat imports reject oversized files rather than silently truncating them.
8. Source mounts are read-only.
9. The data directory is writable by the invoking host UID/GID only.
10. The container drops Linux capabilities and uses no-new-privileges.
11. The database, WAL and shared-memory files are excluded from Git.
12. Localhost binding is not authentication. Other local processes can access
    the API.
13. Do not expose the backend port to a public network.
14. Chat transcripts may contain credentials, tokens, personal information,
    proprietary source code and other sensitive material.
15. The backend does not automatically redact imported content.
16. GitHub Pages receives the static frontend only, not the local database.
