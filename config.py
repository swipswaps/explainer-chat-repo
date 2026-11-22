"""Runtime configuration and canonical filesystem boundaries."""

from dataclasses import dataclass, field
import os
from pathlib import Path

DEFAULT_IGNORED_DIRS = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "__pycache__",
    ".pytest_cache", "node_modules", "dist", "build", ".next",
    "target", "data", ".build-backups", ".build-staging",
})

DEFAULT_ALLOWED_EXTENSIONS = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".jsonl",
    ".toml", ".yaml", ".yml", ".sql", ".sh", ".txt", ".md",
    ".html", ".css", ".gd", ".tscn",
})


@dataclass(frozen=True)
class ExplainerConfig:
    db_path: Path
    allowed_roots: tuple
    host: str = "127.0.0.1"
    port: int = 8765
    max_file_bytes: int = 512_000
    max_api_content_chars: int = 100_000
    max_chat_message_chars: int = 20_000
    allowed_origins: tuple = (
        "https://swipswaps.github.io",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    )
    ignored_dirs: frozenset = field(
        default_factory=lambda: DEFAULT_IGNORED_DIRS
    )
    allowed_extensions: frozenset = field(
        default_factory=lambda: DEFAULT_ALLOWED_EXTENSIONS
    )


def parse_roots(raw):
    return tuple(
        Path(item.strip()).expanduser().resolve()
        for item in raw.split(";")
        if item.strip()
    )


def is_within_roots(candidate, roots):
    resolved = Path(candidate).resolve()
    for root in roots:
        try:
            resolved.relative_to(Path(root).resolve())
            return True
        except ValueError:
            continue
    return False


def load_config_from_env():
    db_path = Path(
        os.environ.get("EXPLAINER_DB_PATH", "./data/explainer.sqlite3")
    ).expanduser().resolve()

    try:
        port = int(os.environ.get("EXPLAINER_PORT", "8765"))
        max_bytes = int(os.environ.get("EXPLAINER_MAX_FILE_BYTES", "512000"))
    except ValueError as exc:
        raise ValueError("Port and maximum file size must be integers") from exc

    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    if max_bytes < 1024:
        raise ValueError("Maximum file size must be at least 1024 bytes")

    origins_raw = os.environ.get(
        "EXPLAINER_ALLOWED_ORIGINS",
        "https://swipswaps.github.io,"
        "http://localhost:8000,"
        "http://127.0.0.1:8000",
    )
    origins = tuple(
        item.strip() for item in origins_raw.split(",") if item.strip()
    )

    if "*" in origins:
        raise ValueError("Wildcard browser origins are prohibited")

    return ExplainerConfig(
        db_path=db_path,
        allowed_roots=parse_roots(
            os.environ.get("EXPLAINER_ALLOWED_ROOTS", "")
        ),
        host=os.environ.get("EXPLAINER_HOST", "127.0.0.1").strip(),
        port=port,
        max_file_bytes=max_bytes,
        allowed_origins=origins,
    )
