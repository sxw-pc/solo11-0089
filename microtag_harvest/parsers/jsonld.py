"""JSON-LD extraction for arrays, @graph containers, and nested objects."""

from __future__ import annotations

import json
from typing import Any, Iterable

from lxml import etree

from .common import (
    FieldValue, ParseResult, base_url_for, collect_object_references,
    find_annotation,
)
from ..issues import RawIssue


def _is_jsonld_script(element: etree._Element) -> bool:
    value = (element.get("type") or "").lower()
    return "ld+json" in value


def _remote_contexts(context: Any) -> list[str]:
    found: list[str] = []
    if isinstance(context, str):
        if context.startswith(("http://", "https://")):
            found.append(context)
    elif isinstance(context, list):
        for item in context:
            found.extend(_remote_contexts(item))
    elif isinstance(context, dict):
        imported = context.get("@import")
        if isinstance(imported, str):
            found.extend(_remote_contexts(imported))
    return found


def _walk_objects(payload: Any, prefix: str = "") -> Iterable[tuple[dict[str, Any], str]]:
    if isinstance(payload, list):
        for index, item in enumerate(payload):
            yield from _walk_objects(item, f"{prefix}[{index}]")
        return
    if not isinstance(payload, dict):
        return
    graph = payload.get("@graph")
    if isinstance(graph, list):
        for index, item in enumerate(graph):
            yield from _walk_objects(item, f"{prefix}/graph[{index}]")
    if payload.get("@type") is not None:
        yield payload, prefix or "/entity[0]"
    elif graph is None and any(key not in {"@context"} for key in payload):
        # Surface a type-less object as an extractor warning rather than dropping it silently.
        yield payload, prefix or "/entity[0]"


def parse(tree: etree._Element, doc_path: str, doc_sha256: str) -> ParseResult:
    result = ParseResult(base_url=base_url_for(tree, doc_path))
    scripts = [element for element in tree.xpath("//script") if _is_jsonld_script(element)]
    for script_index, script in enumerate(scripts):
        locator = f"script[type=application/ld+json]#{script_index}"
        raw = script.text or "".join(script.itertext())
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeError) as exc:
            result.issues.append(RawIssue(
                code="PARSE_JSONLD_INVALID",
                severity="error",
                doc_path=doc_path,
                syntax="json-ld",
                locator=locator,
                evidence_sha256=find_annotation(
                    doc_path=doc_path, doc_sha256=doc_sha256, syntax="json-ld",
                    locator=locator, raw_fragment=raw, types=[], base_url=result.base_url,
                ).fragment_sha256,
                message=f"Invalid JSON-LD: {exc.msg if hasattr(exc, 'msg') else type(exc).__name__}",
                details={"line": getattr(exc, "lineno", None),
                         "column": getattr(exc, "colno", None)},
            ))
            continue

        context = payload.get("@context") if isinstance(payload, dict) else None
        if context is None and isinstance(payload, list):
            context = [item.get("@context") for item in payload if isinstance(item, dict)]
        remote = [value for value in _remote_contexts(context) if value]
        if remote:
            result.issues.append(RawIssue(
                code="REMOTE_CONTEXT_NOT_LOADED",
                severity="warn",
                doc_path=doc_path,
                syntax="json-ld",
                locator=locator,
                evidence_sha256=find_annotation(
                    doc_path=doc_path, doc_sha256=doc_sha256, syntax="json-ld",
                    locator=locator, raw_fragment=raw, types=[], base_url=result.base_url,
                ).fragment_sha256,
                message="Remote JSON-LD contexts are recorded but never fetched.",
                details={"contexts": sorted(set(remote))},
            ))

        for obj, suffix in _walk_objects(payload):
            object_locator = locator + suffix
            types = obj.get("@type")
            type_values = types if isinstance(types, list) else [types]
            clean_types = [str(item) for item in type_values if item]
            if not clean_types:
                result.issues.append(RawIssue(
                    code="JSONLD_ENTITY_WITHOUT_TYPE",
                    severity="warn",
                    doc_path=doc_path,
                    syntax="json-ld",
                    locator=object_locator,
                    evidence_sha256=find_annotation(
                        doc_path=doc_path, doc_sha256=doc_sha256, syntax="json-ld",
                        locator=object_locator, raw_fragment=raw, types=[],
                        base_url=result.base_url,
                    ).fragment_sha256,
                    message="JSON-LD object has no @type and was not turned into a candidate.",
                ))
                continue
            fields: dict[str, list[FieldValue]] = {}
            for key in sorted(obj):
                if key in {"@context", "@type", "@graph", "@id"}:
                    continue
                field_locator = f"{object_locator}#{key}"
                fields[key] = [FieldValue(obj[key], field_locator, "@value")]
            result.annotations.append(find_annotation(
                doc_path=doc_path,
                doc_sha256=doc_sha256,
                syntax="json-ld",
                locator=object_locator,
                raw_fragment=raw,
                types=clean_types,
                identifier=str(obj.get("@id") or ""),
                fields=fields,
                references=collect_object_references(obj),
                base_url=result.base_url,
            ))
    return result
