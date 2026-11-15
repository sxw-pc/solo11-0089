from __future__ import annotations

from microtag_harvest.issues import (
    ISSUE_REGISTRY, RawIssue, issue_counts, issue_from_raw, normalize_issues,
)


def test_issue_registry_and_deterministic_ids():
    assert {
        "PARSE_JSONLD_INVALID", "CONTRACT_MISSING_REQUIRED", "FIELD_VALUE_CONFLICT",
        "DANGLING_REFERENCE", "REFERENCE_CYCLE", "DOC_DUPLICATE_CONTENT",
    } <= set(ISSUE_REGISTRY)
    raw = RawIssue(
        code="FIELD_VALUE_CONFLICT",
        severity="warn",
        doc_path="a.html",
        syntax="json-ld",
        locator="entity[0]#name",
        entity_type="Person",
        field_name="name",
        evidence_sha256="a" * 64,
        message="conflict",
        details={"values": ["A", "B"]},
    )
    first = issue_from_raw(raw)
    second = issue_from_raw(raw)
    assert first.issue_id == second.issue_id
    assert len(first.issue_id) == 12
    changed = issue_from_raw(RawIssue(**{**raw.__dict__, "message": "different"}))
    assert changed.issue_id != first.issue_id
    assert first.to_dict()["details"] == {"values": ["A", "B"]}


def test_issue_counts_and_sorting_are_stable():
    rows = [
        RawIssue(code="DOC_DUPLICATE_CONTENT", severity="info", doc_path="b.html",
                 locator="document", message="duplicate"),
        RawIssue(code="PARSE_JSONLD_INVALID", severity="error", doc_path="a.html",
                 locator="script#0", message="bad json"),
        RawIssue(code="PARSE_JSONLD_INVALID", severity="error", doc_path="a.html",
                 locator="script#0", message="bad json"),
    ]
    issues = normalize_issues(rows)
    assert [issue.code for issue in issues] == [
        "PARSE_JSONLD_INVALID", "DOC_DUPLICATE_CONTENT"
    ]
    counts = issue_counts(issues)
    assert counts["total"] == 2
    assert counts["by_severity"] == {"error": 1, "warn": 0, "info": 1}
    assert counts["by_code"] == {"DOC_DUPLICATE_CONTENT": 1, "PARSE_JSONLD_INVALID": 1}
