"""Identifier duplication and entity-reference graph audits."""

from __future__ import annotations

from typing import Any

from .issues import RawIssue
from .normalize import normalize_identifier
from .parsers.common import Annotation
from .utils import normalize_type_name


def duplicate_id_issues(annotations: list[Annotation]) -> list[RawIssue]:
    seen: dict[tuple[str, str, str], list[Annotation]] = {}
    for annotation in annotations:
        identifier = normalize_identifier(annotation.identifier, annotation.base_url)
        if not identifier:
            continue
        seen.setdefault((annotation.doc_path, annotation.syntax, identifier), []).append(annotation)
    issues: list[RawIssue] = []
    for (_doc, _syntax, identifier), sources in sorted(seen.items()):
        if len(sources) < 2:
            continue
        second = sources[1]
        issues.append(RawIssue(
            code="ENTITY_DUPLICATE_ID",
            severity="warn",
            doc_path=second.doc_path,
            syntax=second.syntax,
            locator=second.locator,
            entity_type=normalize_type_name(second.types[0]),
            evidence_sha256=second.fragment_sha256,
            message="The same document and syntax repeat an @id.",
            details={"identifier": identifier,
                     "locators": sorted(source.locator for source in sources)},
        ))
    return issues


def _find_cycles(edges: dict[str, set[str]]) -> list[tuple[str, ...]]:
    color = {node: 0 for node in edges}
    stack: list[str] = []
    position: dict[str, int] = {}
    cycles: set[tuple[str, ...]] = set()

    def visit(node: str) -> None:
        color[node] = 1
        position[node] = len(stack)
        stack.append(node)
        for target in sorted(edges.get(node, ())):
            if color[target] == 0:
                visit(target)
            elif color[target] == 1:
                cycle = stack[position[target]:]
                if cycle:
                    rotations = [tuple(cycle[i:] + cycle[:i]) for i in range(len(cycle))]
                    cycles.add(min(rotations))
        stack.pop()
        position.pop(node, None)
        color[node] = 2

    for node in sorted(edges):
        if color[node] == 0:
            visit(node)
    return sorted(cycles)


def reference_issues(
    entities: list[Any],
    identifier_to_entity: dict[str, str],
) -> list[RawIssue]:
    issues: list[RawIssue] = []
    edges: dict[str, set[str]] = {entity.entity_id: set() for entity in entities}
    by_id = {entity.entity_id: entity for entity in entities}
    for entity in entities:
        for reference in entity.references:
            target = identifier_to_entity.get(reference)
            if not target:
                source = entity.sources[0]
                issues.append(RawIssue(
                    code="DANGLING_REFERENCE",
                    severity="warn",
                    doc_path=source.doc_path,
                    syntax=source.syntax,
                    locator=source.locator,
                    entity_type=entity.primary_type,
                    evidence_sha256=source.fragment_sha256,
                    message="Entity reference has no matching normalized @id.",
                    details={"entity_id": entity.entity_id, "reference": reference},
                ))
                continue
            if target != entity.entity_id:
                edges[entity.entity_id].add(target)
    for cycle in _find_cycles(edges):
        source = by_id[cycle[0]].sources[0]
        issues.append(RawIssue(
            code="REFERENCE_CYCLE",
            severity="warn",
            doc_path=source.doc_path,
            syntax=source.syntax,
            locator=source.locator,
            entity_type=by_id[cycle[0]].primary_type,
            evidence_sha256=source.fragment_sha256,
            message="Entity references form a cycle.",
            details={"cycle": list(cycle)},
        ))
    return issues
