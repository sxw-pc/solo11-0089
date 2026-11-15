"""A deliberately bounded Microdata extractor with nested item support."""

from __future__ import annotations

from typing import Any

from lxml import etree

from .common import (
    FieldValue, ParseResult, attribute_value, base_url_for, dedupe_strings,
    find_annotation, serialize_element,
)
from ..issues import RawIssue
from ..utils import collapse_whitespace, dom_path, normalize_type_name


def _nearest_scope(element: etree._Element, root: etree._Element) -> etree._Element | None:
    parent = element.getparent()
    while parent is not None:
        if parent is root:
            return root
        if "itemscope" in parent.attrib:
            return parent
        parent = parent.getparent()
    return None


def _direct_properties(scope: etree._Element) -> list[etree._Element]:
    properties: list[etree._Element] = []
    for element in scope.iterdescendants():
        if "itemprop" not in element.attrib:
            continue
        # A property owned by a nested item belongs to that nested item.
        if _nearest_scope(element, scope) is scope:
            properties.append(element)
    return properties


def _types(element: etree._Element) -> list[str]:
    return [normalize_type_name(item) for item in (element.get("itemtype") or "").split() if item]


def _nested_value(scope: etree._Element, depth: int = 0) -> dict[str, Any]:
    if depth > 8:
        return {}
    value: dict[str, Any] = {}
    types = _types(scope)
    if types:
        value["@type"] = types
    if scope.get("itemid"):
        value["@id"] = scope.get("itemid")
    grouped: dict[str, list[Any]] = {}
    for prop in _direct_properties(scope):
        prop_value = _property_value(prop, depth + 1)
        for name in (prop.get("itemprop") or "").split():
            if name:
                grouped.setdefault(name, []).append(prop_value)
    for name in sorted(grouped):
        values = grouped[name]
        value[name] = values[0] if len(values) == 1 else values
    return value


def _property_value(element: etree._Element, depth: int = 0) -> Any:
    if "itemscope" in element.attrib:
        return _nested_value(element, depth)
    value, _attr = attribute_value(element)
    return value


def _annotation_fields(scope: etree._Element) -> tuple[dict[str, list[FieldValue]], list[str]]:
    fields: dict[str, list[FieldValue]] = {}
    references: list[str] = []
    for element in _direct_properties(scope):
        value = _property_value(element)
        locator = dom_path(element)
        if "itemscope" in element.attrib and element.get("itemid"):
            references.append(element.get("itemid") or "")
        for name in (element.get("itemprop") or "").split():
            if name:
                fields.setdefault(name, []).append(FieldValue(value, locator, "microdata"))
    return fields, references


def parse(tree: etree._Element, doc_path: str, doc_sha256: str) -> ParseResult:
    result = ParseResult(base_url=base_url_for(tree, doc_path))
    scopes = tree.xpath("//*[@itemscope]")
    for scope in scopes:
        types = _types(scope)
        locator = dom_path(scope)
        raw_fragment = serialize_element(scope)
        parent_scope = _nearest_scope(scope, scope)
        is_top_level = parent_scope is None and "itemprop" not in scope.attrib
        if not types:
            if is_top_level:
                result.issues.append(RawIssue(
                    code="MICRODATA_ITEM_WITHOUT_TYPE",
                    severity="warn",
                    doc_path=doc_path,
                    syntax="microdata",
                    locator=locator,
                    evidence_sha256=find_annotation(
                        doc_path=doc_path, doc_sha256=doc_sha256, syntax="microdata",
                        locator=locator, raw_fragment=raw_fragment, types=[],
                        base_url=result.base_url,
                    ).fragment_sha256,
                    message="Top-level Microdata item has no itemtype.",
                ))
            continue
        fields, references = _annotation_fields(scope)
        result.annotations.append(find_annotation(
            doc_path=doc_path,
            doc_sha256=doc_sha256,
            syntax="microdata",
            locator=locator,
            raw_fragment=raw_fragment,
            types=types,
            identifier=collapse_whitespace(scope.get("itemid") or ""),
            fields=fields,
            references=dedupe_strings(references),
            base_url=result.base_url,
        ))
    return result
