FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV EXPLAINER_DB_PATH=/data/explainer.sqlite3
ENV EXPLAINER_HOST=0.0.0.0
ENV EXPLAINER_PORT=8765

COPY schema.sql config.py db.py scanner.py chat_ingest.py \
     indexer.py server.py cli.py ./

RUN mkdir -p /data /workspace /host_root

CMD ["sh", "-c", "python3 /app/cli.py init --schema /app/schema.sql && python3 /app/cli.py scan-project --root /workspace --label explainer-chat-repo && exec python3 /app/cli.py serve"]
