"""Contract field validation kept separate from YAML parsing."""

from __future__ import annotations

import re
from typing import Any, Iterable

from .contracts import Contract, FieldSpec
from .issues import ISSUE_REGISTRY, RawIssue
from .normalize import is_iso_date, normalize_date, normalize_text, normalize_url
from .parsers.common import Annotation, field_locator
from .utils import canonical_json, collapse_whitespace

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _check_type(value: Any, field_type: str) -> bool:
    if field_type == "string":
        return isinstance(value, str)
    if field_type in {"url", "date", "identifier"}:
        return isinstance(value, str)
    if field_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if field_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if field_type == "boolean":
        return isinstance(value, bool)
    if field_type == "array":
        return isinstance(value, list)
    if field_type == "object":
        return isinstance(value, dict)
    return False


def _check_format(value: Any, spec: FieldSpec, base_url: str) -> bool:
    if not isinstance(value, str):
        return True
    text = normalize_text(value)
    if spec.format in {"uri", "url"}:
        normalized = normalize_url(text, base_url)
        return normalized.startswith(("http://", "https://", "mailto:")) and "://" in normalized
    if spec.format == "iso8601":
        return bool(text) and is_iso_date(normalize_date(text))
    if spec.format == "email":
        return bool(_EMAIL_RE.match(text))
    if spec.format == "nonempty":
        return bool(collapse_whitespace(text))
    if spec.type == "date":
        return bool(text) and is_iso_date(normalize_date(text))
    if spec.type == "url":
        normalized = normalize_url(text, base_url)
        return normalized.startswith(("http://", "https://", "mailto:"))
    return True


def _issue(code: str, annotation: Annotation, severities: dict[str, str], **kwargs: Any) -> RawIssue:
    severity = kwargs.pop("severity", None)
    if severity is None:
        severity = severities.get(code) or ISSUE_REGISTRY.get(code, ("error", ""))[0]
    return RawIssue(
        code=code,
        severity=severity,
        doc_path=annotation.doc_path,
        syntax=annotation.syntax,
        locator=kwargs.pop("locator", annotation.locator),
        entity_type=kwargs.pop("entity_type", ""),
        field_name=kwargs.pop("field_name", ""),
        evidence_sha256=annotation.fragment_sha256,
        **kwargs,
    )


def _policy_severity(policy: str) -> str | None:
    return {"error": "error", "warn": "warn", "ignore": None}.get(policy)


def validate_annotations(annotations: Iterable[Annotation], contract: Contract) -> list[RawIssue]:
    issues: list[RawIssue] = []
    for annotation in annotations:
        for type_name in annotation.types:
            spec = contract.types.get(type_name)
            if spec is None:
                severity = _policy_severity(contract.unknown_type)
                if severity:
                    issues.append(_issue(
                        "CONTRACT_UNKNOWN_TYPE", annotation, {},
                        severity=severity, entity_type=type_name,
                        locator=annotation.locator,
                        message=f"Type {type_name!r} is not declared by contract {contract.name!r}.",
                        details={"contract": contract.name},
                    ))
                continue
            for field_name in spec.required:
                values = [item.value for item in annotation.fields.get(field_name, [])]
                if not values or all(_is_empty(value) for value in values):
                    issues.append(_issue(
                        "CONTRACT_MISSING_REQUIRED", annotation, {},
                        entity_type=type_name, field_name=field_name,
                        locator=field_locator(annotation, field_name),
                        message=f"{type_name}.{field_name} is required.",
                        details={"contract": contract.name},
                    ))
            for field_name in spec.recommended:
                values = [item.value for item in annotation.fields.get(field_name, [])]
                if not values or all(_is_empty(value) for value in values):
                    issues.append(_issue(
                        "CONTRACT_RECOMMENDED_MISSING", annotation, {},
                        entity_type=type_name, field_name=field_name,
                        locator=field_locator(annotation, field_name),
                        message=f"{type_name}.{field_name} is recommended.",
                        details={"contract": contract.name},
                    ))
            unknown_policy = spec.unknown_fields or contract.unknown_field
            for field_name, values in sorted(annotation.fields.items()):
                if field_name not in spec.fields:
                    severity = _policy_severity(unknown_policy)
                    if severity:
                        issues.append(_issue(
                            "CONTRACT_UNKNOWN_FIELD", annotation, {},
                            severity=severity, entity_type=type_name,
                            field_name=field_name,
                            locator=field_locator(annotation, field_name),
                            message=f"{type_name}.{field_name} is not declared by the contract.",
                            details={"policy": unknown_policy},
                        ))
                    continue
                field_spec = spec.fields[field_name]
                for occurrence in values:
                    value = occurrence.value
                    if not _check_type(value, field_spec.type):
                        issues.append(_issue(
                            "CONTRACT_TYPE_MISMATCH", annotation, {},
                            entity_type=type_name, field_name=field_name,
                            locator=occurrence.locator,
                            message=f"{type_name}.{field_name} expected {field_spec.type}.",
                            details={"expected": field_spec.type,
                                     "actual": type(value).__name__},
                        ))
                        continue
                    if not _check_format(value, field_spec, annotation.base_url):
                        issues.append(_issue(
                            "CONTRACT_FORMAT_INVALID", annotation, {},
                            entity_type=type_name, field_name=field_name,
                            locator=occurrence.locator,
                            message=f"{type_name}.{field_name} failed format {field_spec.format!r}.",
                            details={"format": field_spec.format or field_spec.type},
                        ))
                    if field_spec.enum:
                        comparable = normalize_text(value) if isinstance(value, str) else value
                        if comparable not in field_spec.enum:
                            issues.append(_issue(
                                "CONTRACT_ENUM_INVALID", annotation, {},
                                entity_type=type_name, field_name=field_name,
                                locator=occurrence.locator,
                                message=f"{type_name}.{field_name} is outside its enum.",
                                details={"allowed": list(field_spec.enum)},
                            ))
    return issues


def contract_fingerprint(contract: Contract) -> str:
    return canonical_json(contract.summary())
