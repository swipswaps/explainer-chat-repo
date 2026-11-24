"""Chat transcript parser rejecting oversized files rather than truncating."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, List, Optional
from config import ExplainerConfig, is_within_roots

VALID_ROLES = frozenset({"system", "user", "assistant", "tool"})
ROLE_LINE = re.compile(r"^\s*\[(system|user|assistant|tool)\]\s*(.*)$", re.IGNORECASE)


@dataclass
class ParsedMessage:
    """Normalized chat message entry."""

    seq_index: int
    role: str
    timestamp_utc: Optional[str]
    content_text: str
    content_hash: str


@dataclass
class ParsedChatSession:
    """Parsed chat transcript with derived session key and title."""

    source_path: str
    session_key: str
    title: str
    messages: List[ParsedMessage]


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_role(value: Any) -> str:
    if not isinstance(value, str):
        return "user"
    lowered = value.strip().lower()
    if lowered in VALID_ROLES:
        return lowered
    aliases = {
        "human": "user",
        "prompt": "user",
        "ai": "assistant",
        "model": "assistant",
        "bot": "assistant",
        "function": "tool",
        "observation": "tool",
    }
    return aliases.get(lowered, "user")


def _extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return "" if value is None else str(value)


def _ensure_within_size(path: Path, max_bytes: int) -> None:
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"Chat export size ({size} bytes) exceeds limit ({max_bytes}): {path}")


def _parse_jsonl(path: Path) -> List[ParsedMessage]:
    items: List[ParsedMessage] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                obj = {"role": "tool", "content": stripped}
            if not isinstance(obj, dict):
                continue
            role = obj.get("role") or obj.get("sender") or obj.get("author")
            ts = obj.get("timestamp_utc") or obj.get("timestamp") or obj.get("created_at")
            raw_content = obj.get("content", obj.get("message", obj.get("text")))
            text = _extract_text(raw_content)
            if text.strip():
                items.append(
                    ParsedMessage(
                        seq_index=len(items),
                        role=_normalize_role(role),
                        timestamp_utc=str(ts) if ts is not None else None,
                        content_text=text,
                        content_hash=_digest_text(text),
                    )
                )
    return items


def _parse_json_doc(path: Path) -> List[ParsedMessage]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        raw_items = payload
    elif isinstance(payload, dict):
        raw_items = payload.get("messages", [])
    else:
        raise ValueError("Unsupported JSON chat transcript format")
    if not isinstance(raw_items, list):
        raise ValueError("Chat JSON messages field must be a list")

    items: List[ParsedMessage] = []
    for entry in raw_items:
        if not isinstance(entry, dict):
            continue
        role = entry.get("role", entry.get("sender"))
        ts = entry.get("timestamp_utc", entry.get("timestamp"))
        text = _extract_text(entry.get("content", entry.get("text")))
        if text.strip():
            items.append(
                ParsedMessage(
                    seq_index=len(items),
                    role=_normalize_role(role),
                    timestamp_utc=str(ts) if ts is not None else None,
                    content_text=text,
                    content_hash=_digest_text(text),
                )
            )
    return items


def _parse_text_doc(path: Path) -> List[ParsedMessage]:
    items: List[ParsedMessage] = []
    role = "user"
    buffer: List[str] = []

    def flush_block() -> None:
        nonlocal buffer
        joined = "\n".join(buffer).strip()
        if joined:
            items.append(
                ParsedMessage(
                    seq_index=len(items),
                    role=role,
                    timestamp_utc=None,
                    content_text=joined,
                    content_hash=_digest_text(joined),
                )
            )
        buffer = []

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = ROLE_LINE.match(line)
            if match:
                flush_block()
                role = _normalize_role(match.group(1))
                if match.group(2):
                    buffer.append(match.group(2))
            else:
                buffer.append(line.rstrip("\n"))
    flush_block()
    return items


def parse_chat_log(log_path: Path, config: ExplainerConfig) -> ParsedChatSession:
    """Validate path and size limits, then parse JSONL, JSON, or text chat logs."""
    resolved = log_path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Chat log not found: {resolved}")
    if not config.allowed_roots or not is_within_roots(resolved, config.allowed_roots):
        raise PermissionError(f"Chat log outside allowed roots: {resolved}")
    _ensure_within_size(resolved, config.max_file_bytes)

    suffix = resolved.suffix.lower()
    if suffix == ".jsonl":
        messages = _parse_jsonl(resolved)
    elif suffix == ".json":
        messages = _parse_json_doc(resolved)
    else:
        messages = _parse_text_doc(resolved)

    session_key = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:16]
    return ParsedChatSession(
        source_path=str(resolved),
        session_key=session_key,
        title=resolved.stem,
        messages=messages,
    )
