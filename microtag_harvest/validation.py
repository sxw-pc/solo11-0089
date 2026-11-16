"""Contract field validation kept separate from YAML parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from .contracts import Contract, FieldSpec, TypeSpec
from .issues import ISSUE_REGISTRY, RawIssue
from .merge import MergedEntity
from .normalize import is_iso_date, normalize_date, normalize_text, normalize_url
from .parsers.common import Annotation, field_locator
from .utils import canonical_json, collapse_whitespace

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Field-level evidence verdicts. Missing, complementary satisfaction and
# unresolved conflict are distinct states and must never be conflated.
FIELD_PRESENT = "present"
FIELD_COMPLEMENTARY = "complementary"
FIELD_MISSING = "missing"
FIELD_CONFLICT = "conflict"
FIELD_INELIGIBLE = "ineligible"
FIELD_EVIDENCE_STATUSES = (
    FIELD_PRESENT, FIELD_COMPLEMENTARY, FIELD_MISSING, FIELD_CONFLICT, FIELD_INELIGIBLE,
)

# Entity-level contract verdicts.
ENTITY_PASS = "pass"
ENTITY_FAIL_MISSING = "fail_missing"
ENTITY_FAIL_CONFLICT = "fail_conflict"
ENTITY_INELIGIBLE = "ineligible"
ENTITY_VERDICTS = (
    ENTITY_PASS, ENTITY_FAIL_MISSING, ENTITY_FAIL_CONFLICT, ENTITY_INELIGIBLE,
)

# Reasons recorded on an ineligible verdict.
REASON_UNKNOWN_TYPE = "unknown_type"
REASON_EXTRACTION_INCOMPLETE = "extraction_incomplete"


@dataclass(frozen=True)
class FieldEvidence:
    """Field-level contract evidence bound to the annotations that establish it."""

    field: str
    status: str
    annotation_ids: tuple[str, ...] = ()
    locators: tuple[str, ...] = ()
    fragment_sha256: tuple[str, ...] = ()
    values: tuple[Any, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "status": self.status,
            "annotation_ids": list(self.annotation_ids),
            "locators": list(self.locators),
            "fragment_sha256": list(self.fragment_sha256),
            "values": list(self.values),
        }


@dataclass(frozen=True)
class TypeRequirementResult:
    """Verification outcome for one contract-declared type of an entity."""

    entity_type: str
    verdict: str
    required: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    conflicting_fields: tuple[str, ...] = ()
    field_evidence: tuple[FieldEvidence, ...] = ()
    eligible_annotation_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "verdict": self.verdict,
            "required": list(self.required),
            "missing_fields": list(self.missing_fields),
            "conflicting_fields": list(self.conflicting_fields),
            "eligible_annotation_ids": list(self.eligible_annotation_ids),
            "field_evidence": [item.to_dict() for item in self.field_evidence],
        }


@dataclass(frozen=True)
class EntityVerification:
    """Entity-level contract conclusion driven by merge grouping and conflicts."""

    entity_id: str
    verdict: str
    mode: str
    reasons: tuple[str, ...] = ()
    eligible_annotation_ids: tuple[str, ...] = ()
    ineligible_annotation_ids: tuple[str, ...] = ()
    failed_extractions: tuple[dict[str, str], ...] = ()
    type_results: tuple[TypeRequirementResult, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "verdict": self.verdict,
            "mode": self.mode,
            "reasons": list(self.reasons),
            "eligible_annotation_ids": list(self.eligible_annotation_ids),
            "ineligible_annotation_ids": list(self.ineligible_annotation_ids),
            "failed_extractions": [dict(item) for item in self.failed_extractions],
            "type_results": [item.to_dict() for item in self.type_results],
        }


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


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _syntax_rank(syntax: str) -> int:
    order = {"json-ld": 0, "microdata": 1, "rdfa": 2, "meta": 3}
    return order.get(syntax, 4)


def _sort_annotations(sources: Iterable[Annotation]) -> list[Annotation]:
    return sorted(
        sources,
        key=lambda item: (
            item.doc_path, _syntax_rank(item.syntax), item.syntax,
            item.locator, item.annotation_id,
        ),
    )


def _eligible_sources(entity: MergedEntity, spec: TypeSpec) -> list[Annotation]:
    """Sources that may establish an entity verdict for one contract type.

    Only successfully parsed annotations whose type is declared by the
    contract are eligible. Unknown-type or unparsed sources can never make an
    entity look complete.
    """
    return [
        source
        for source in _sort_annotations(entity.sources)
        if spec.name in source.types
    ]


def _build_field_evidence(
    entity: MergedEntity,
    spec: TypeSpec,
    eligible: list[Annotation],
    eligible_ids: set[str],
) -> list[FieldEvidence]:
    """Assemble field-level evidence for every required field of one type."""
    evidence: list[FieldEvidence] = []
    for field_name in spec.required:
        if field_name in entity.conflicts:
            # Conflict dominates: an unresolved field can never satisfy the
            # contract even though values are physically present.
            values = [
                item for item in entity.fields.get(field_name, [])
                if item.annotation_id in eligible_ids
            ]
            evidence.append(FieldEvidence(
                field=field_name,
                status=FIELD_CONFLICT,
                annotation_ids=tuple(sorted({item.annotation_id for item in values})),
                locators=tuple(sorted({item.locator for item in values})),
                fragment_sha256=tuple(sorted({item.fragment_sha256 for item in values})),
                values=tuple(item.value for item in values),
            ))
            continue

        # Non-empty normalized values contributed by eligible sources only.
        present_values = [
            item
            for item in entity.fields.get(field_name, [])
            if item.annotation_id in eligible_ids and not _is_empty(item.value)
        ]
        if not present_values:
            evidence.append(FieldEvidence(
                field=field_name,
                status=FIELD_INELIGIBLE if not eligible else FIELD_MISSING,
            ))
            continue

        provider_ids = {item.annotation_id for item in present_values}
        status = (
            FIELD_PRESENT
            if provider_ids == {source.annotation_id for source in eligible}
            else FIELD_COMPLEMENTARY
        )
        evidence.append(FieldEvidence(
            field=field_name,
            status=status,
            annotation_ids=tuple(sorted(provider_ids)),
            locators=tuple(sorted({item.locator for item in present_values})),
            fragment_sha256=tuple(sorted({item.fragment_sha256 for item in present_values})),
            values=tuple(item.value for item in present_values),
        ))
    return evidence


def _verify_type(
    entity: MergedEntity,
    spec: TypeSpec,
    mode: str,
) -> TypeRequirementResult:
    eligible = _eligible_sources(entity, spec)
    eligible_ids = {source.annotation_id for source in eligible}
    evidence = _build_field_evidence(entity, spec, eligible, eligible_ids)
    conflicting = tuple(item.field for item in evidence if item.status == FIELD_CONFLICT)
    absent = tuple(
        item.field for item in evidence
        if item.status in (FIELD_MISSING, FIELD_INELIGIBLE)
    )

    if not eligible:
        verdict = ENTITY_INELIGIBLE
        missing = absent
    elif conflicting:
        # Conflicting values block a pass under both authorized modes.
        verdict = ENTITY_FAIL_CONFLICT
        missing = absent
    elif mode == "independent":
        # Every source must itself carry every required field. A field only
        # present on a subset of sources is complementary, not independently
        # declared, so it counts as an unmet requirement at entity level.
        complementary = tuple(
            item.field for item in evidence if item.status == FIELD_COMPLEMENTARY
        )
        missing = absent + complementary
        verdict = ENTITY_FAIL_MISSING if missing else ENTITY_PASS
    else:
        # Joint mode: complementary sources may combine into a complete entity.
        missing = absent
        verdict = ENTITY_FAIL_MISSING if missing else ENTITY_PASS

    return TypeRequirementResult(
        entity_type=spec.name,
        verdict=verdict,
        required=tuple(spec.required),
        missing_fields=missing,
        conflicting_fields=conflicting,
        field_evidence=tuple(evidence),
        eligible_annotation_ids=tuple(sorted(eligible_ids)),
    )


def _failed_extraction_slots(
    entities: list[MergedEntity],
    parse_issues: Iterable[RawIssue],
) -> dict[str, list[dict[str, str]]]:
    """Map each entity to parser slots that failed on its source documents."""
    entity_docs = {
        entity.entity_id: {source.doc_path for source in entity.sources}
        for entity in entities
    }
    slots: dict[str, list[dict[str, str]]] = {
        entity.entity_id: [] for entity in entities
    }
    for issue in parse_issues:
        if issue.code not in {"PARSER_FAILURE", "DOC_HTML_PARSE_ERROR"}:
            continue
        for entity_id, doc_paths in entity_docs.items():
            if issue.doc_path in doc_paths:
                slots[entity_id].append({
                    "document": issue.doc_path,
                    "syntax": issue.syntax or "document",
                    "locator": issue.locator,
                    "fragment_sha256": issue.evidence_sha256,
                })
    for entity_id in slots:
        slots[entity_id] = sorted(
            slots[entity_id],
            key=lambda item: (
                item["document"], _syntax_rank(item["syntax"]),
                item["syntax"], item["locator"],
            ),
        )
    return slots


def _entity_issue(
    entity: MergedEntity,
    code: str,
    message: str,
    details: dict[str, Any],
    field_name: str = "",
) -> RawIssue:
    anchor = entity.sources[0]
    return RawIssue(
        code=code,
        severity=ISSUE_REGISTRY.get(code, ("error", ""))[0],
        doc_path=anchor.doc_path,
        syntax=anchor.syntax,
        locator=anchor.locator,
        entity_type=entity.primary_type,
        field_name=field_name,
        evidence_sha256=anchor.fragment_sha256,
        message=message,
        details=details,
    )


def verify_entities(
    entities: Iterable[MergedEntity],
    contract: Contract,
    parse_issues: Iterable[RawIssue] = (),
) -> tuple[list[EntityVerification], list[RawIssue]]:
    """Derive entity-level contract verdicts from merged groups and conflicts.

    Per-source annotation issues produced by ``validate_annotations`` stay
    untouched; this layer adds the entity_id-bound integrity conclusion and
    field-level evidence. Merge grouping and conflict changes therefore drive
    the entity verdict directly.
    """
    entity_list = list(entities)
    mode = contract.entity_verification.mode
    failed_slots = _failed_extraction_slots(entity_list, parse_issues)
    verifications: list[EntityVerification] = []
    issues: list[RawIssue] = []

    for entity in entity_list:
        declared_types = [
            type_name for type_name in entity.types if type_name in contract.types
        ]
        all_source_ids = {source.annotation_id for source in entity.sources}
        type_results = [
            _verify_type(entity, contract.types[type_name], mode)
            for type_name in sorted(declared_types)
        ]
        eligible_ids = {
            annotation_id
            for result in type_results
            for annotation_id in result.eligible_annotation_ids
        }
        ineligible_ids = tuple(sorted(all_source_ids - eligible_ids))
        extraction_failures = tuple(failed_slots.get(entity.entity_id, ()))

        if not declared_types or not eligible_ids:
            verdict = ENTITY_INELIGIBLE
            reasons = [REASON_EXTRACTION_INCOMPLETE] if extraction_failures else []
            if not declared_types or ineligible_ids:
                reasons.append(REASON_UNKNOWN_TYPE)
            reasons = tuple(dict.fromkeys(reasons)) or (REASON_UNKNOWN_TYPE,)
        elif any(result.verdict == ENTITY_FAIL_CONFLICT for result in type_results):
            # An unresolved conflict is the strongest failure and always wins.
            verdict = ENTITY_FAIL_CONFLICT
            reasons = (REASON_EXTRACTION_INCOMPLETE,) if extraction_failures else ()
        elif extraction_failures:
            # Parsing gaps mean the merged picture cannot be trusted as
            # complete: an unparsed source must never be silently treated as
            # satisfying, nor merely reported as a missing field.
            verdict = ENTITY_INELIGIBLE
            reasons = (REASON_EXTRACTION_INCOMPLETE,)
        elif any(result.verdict == ENTITY_FAIL_MISSING for result in type_results):
            verdict = ENTITY_FAIL_MISSING
            reasons = ()
        else:
            verdict = ENTITY_PASS
            reasons = ()

        verification = EntityVerification(
            entity_id=entity.entity_id,
            verdict=verdict,
            mode=mode,
            reasons=reasons,
            eligible_annotation_ids=tuple(sorted(eligible_ids)),
            ineligible_annotation_ids=ineligible_ids,
            failed_extractions=extraction_failures,
            type_results=tuple(type_results),
        )
        entity.verification = verification
        verifications.append(verification)
        issues.extend(_verification_issues(entity, verification, contract))

    return verifications, issues


def _verification_issues(
    entity: MergedEntity,
    verification: EntityVerification,
    contract: Contract,
) -> list[RawIssue]:
    issues: list[RawIssue] = []
    details_base = {
        "entity_id": entity.entity_id,
        "mode": verification.mode,
        "verdict": verification.verdict,
        "annotation_ids": [
            record["annotation_id"] for record in entity.source_records()
        ],
    }
    if verification.verdict == ENTITY_INELIGIBLE:
        issues.append(_entity_issue(
            entity,
            "ENTITY_VERIFICATION_INELIGIBLE",
            f"Entity {entity.entity_id} has no eligible parsed source for contract "
            f"{contract.name!r}; it cannot be judged complete.",
            {
                **details_base,
                "reasons": list(verification.reasons),
                "ineligible_annotation_ids": list(verification.ineligible_annotation_ids),
                "failed_extractions": [dict(item) for item in verification.failed_extractions],
            },
        ))
        return issues

    for result in verification.type_results:
        for field_name in result.conflicting_fields:
            evidence = next(
                item for item in result.field_evidence if item.field == field_name
            )
            issues.append(_entity_issue(
                entity,
                "ENTITY_REQUIREMENT_CONFLICT",
                f"{result.entity_type}.{field_name} has unresolved conflicting values; "
                "the entity cannot pass contract verification.",
                {
                    **details_base,
                    "entity_type": result.entity_type,
                    "field": field_name,
                    "annotation_ids": list(evidence.annotation_ids),
                    "locators": list(evidence.locators),
                    "fragment_sha256": list(evidence.fragment_sha256),
                    "values": list(evidence.values),
                },
                field_name=field_name,
            ))
        for field_name in result.missing_fields:
            evidence = next(
                item for item in result.field_evidence if item.field == field_name
            )
            issues.append(_entity_issue(
                entity,
                "ENTITY_REQUIREMENT_MISSING",
                f"{result.entity_type}.{field_name} is not satisfied by any eligible "
                f"source after merging ({verification.mode} mode).",
                {
                    **details_base,
                    "entity_type": result.entity_type,
                    "field": field_name,
                    "field_status": evidence.status,
                    "eligible_annotation_ids": list(result.eligible_annotation_ids),
                },
                field_name=field_name,
            ))
    return issues
