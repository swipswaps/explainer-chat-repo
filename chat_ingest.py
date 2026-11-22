"""Normalize JSONL, JSON and role-labelled plain-text chat exports."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from config import is_within_roots

VALID_ROLES = {"system", "user", "assistant", "tool"}
ROLE_LINE = re.compile(
    r"^\s*\[(system|user|assistant|tool)\]\s*(.*)$",
    re.IGNORECASE,
)


@dataclass
class ParsedMessage:
    seq_index: int
    role: str
    timestamp_utc: str | None
    content_text: str
    content_hash: str


@dataclass
class ParsedChatSession:
    source_path: str
    session_key: str
    title: str
    messages: list


def normalize_role(value):
    if not isinstance(value, str):
        return "user"
    role = value.strip().lower()
    aliases = {
        "human": "user", "prompt": "user",
        "ai": "assistant", "model": "assistant", "bot": "assistant",
        "function": "tool", "observation": "tool",
    }
    role = aliases.get(role, role)
    return role if role in VALID_ROLES else "user"


def extract_text(value):
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


def make_message(index, role, timestamp, content):
    text = extract_text(content)
    if not text.strip():
        return None
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return ParsedMessage(
        seq_index=index,
        role=normalize_role(role),
        timestamp_utc=str(timestamp) if timestamp is not None else None,
        content_text=text,
        content_hash=digest,
    )


def parse_jsonl(path):
    output = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                obj = {"role": "tool", "content": line}
            if not isinstance(obj, dict):
                continue
            message = make_message(
                len(output),
                obj.get("role") or obj.get("sender") or obj.get("author"),
                obj.get("timestamp_utc") or obj.get("timestamp")
                    or obj.get("created_at"),
                obj.get("content", obj.get("message", obj.get("text"))),
            )
            if message:
                output.append(message)
    return output


def parse_json_document(path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("messages", [])
    else:
        raise ValueError("Unsupported JSON transcript structure")
    if not isinstance(items, list):
        raise ValueError("JSON messages must be an array")

    output = []
    for item in items:
        if not isinstance(item, dict):
            continue
        message = make_message(
            len(output),
            item.get("role", item.get("sender")),
            item.get("timestamp_utc", item.get("timestamp")),
            item.get("content", item.get("text")),
        )
        if message:
            output.append(message)
    return output


def parse_text_document(path):
    output = []
    role = "user"
    buffer = []

    def flush():
        nonlocal buffer
        content = "\n".join(buffer).strip()
        if content:
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            output.append(ParsedMessage(
                seq_index=len(output),
                role=role,
                timestamp_utc=None,
                content_text=content,
                content_hash=digest,
            ))
        buffer = []

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = ROLE_LINE.match(line)
            if match:
                flush()
                role = normalize_role(match.group(1))
                if match.group(2):
                    buffer.append(match.group(2))
            else:
                buffer.append(line.rstrip("\n"))
    flush()
    return output


def parse_chat_log(log_path, config):
    path = Path(log_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(str(path))
    if not config.allowed_roots:
        raise PermissionError("No allowed roots configured")
    if not is_within_roots(path, config.allowed_roots):
        raise PermissionError(f"Chat outside allowed roots: {path}")
    if path.stat().st_size > config.max_file_bytes:
        raise ValueError(
            f"Chat export exceeds {config.max_file_bytes} bytes: {path}"
        )

    if path.suffix.lower() == ".jsonl":
        messages = parse_jsonl(path)
    elif path.suffix.lower() == ".json":
        messages = parse_json_document(path)
    else:
        messages = parse_text_document(path)

    key = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
    return ParsedChatSession(
        source_path=str(path),
        session_key=key,
        title=path.stem,
        messages=messages,
    )
