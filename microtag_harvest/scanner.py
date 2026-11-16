"""HTML archive discovery, hashing, and duplicate marking."""

from __future__ import annotations

import fnmatch
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .config import DEFAULT_IGNORES


@dataclass
class Document:
    path: str
    sha256: str
    size: int
    parse_status: str = "pending"
    duplicate_of: str | None = None
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
            "parse_status": self.parse_status,
            "duplicate_of": self.duplicate_of,
            "error": self.error,
        }


def _ignored(relative: Path, patterns: Iterable[str]) -> bool:
    parts = relative.parts
    name = relative.name
    for pattern in patterns:
        if not pattern:
            continue
        if any(fnmatch.fnmatch(part, pattern) for part in parts):
            return True
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(relative.as_posix(), pattern):
            return True
    return False


def discover(input_dir: str | Path, ignores: Iterable[str] = DEFAULT_IGNORES) -> list[Document]:
    root = Path(input_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"input directory does not exist: {root}")
    documents: list[Document] = []
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        for filename in sorted(filenames):
            if not filename.lower().endswith((".html", ".htm")):
                continue
            absolute = Path(current) / filename
            relative = absolute.relative_to(root)
            if _ignored(relative, ignores):
                continue
            data = absolute.read_bytes()
            documents.append(Document(
                path=relative.as_posix(),
                sha256=hashlib.sha256(data).hexdigest(),
                size=len(data),
            ))
    documents.sort(key=lambda doc: doc.path.casefold())
    first_by_hash: dict[str, str] = {}
    for document in documents:
        first = first_by_hash.get(document.sha256)
        if first is None:
            first_by_hash[document.sha256] = document.path
        else:
            document.duplicate_of = first
    return documents


def read_document(input_dir: str | Path, document: Document) -> bytes:
    return (Path(input_dir) / document.path).read_bytes()
