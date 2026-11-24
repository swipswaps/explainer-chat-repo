FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EXPLAINER_DB_PATH=/data/explainer.sqlite3 \
    EXPLAINER_HOST=0.0.0.0 \
    EXPLAINER_PORT=8765

WORKDIR /app

COPY schema.sql config.py db.py scanner.py chat_ingest.py indexer.py server.py cli.py ./

RUN mkdir -p /data /workspace /host_root

CMD ["sh", "-c", "python3 /app/cli.py init --schema /app/schema.sql && python3 /app/cli.py scan-project --root /workspace --label explainer-chat-repo && exec python3 /app/cli.py serve"]
