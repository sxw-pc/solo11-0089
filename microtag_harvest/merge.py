"""Deterministic entity grouping, conflict detection, and reference auditing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .audit import duplicate_id_issues, reference_issues
from .contracts import Contract
from .issues import RawIssue
from .normalize import normalize_identifier, normalize_value, normalized_name
from .parsers.common import Annotation
from .utils import canonical_json, sha1_short


@dataclass(frozen=True)
class MergedValue:
    value: Any
    raw: Any
    doc_path: str
    syntax: str
    locator: str
    fragment_sha256: str
    annotation_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "raw": self.raw,
            "document": self.doc_path,
            "syntax": self.syntax,
            "locator": self.locator,
            "fragment_sha256": self.fragment_sha256,
            "annotation_id": self.annotation_id,
        }


@dataclass
class MergedEntity:
    entity_id: str
    types: tuple[str, ...]
    primary_type: str
    key: str
    identifier: str
    fields: dict[str, list[MergedValue]]
    sources: tuple[Annotation, ...]
    conflicts: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    # Populated by validation.verify_entities after merge grouping is known.
    # Holds an EntityVerification instance; typed loosely to avoid an import cycle.
    verification: Any = None

    def source_records(self) -> list[dict[str, Any]]:
        rows = [
            {
                "annotation_id": source.annotation_id,
                "document": source.doc_path,
                "syntax": source.syntax,
                "locator": source.locator,
                "fragment_sha256": source.fragment_sha256,
            }
            for source in self.sources
        ]
        return sorted(rows, key=lambda item: (
            item["document"], item["syntax"], item["locator"], item["annotation_id"]
        ))

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "types": list(self.types),
            "primary_type": self.primary_type,
            "key": self.key,
            "identifier": self.identifier,
            "sources": self.source_records(),
            "fields": {
                name: [value.to_dict() for value in values]
                for name, values in sorted(self.fields.items())
            },
            "conflicts": list(self.conflicts),
            "verification": self.verification.to_dict() if self.verification else None,
        }


@dataclass
class _Group:
    identifier: str
    name: str
    doc_path: str
    syntax: str
    locator: str
    fragment_sha256: str
    annotations: list[Annotation] = field(default_factory=list)
    normalized_fields: dict[str, list[MergedValue]] = field(default_factory=dict)
    normalized_refs: list[str] = field(default_factory=list)


def _field_format(annotation: Annotation, name: str, contract: Contract) -> str | None:
    for type_name in annotation.types:
        spec = contract.types.get(type_name)
        if spec and name in spec.fields:
            return spec.fields[name].format or spec.fields[name].type
    return None


def _group_sort_key(group: _Group) -> tuple[str, str, str]:
    return (group.doc_path, group.identifier, group.name)


def _new_group(annotation: Annotation, identifier: str, name: str) -> _Group:
    return _Group(
        identifier=identifier,
        name=name,
        doc_path=annotation.doc_path,
        syntax=annotation.syntax,
        locator=annotation.locator,
        fragment_sha256=annotation.fragment_sha256,
        annotations=[annotation],
    )


def _merge_into_group(group: _Group, annotation: Annotation, contract: Contract) -> None:
    group.annotations.append(annotation)
    for name, occurrences in annotation.fields.items():
        field_format = _field_format(annotation, name, contract)
        for occurrence in occurrences:
            normalized = normalize_value(
                occurrence.value, name, annotation.base_url, field_format
            )
            group.normalized_fields.setdefault(name, []).append(MergedValue(
                value=normalized,
                raw=occurrence.value,
                doc_path=annotation.doc_path,
                syntax=annotation.syntax,
                locator=occurrence.locator or annotation.locator,
                fragment_sha256=annotation.fragment_sha256,
                annotation_id=annotation.annotation_id,
            ))
    for reference in annotation.references:
        normalized_ref = normalize_identifier(reference, annotation.base_url)
        if normalized_ref:
            group.normalized_refs.append(normalized_ref)


def _make_entity(group: _Group, contract: Contract) -> MergedEntity:
    types = tuple(sorted({t for source in group.annotations for t in source.types}))
    primary_type = types[0] if types else "Thing"
    if group.identifier:
        key = f"@id:{group.identifier}"
    elif group.name:
        key = f"name:{group.doc_path}:{group.name}"
    else:
        key = f"loc:{group.doc_path}:{group.syntax}:{group.locator}:{group.fragment_sha256}"
    entity_id = sha1_short(f"{primary_type}|{key}")
    sources = tuple(sorted(
        {source.annotation_id: source for source in group.annotations}.values(),
        key=lambda item: (item.doc_path, item.syntax, item.locator, item.annotation_id),
    ))
    conflicts = tuple(sorted(_find_conflicts(group, sources, contract)))
    return MergedEntity(
        entity_id=entity_id,
        types=types,
        primary_type=primary_type,
        key=key,
        identifier=group.identifier,
        fields={name: list(values) for name, values in group.normalized_fields.items()},
        sources=sources,
        conflicts=conflicts,
        references=tuple(sorted(set(group.normalized_refs))),
    )


def _expected_array(field_name: str, sources: Iterable[Annotation], contract: Contract) -> bool:
    for source in sources:
        for type_name in source.types:
            spec = contract.types.get(type_name)
            if spec and field_name in spec.fields and spec.fields[field_name].type == "array":
                return True
    return False


def _find_conflicts(
    group: _Group, sources: tuple[Annotation, ...], contract: Contract
) -> set[str]:
    conflicts: set[str] = set()
    for field_name, values in group.normalized_fields.items():
        if _expected_array(field_name, sources, contract):
            continue
        if any(isinstance(item.value, list) for item in values):
            continue
        distinct = {canonical_json(item.value) for item in values}
        if len(distinct) > 1:
            conflicts.add(field_name)
    return conflicts


def _conflict_issues(entity: MergedEntity, contract: Contract) -> list[RawIssue]:
    issues: list[RawIssue] = []
    for field_name in entity.conflicts:
        values = entity.fields.get(field_name, [])
        if _expected_array(field_name, entity.sources, contract):
            continue
        if any(isinstance(item.value, list) for item in values):
            continue
        first = values[0]
        issues.append(RawIssue(
            code="FIELD_VALUE_CONFLICT",
            severity="warn",
            doc_path=first.doc_path,
            syntax=first.syntax,
            locator=first.locator,
            entity_type=entity.primary_type,
            field_name=field_name,
            evidence_sha256=first.fragment_sha256,
            message=f"Merged sources disagree on {entity.primary_type}.{field_name}.",
            details={
                "entity_id": entity.entity_id,
                "values": [
                    {"value": item.value, "document": item.doc_path,
                     "syntax": item.syntax, "annotation_id": item.annotation_id}
                    for item in values
                ],
            },
        ))
    syntaxes = {source.syntax for source in entity.sources}
    if entity.conflicts and len(syntaxes) > 1:
        first = entity.sources[0]
        issues.append(RawIssue(
            code="MULTI_SYNTAX_CONFLICT",
            severity="warn",
            doc_path=first.doc_path,
            syntax=first.syntax,
            locator=first.locator,
            entity_type=entity.primary_type,
            evidence_sha256=first.fragment_sha256,
            message="Multiple syntaxes describe the entity with conflicting scalar values.",
            details={"entity_id": entity.entity_id, "fields": list(entity.conflicts),
                     "syntaxes": sorted(syntaxes)},
        ))
    return issues


def merge_annotations(
    annotations: Iterable[Annotation],
    contract: Contract,
) -> tuple[list[MergedEntity], list[RawIssue]]:
    annotation_list = list(annotations)
    groups: dict[tuple, _Group] = {}
    for annotation in sorted(annotation_list, key=lambda item: (
        item.doc_path, item.syntax, item.locator, item.annotation_id
    )):
        identifier = normalize_identifier(annotation.identifier, annotation.base_url)
        raw_fields = {name: [item.value for item in values]
                      for name, values in annotation.fields.items()}
        document_named = normalized_name(annotation.types, raw_fields)
        type_key = "|".join(sorted(annotation.types))
        if identifier:
            group_key = ("id", identifier)
        elif document_named:
            group_key = ("name", annotation.doc_path, type_key, document_named)
        else:
            group_key = (
                "loc", annotation.doc_path, type_key, annotation.syntax,
                annotation.locator, annotation.fragment_sha256,
            )
        group = groups.get(group_key)
        if group is None:
            group = _new_group(annotation, identifier, document_named)
            groups[group_key] = group
        _merge_into_group(group, annotation, contract)

    entities = [_make_entity(group, contract) for group in groups.values()]
    entities.sort(key=lambda item: item.entity_id)
    identifier_map = {
        entity.identifier: entity.entity_id
        for entity in entities if entity.identifier
    }
    issues = duplicate_id_issues(annotation_list)
    for entity in entities:
        issues.extend(_conflict_issues(entity, contract))
    issues.extend(reference_issues(entities, identifier_map))
    return entities, issues
