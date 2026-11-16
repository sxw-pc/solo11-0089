"""A basic RDFa subset: typeof, about, resource, property, and vocab."""

from __future__ import annotations

from lxml import etree

from .common import (
    FieldValue, ParseResult, attribute_value, base_url_for, dedupe_strings,
    find_annotation, serialize_element,
)
from ..issues import RawIssue
from ..utils import collapse_whitespace, dom_path, normalize_type_name


def _typed_elements(tree: etree._Element) -> list[etree._Element]:
    return [element for element in tree.iter() if (element.get("typeof") or "").strip()]


def _owned_by(element: etree._Element, root: etree._Element) -> bool:
    parent = element.getparent()
    while parent is not None:
        if parent is root:
            return True
        if (parent.get("typeof") or "").strip():
            return False
        parent = parent.getparent()
    return False


def _identifier(element: etree._Element) -> str:
    return collapse_whitespace(
        element.get("about") or element.get("resource") or element.get("id") or ""
    )


def parse(tree: etree._Element, doc_path: str, doc_sha256: str) -> ParseResult:
    result = ParseResult(base_url=base_url_for(tree, doc_path))
    for entity in _typed_elements(tree):
        types = [normalize_type_name(item) for item in (entity.get("typeof") or "").split() if item]
        locator = dom_path(entity)
        raw_fragment = serialize_element(entity)
        if not types:
            result.issues.append(RawIssue(
                code="RDFA_ENTITY_WITHOUT_TYPE",
                severity="warn",
                doc_path=doc_path,
                syntax="rdfa",
                locator=locator,
                evidence_sha256=find_annotation(
                    doc_path=doc_path, doc_sha256=doc_sha256, syntax="rdfa",
                    locator=locator, raw_fragment=raw_fragment, types=[],
                    base_url=result.base_url,
                ).fragment_sha256,
                message="RDFa entity has no usable typeof value.",
            ))
            continue
        fields: dict[str, list[FieldValue]] = {}
        references: list[str] = []
        for element in entity.iterdescendants():
            properties = (element.get("property") or "").split()
            if not properties or not _owned_by(element, entity):
                continue
            value, attr = attribute_value(element)
            for name in properties:
                fields.setdefault(name, []).append(
                    FieldValue(value, dom_path(element), f"rdfa:{attr}")
                )
            if attr == "resource" and value:
                references.append(str(value))
        result.annotations.append(find_annotation(
            doc_path=doc_path,
            doc_sha256=doc_sha256,
            syntax="rdfa",
            locator=locator,
            raw_fragment=raw_fragment,
            types=types,
            identifier=_identifier(entity),
            fields=fields,
            references=dedupe_strings(references),
            base_url=result.base_url,
        ))
    return result
