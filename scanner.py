"""Bounded project scanner with hashing and symlink rejection."""

from dataclasses import dataclass
import hashlib
import mimetypes
import os
from pathlib import Path
import stat

from config import is_within_roots


@dataclass
class ScannedFile:
    rel_path: str
    sha256: str
    size_bytes: int
    mime_hint: str
    content_text: str | None


def scan_one_file(path, max_bytes):
    path = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"Not a regular file: {path}")

        hasher = hashlib.sha256()
        sample = bytearray()
        total = 0

        with os.fdopen(fd, "rb", closefd=True) as handle:
            fd = -1
            while True:
                block = handle.read(65536)
                if not block:
                    break
                total += len(block)
                hasher.update(block)
                if total <= max_bytes:
                    sample.extend(block)
                elif sample:
                    sample.clear()

        content = None
        if total <= max_bytes and b"\x00" not in sample:
            content = bytes(sample).decode("utf-8", errors="replace")

        return ScannedFile(
            rel_path="",
            sha256=hasher.hexdigest(),
            size_bytes=total,
            mime_hint=mimetypes.guess_type(path.name)[0] or "text/plain",
            content_text=content,
        )
    finally:
        if fd >= 0:
            os.close(fd)


def scan_project_folder(project_root, config):
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    if not config.allowed_roots:
        raise PermissionError("At least one allowed root is required")
    if not is_within_roots(root, config.allowed_roots):
        raise PermissionError(f"Project outside allowed roots: {root}")

    results = []
    for current_root, dirnames, filenames in os.walk(
        root, topdown=True, followlinks=False
    ):
        current = Path(current_root).resolve()
        dirnames[:] = [
            name for name in sorted(dirnames)
            if name not in config.ignored_dirs
            and not (current / name).is_symlink()
            and is_within_roots(current / name, config.allowed_roots)
        ]

        for filename in sorted(filenames):
            path = current / filename
            if path.is_symlink():
                continue
            if path.suffix.lower() not in config.allowed_extensions:
                continue
            if not is_within_roots(path, config.allowed_roots):
                raise PermissionError(f"File outside allowed roots: {path}")

            record = scan_one_file(path, config.max_file_bytes)
            record.rel_path = path.relative_to(root).as_posix()
            results.append(record)

    return results
