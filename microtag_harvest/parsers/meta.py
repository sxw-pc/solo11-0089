"""OpenGraph and Twitter Card metadata extraction."""

from __future__ import annotations

from lxml import etree

from .common import (
    FieldValue, ParseResult, base_url_for, find_annotation, serialize_element,
)
from ..utils import collapse_whitespace, dom_path


def _meta_key(element: etree._Element) -> tuple[str, str]:
    prop = collapse_whitespace(element.get("property") or "").lower()
    name = collapse_whitespace(element.get("name") or "").lower()
    if prop.startswith("og:"):
        return prop, "property"
    if name.startswith("twitter:"):
        return name, "name"
    return "", ""


def parse(tree: etree._Element, doc_path: str, doc_sha256: str) -> ParseResult:
    result = ParseResult(base_url=base_url_for(tree, doc_path))
    fields: dict[str, list[FieldValue]] = {}
    raw_tags: list[str] = []
    for element in tree.xpath("//meta"):
        key, attr_name = _meta_key(element)
        if not key or "content" not in element.attrib:
            continue
        content = element.get("content") or ""
        fields.setdefault(key, []).append(FieldValue(content, dom_path(element), attr_name))
        raw_tags.append(serialize_element(element))
    if not fields:
        return result
    raw_fragment = "\n".join(raw_tags)
    result.annotations.append(find_annotation(
        doc_path=doc_path,
        doc_sha256=doc_sha256,
        syntax="meta",
        locator="meta[opengraph,twitter-card]",
        raw_fragment=raw_fragment,
        types=["Metadata"],
        fields=fields,
        base_url=result.base_url,
    ))
    return result
