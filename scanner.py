"""Safe filesystem scanner rejecting symlinks, NUL bytes, and oversized files."""
from dataclasses import dataclass
import hashlib
import mimetypes
import os
from pathlib import Path
from typing import List
from config import ExplainerConfig, is_within_roots


@dataclass
class ScannedFile:
    """Metadata and decoded UTF-8 payload for an indexed project file."""

    rel_path: str
    sha256: str
    size_bytes: int
    mime_hint: str
    content_text: str


def scan_one_file(file_path: Path, max_bytes: int) -> ScannedFile:
    """Read a regular non-symlink file safely using O_NOFOLLOW."""
    resolved = file_path.resolve()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(resolved), flags)
    try:
        st = os.fstat(fd)
        if st.st_size > max_bytes:
            raise ValueError(f"File exceeds max_file_bytes ({max_bytes}): {resolved}")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            raw = handle.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError(f"File exceeded max_file_bytes during read: {resolved}")
        if b"\x00" in raw:
            raise ValueError(f"Binary NUL byte encountered in text scan: {resolved}")
        digest = hashlib.sha256(raw).hexdigest()
        mime_type, _ = mimetypes.guess_type(str(resolved))
        return ScannedFile(
            rel_path="",
            sha256=digest,
            size_bytes=len(raw),
            mime_hint=mime_type or "text/plain",
            content_text=raw.decode("utf-8", errors="replace"),
        )
    finally:
        if fd >= 0:
            os.close(fd)


def scan_project_folder(project_root: Path, config: ExplainerConfig) -> List[ScannedFile]:
    """Walk a project directory without following symlinks and collect eligible files."""
    root = project_root.resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Project root is not a directory: {root}")
    if not config.allowed_roots or not is_within_roots(root, config.allowed_roots):
        raise PermissionError(f"Project root outside allowed roots: {root}")

    collected: List[ScannedFile] = []
    for current_str, dirnames, filenames in os.walk(str(root), topdown=True, followlinks=False):
        current = Path(current_str)
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in config.ignored_dirs and not (current / d).is_symlink()
        )
        for filename in sorted(filenames):
            candidate = current / filename
            if candidate.is_symlink():
                continue
            if candidate.suffix.lower() not in config.allowed_extensions:
                continue
            rel = candidate.resolve().relative_to(root).as_posix()
            record = scan_one_file(candidate, config.max_file_bytes)
            record.rel_path = rel
            collected.append(record)
    return collected
