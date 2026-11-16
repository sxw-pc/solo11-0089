"""Stable issue codes, severities, evidence records, and grouping."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .utils import canonical_json, sha1_short

SEVERITIES = ("error", "warn", "info")

# code -> (default severity, description)
ISSUE_REGISTRY: dict[str, tuple[str, str]] = {
    "DOC_DUPLICATE_CONTENT": ("info", "Two documents have identical bytes."),
    "DOC_HTML_PARSE_ERROR": ("error", "The HTML archive could not be parsed."),
    "PARSER_FAILURE": ("error", "A syntax extractor failed without stopping the run."),
    "PARSE_JSONLD_INVALID": ("error", "A JSON-LD script contains invalid JSON."),
    "REMOTE_CONTEXT_NOT_LOADED": ("warn", "A remote JSON-LD context was recorded but not fetched."),
    "JSONLD_ENTITY_WITHOUT_TYPE": ("warn", "A JSON-LD object has no usable @type."),
    "MICRODATA_ITEM_WITHOUT_TYPE": ("warn", "A top-level Microdata item has no itemtype."),
    "RDFA_ENTITY_WITHOUT_TYPE": ("warn", "An RDFa entity has no usable typeof value."),
    "CONTRACT_UNKNOWN_TYPE": ("error", "An entity type is not declared by the contract."),
    "CONTRACT_MISSING_REQUIRED": ("error", "A required field is absent or empty."),
    "CONTRACT_RECOMMENDED_MISSING": ("warn", "A recommended field is absent or empty."),
    "CONTRACT_TYPE_MISMATCH": ("error", "A field value does not match its declared type."),
    "CONTRACT_FORMAT_INVALID": ("error", "A field value does not match its declared format."),
    "CONTRACT_ENUM_INVALID": ("error", "A field value is outside its declared enum."),
    "CONTRACT_UNKNOWN_FIELD": ("warn", "An entity has a field the contract does not declare."),
    "ENTITY_DUPLICATE_ID": ("warn", "The same document and syntax repeat an @id."),
    "MULTI_SYNTAX_CONFLICT": ("warn", "Syntaxes describe one entity with conflicting scalar values."),
    "FIELD_VALUE_CONFLICT": ("warn", "Merged sources provide incompatible values for a field."),
    "ENTITY_REQUIREMENT_MISSING": ("error", "An entity still lacks a required field after merging eligible sources."),
    "ENTITY_REQUIREMENT_CONFLICT": ("error", "Conflicting field values block an entity contract verdict."),
    "ENTITY_VERIFICATION_INELIGIBLE": ("warn", "No eligible parsed source can establish the entity contract verdict."),
    "DANGLING_REFERENCE": ("warn", "An entity reference has no matching normalized @id."),
    "REFERENCE_CYCLE": ("warn", "Entity references form a cycle."),
}


@dataclass
class RawIssue:
    code: str
    severity: str
    doc_path: str = ""
    syntax: str = ""
    locator: str = ""
    entity_type: str = ""
    field_name: str = ""
    evidence_sha256: str = ""
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Issue:
    issue_id: str
    code: str
    severity: str
    doc_path: str
    syntax: str
    locator: str
    entity_type: str
    field_name: str
    evidence_sha256: str
    message: str
    details_json: str

    def to_dict(self) -> dict[str, Any]:
        import json

        return {
            "issue_id": self.issue_id,
            "code": self.code,
            "severity": self.severity,
            "document": self.doc_path,
            "syntax": self.syntax,
            "locator": self.locator,
            "entity_type": self.entity_type,
            "field": self.field_name,
            "evidence_sha256": self.evidence_sha256,
            "message": self.message,
            "details": json.loads(self.details_json),
        }


def issue_from_raw(raw: RawIssue) -> Issue:
    """Attach a deterministic ID and normalize registry metadata."""
    default_severity, _ = ISSUE_REGISTRY.get(raw.code, ("error", ""))
    severity = raw.severity if raw.severity in SEVERITIES else default_severity
    payload = {
        "code": raw.code,
        "severity": severity,
        "document": raw.doc_path,
        "syntax": raw.syntax,
        "locator": raw.locator,
        "entity_type": raw.entity_type,
        "field": raw.field_name,
        "evidence": raw.evidence_sha256,
        "message": raw.message,
        "details": raw.details,
    }
    issue_id = sha1_short(canonical_json(payload))
    return Issue(
        issue_id=issue_id,
        code=raw.code,
        severity=severity,
        doc_path=raw.doc_path,
        syntax=raw.syntax,
        locator=raw.locator,
        entity_type=raw.entity_type,
        field_name=raw.field_name,
        evidence_sha256=raw.evidence_sha256,
        message=raw.message or ISSUE_REGISTRY.get(raw.code, ("", ""))[1],
        details_json=canonical_json(raw.details),
    )


def issue_sort_key(issue: Issue) -> tuple[str, str, str, str, str, str]:
    return (
        issue.doc_path.replace("\\", "/").casefold(),
        issue.locator,
        issue.code,
        issue.field_name,
        issue.entity_type,
        issue.issue_id,
    )


def normalize_issues(raw_issues: Iterable[RawIssue]) -> list[Issue]:
    unique = {issue.issue_id: issue for issue in map(issue_from_raw, raw_issues)}
    return sorted(unique.values(), key=issue_sort_key)


def issue_counts(issues: Iterable[Issue]) -> dict[str, Any]:
    by_severity = {severity: 0 for severity in SEVERITIES}
    by_code: dict[str, int] = {}
    rows = list(issues)
    for issue in rows:
        by_severity[issue.severity] = by_severity.get(issue.severity, 0) + 1
        by_code[issue.code] = by_code.get(issue.code, 0) + 1
    return {
        "total": len(rows),
        "by_severity": by_severity,
        "by_code": dict(sorted(by_code.items())),
    }
