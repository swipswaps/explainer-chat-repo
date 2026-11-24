"""Configuration model enforcing loopback defaults and allowlisted origins."""
from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Tuple

DEFAULT_IGNORED_DIRS = frozenset({
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    "dist",
    "build",
    ".next",
    "target",
    "data",
    ".build-evidence",
    ".build-backups",
    ".build-staging",
})

DEFAULT_ALLOWED_EXTENSIONS = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".jsonl",
    ".toml", ".yaml", ".yml", ".sql", ".sh", ".txt", ".md",
    ".html", ".css", ".gd", ".tscn",
})


@dataclass(frozen=True)
class ExplainerConfig:
    """Immutable configuration object for CLI ingestion and HTTP serving."""

    db_path: Path
    allowed_roots: Tuple[Path, ...]
    host: str = "127.0.0.1"
    port: int = 8765
    max_file_bytes: int = 512_000
    max_api_content_chars: int = 100_000
    allowed_origins: Tuple[str, ...] = (
        "https://swipswaps.github.io",
        "https://start.fedoraproject.org",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    )
    ignored_dirs: frozenset = field(default_factory=lambda: DEFAULT_IGNORED_DIRS)
    allowed_extensions: frozenset = field(default_factory=lambda: DEFAULT_ALLOWED_EXTENSIONS)


def parse_roots(raw: str) -> Tuple[Path, ...]:
    """Split root paths on semicolons so directory names with commas remain valid."""
    resolved_roots = []
    for entry in raw.split(";"):
        cleaned = entry.strip()
        if cleaned:
            resolved_roots.append(Path(cleaned).expanduser().resolve())
    return tuple(resolved_roots)


def is_within_roots(candidate: Path, roots: Tuple[Path, ...]) -> bool:
    """Verify candidate resolves strictly inside at least one configured root."""
    resolved_candidate = candidate.resolve()
    for root in roots:
        resolved_root = root.resolve()
        try:
            resolved_candidate.relative_to(resolved_root)
            return True
        except ValueError:
            continue
    return False


def load_config_from_env() -> ExplainerConfig:
    """Load and validate configuration from environment variables."""
    db_path = Path(
        os.environ.get("EXPLAINER_DB_PATH", "./data/explainer.sqlite3")
    ).expanduser().resolve()
    host = os.environ.get("EXPLAINER_HOST", "127.0.0.1").strip() or "127.0.0.1"

    try:
        port = int(os.environ.get("EXPLAINER_PORT", "8765").strip())
        max_file_bytes = int(os.environ.get("EXPLAINER_MAX_FILE_BYTES", "512000").strip())
        max_api_content_chars = int(
            os.environ.get("EXPLAINER_MAX_API_CONTENT_CHARS", "100000").strip()
        )
    except ValueError as exc:
        raise ValueError("Numeric environment settings must be valid integers") from exc

    if not 1 <= port <= 65535:
        raise ValueError("EXPLAINER_PORT must be within 1..65535")
    if max_file_bytes < 1024:
        raise ValueError("EXPLAINER_MAX_FILE_BYTES must be >= 1024")
    if max_api_content_chars < 256:
        raise ValueError("EXPLAINER_MAX_API_CONTENT_CHARS must be >= 256")

    origins_raw = os.environ.get(
        "EXPLAINER_ALLOWED_ORIGINS",
        "https://swipswaps.github.io,https://start.fedoraproject.org,http://localhost:8000,http://127.0.0.1:8000",
    )
    origins = tuple(item.strip() for item in origins_raw.split(",") if item.strip())
    if "*" in origins:
        raise ValueError("Wildcard '*' in EXPLAINER_ALLOWED_ORIGINS is disallowed")

    allowed_roots = parse_roots(os.environ.get("EXPLAINER_ALLOWED_ROOTS", ""))
    return ExplainerConfig(
        db_path=db_path,
        allowed_roots=allowed_roots,
        host=host,
        port=port,
        max_file_bytes=max_file_bytes,
        max_api_content_chars=max_api_content_chars,
        allowed_origins=origins,
    )
