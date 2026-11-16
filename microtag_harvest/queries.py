"""Read-only SQLite query helpers for reports and the review console."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def _loads(value: str) -> Any:
    return json.loads(value)


def latest_run(connection: sqlite3.Connection) -> dict[str, Any] | None:
    row = connection.execute("SELECT * FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
    if row is None:
        return None
    result = dict(row)
    result["params"] = _loads(result.pop("params_json"))
    result["counts"] = _loads(result.pop("counts_json"))
    return result


def get_run(connection: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["params"] = _loads(result.pop("params_json"))
    result["counts"] = _loads(result.pop("counts_json"))
    return result


def list_documents(connection: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT d.* FROM documents d JOIN run_documents rd ON rd.document_id=d.document_id
        WHERE rd.run_id=? ORDER BY rd.position""",
        (run_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def list_entities(connection: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT e.*, COUNT(es.annotation_id) AS source_count
        FROM entities e LEFT JOIN entity_sources es
          ON es.run_id=e.run_id AND es.entity_id=e.entity_id
        WHERE e.run_id=? GROUP BY e.run_id, e.entity_id ORDER BY e.entity_id""",
        (run_id,),
    ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["types"] = _loads(item.pop("types_json"))
        item["conflicts"] = _loads(item.pop("conflicts_json"))
        item["sources"] = _loads(item.pop("sources_json"))
        item["verification"] = _loads(item.pop("verification_json"))
        item.pop("fields_json", None)
        results.append(item)
    return results


def list_field_evidence(
    connection: sqlite3.Connection,
    run_id: str,
    entity_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    clauses = ["run_id=?"]
    params: list[Any] = [run_id]
    if entity_id:
        clauses.append("entity_id=?")
        params.append(entity_id)
    if status:
        clauses.append("status=?")
        params.append(status)
    rows = connection.execute(
        f"""SELECT * FROM entity_field_evidence WHERE {' AND '.join(clauses)}
        ORDER BY entity_id, position""",
        params,
    ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        for key in (
            "annotation_ids_json", "locators_json", "fragment_sha256_json",
            "values_json", "reasons_json",
        ):
            if key in item:
                item[key.removesuffix("_json")] = _loads(item.pop(key))
        results.append(item)
    return results


def get_entity(connection: sqlite3.Connection, run_id: str,
               entity_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM entities WHERE run_id=? AND entity_id=?", (run_id, entity_id)
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["types"] = _loads(result.pop("types_json"))
    result["fields"] = _loads(result.pop("fields_json"))
    result["conflicts"] = _loads(result.pop("conflicts_json"))
    result["sources"] = _loads(result.pop("sources_json"))
    result["verification"] = _loads(result.pop("verification_json"))
    verification_row = connection.execute(
        "SELECT * FROM entity_verification WHERE run_id=? AND entity_id=?",
        (run_id, entity_id),
    ).fetchone()
    if verification_row is not None:
        verification = dict(verification_row)
        for key in (
            "reasons_json", "eligible_annotation_ids_json",
            "ineligible_annotation_ids_json", "failed_extractions_json",
        ):
            verification[key.removesuffix("_json")] = _loads(verification.pop(key))
        result["verification_summary"] = verification
    result["field_evidence"] = list_field_evidence(
        connection, run_id, entity_id=entity_id
    )
    source_rows = connection.execute(
        """SELECT a.annotation_id, a.document_path, a.syntax, a.locator,
                  a.fragment_sha256, a.fragment, a.fields_json
        FROM entity_sources es JOIN annotations a ON a.annotation_id=es.annotation_id
        WHERE es.run_id=? AND es.entity_id=? ORDER BY a.document_path, a.syntax, a.locator""",
        (run_id, entity_id),
    ).fetchall()
    result["source_fragments"] = [
        {
            **dict(source_row),
            "fields": _loads(source_row["fields_json"]),
        }
        for source_row in source_rows
    ]
    for item in result["source_fragments"]:
        item.pop("fields_json", None)
    return result


def list_issues(
    connection: sqlite3.Connection,
    run_id: str,
    severity: str | None = None,
    code: str | None = None,
) -> list[dict[str, Any]]:
    clauses = ["run_id=?"]
    params: list[Any] = [run_id]
    if severity:
        clauses.append("severity=?")
        params.append(severity)
    if code:
        clauses.append("code=?")
        params.append(code)
    rows = connection.execute(
        f"""SELECT * FROM issues WHERE {' AND '.join(clauses)}
        ORDER BY document_path, locator, code, field_name, issue_id""",
        params,
    ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["details"] = _loads(item.pop("details_json"))
        results.append(item)
    return results


def report_rows(connection: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT format, path, sha256, size FROM reports WHERE run_id=? ORDER BY format",
        (run_id,),
    ).fetchall()
    return [dict(row) for row in rows]
