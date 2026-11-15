"""Small deterministic helpers shared across the application."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from lxml import etree


_WS_RE = re.compile(r"\s+", re.UNICODE)


def collapse_whitespace(value: str) -> str:
    """Collapse every run of Unicode whitespace to one ASCII space."""
    return _WS_RE.sub(" ", value or "").strip()


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def sha1_short(value: str, length: int = 12) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:length]


def canonical_json(value: Any) -> str:
    """Compact, sorted JSON used whenever a hash must be reproducible."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def cap_text(value: str, limit: int = 1600) -> str:
    """Keep stored evidence small; the full fragment is represented by its hash."""
    value = value or ""
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...[truncated]"


def dom_path(element: etree._Element) -> str:
    """Return a stable tag/nth-child path for an lxml element."""
    parts: list[str] = []
    node: etree._Element | None = element
    while node is not None and isinstance(node.tag, str):
        parent = node.getparent()
        if parent is None:
            parts.append(node.tag.lower())
        else:
            same_tag = [c for c in parent if isinstance(c.tag, str)
                        and c.tag.lower() == node.tag.lower()]
            position = same_tag.index(node) + 1
            parts.append(f"{node.tag.lower()}[{position}]")
        node = parent
    return "/" + "/".join(reversed(parts))


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def normalize_type_name(value: Any) -> str:
    """Reduce schema URLs and prefixed names to a stable local type name."""
    text = collapse_whitespace(str(value))
    if not text:
        return ""
    text = text.rstrip("/")
    text = text.rsplit("/", 1)[-1]
    text = text.rsplit(":", 1)[-1]
    return text


def sorted_paths(paths: list[str]) -> list[str]:
    return sorted(paths, key=lambda item: item.replace("\\", "/").casefold())
