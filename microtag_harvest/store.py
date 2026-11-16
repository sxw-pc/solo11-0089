"""SQLite ledger with idempotent annotations and read-only query helpers."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, TYPE_CHECKING

from .utils import sha1_short

if TYPE_CHECKING:
    from .pipeline import PipelineResult

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  generated_at TEXT NOT NULL,
  now TEXT NOT NULL,
  input_dir TEXT NOT NULL,
  contract_sha256 TEXT NOT NULL,
  contract_name TEXT NOT NULL,
  params_json TEXT NOT NULL,
  counts_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
  document_id TEXT PRIMARY KEY,
  path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  size INTEGER NOT NULL,
  parse_status TEXT NOT NULL,
  duplicate_of TEXT,
  error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS run_documents (
  run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  document_id TEXT NOT NULL REFERENCES documents(document_id),
  position INTEGER NOT NULL,
  PRIMARY KEY (run_id, document_id)
);
CREATE TABLE IF NOT EXISTS contracts (
  sha256 TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  version INTEGER NOT NULL,
  source_path TEXT NOT NULL,
  snapshot_yaml TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS annotations (
  annotation_id TEXT PRIMARY KEY,
  document_sha256 TEXT NOT NULL,
  document_path TEXT NOT NULL,
  syntax TEXT NOT NULL,
  locator TEXT NOT NULL,
  fragment_sha256 TEXT NOT NULL,
  types_json TEXT NOT NULL,
  identifier TEXT NOT NULL,
  references_json TEXT NOT NULL,
  fields_json TEXT NOT NULL,
  fragment TEXT NOT NULL,
  base_url TEXT NOT NULL,
  first_run_id TEXT NOT NULL,
  UNIQUE (document_sha256, syntax, locator, fragment_sha256)
);
CREATE TABLE IF NOT EXISTS entities (
  run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  entity_id TEXT NOT NULL,
  primary_type TEXT NOT NULL,
  types_json TEXT NOT NULL,
  key TEXT NOT NULL,
  identifier TEXT NOT NULL,
  fields_json TEXT NOT NULL,
  conflicts_json TEXT NOT NULL,
  sources_json TEXT NOT NULL,
  verification_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (run_id, entity_id)
);
CREATE TABLE IF NOT EXISTS entity_sources (
  run_id TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  annotation_id TEXT NOT NULL REFERENCES annotations(annotation_id),
  PRIMARY KEY (run_id, entity_id, annotation_id),
  FOREIGN KEY (run_id, entity_id) REFERENCES entities(run_id, entity_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS entity_verification (
  run_id TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  verdict TEXT NOT NULL,
  mode TEXT NOT NULL,
  reasons_json TEXT NOT NULL,
  eligible_annotation_ids_json TEXT NOT NULL,
  ineligible_annotation_ids_json TEXT NOT NULL,
  failed_extractions_json TEXT NOT NULL,
  PRIMARY KEY (run_id, entity_id),
  FOREIGN KEY (run_id, entity_id) REFERENCES entities(run_id, entity_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS entity_field_evidence (
  run_id TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  field_name TEXT NOT NULL,
  status TEXT NOT NULL,
  annotation_ids_json TEXT NOT NULL,
  locators_json TEXT NOT NULL,
  fragment_sha256_json TEXT NOT NULL,
  values_json TEXT NOT NULL,
  position INTEGER NOT NULL,
  PRIMARY KEY (run_id, entity_id, entity_type, field_name),
  FOREIGN KEY (run_id, entity_id) REFERENCES entities(run_id, entity_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS issues (
  run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  issue_id TEXT NOT NULL,
  code TEXT NOT NULL,
  severity TEXT NOT NULL,
  document_path TEXT NOT NULL,
  syntax TEXT NOT NULL,
  locator TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  field_name TEXT NOT NULL,
  evidence_sha256 TEXT NOT NULL,
  message TEXT NOT NULL,
  details_json TEXT NOT NULL,
  PRIMARY KEY (run_id, issue_id)
);
CREATE TABLE IF NOT EXISTS reports (
  run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  format TEXT NOT NULL,
  path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  size INTEGER NOT NULL,
  PRIMARY KEY (run_id, format)
);
CREATE INDEX IF NOT EXISTS idx_issues_code ON issues(run_id, code);
CREATE INDEX IF NOT EXISTS idx_annotations_document ON annotations(document_sha256);
CREATE INDEX IF NOT EXISTS idx_field_evidence_status
  ON entity_field_evidence(run_id, status);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    connection.commit()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: str) -> Any:
    return json.loads(value)


def persist(result: "PipelineResult", report_artifacts: Iterable[dict[str, Any]]) -> None:
    """Write one run. Re-running identical inputs does not grow annotation rows."""
    connection = connect(result.config.db_path)
    try:
        init_db(connection)
        contract = result.contract
        run = result.run
        with connection:
            connection.execute(
                """INSERT INTO runs
                (run_id, generated_at, now, input_dir, contract_sha256, contract_name,
                 params_json, counts_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                  generated_at=excluded.generated_at, now=excluded.now,
                  input_dir=excluded.input_dir, contract_sha256=excluded.contract_sha256,
                  contract_name=excluded.contract_name, params_json=excluded.params_json,
                  counts_json=excluded.counts_json""",
                (run["run_id"], run["generated_at"], run["now"], run["input_dir"],
                 contract.sha256, contract.name, _json(run["params"]), _json(run["counts"])),
            )
            connection.execute(
                """INSERT INTO contracts (sha256, name, version, source_path, snapshot_yaml)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(sha256) DO UPDATE SET name=excluded.name,
                  version=excluded.version, source_path=excluded.source_path,
                  snapshot_yaml=excluded.snapshot_yaml""",
                (contract.sha256, contract.name, contract.version,
                 contract.source_path, contract.source_text),
            )
            connection.execute("DELETE FROM run_documents WHERE run_id=?", (run["run_id"],))
            for position, document in enumerate(result.documents):
                document_id = sha1_short(f"{document.path}|{document.sha256}", 16)
                connection.execute(
                    """INSERT INTO documents
                    (document_id, path, sha256, size, parse_status, duplicate_of, error)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(document_id) DO UPDATE SET size=excluded.size,
                      parse_status=excluded.parse_status, duplicate_of=excluded.duplicate_of,
                      error=excluded.error""",
                    (document_id, document.path, document.sha256, document.size,
                     document.parse_status, document.duplicate_of, document.error),
                )
                connection.execute(
                    "INSERT INTO run_documents (run_id, document_id, position) VALUES (?, ?, ?)",
                    (run["run_id"], document_id, position),
                )
            for annotation in result.annotations:
                connection.execute(
                    """INSERT OR IGNORE INTO annotations
                    (annotation_id, document_sha256, document_path, syntax, locator,
                     fragment_sha256, types_json, identifier, references_json, fields_json,
                     fragment, base_url, first_run_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (annotation.annotation_id, annotation.doc_sha256, annotation.doc_path,
                     annotation.syntax, annotation.locator, annotation.fragment_sha256,
                     _json(list(annotation.types)), annotation.identifier,
                     _json(list(annotation.references)),
                     _json({name: [item.value for item in values]
                            for name, values in annotation.fields.items()}),
                     annotation.fragment, annotation.base_url, run["run_id"]),
                )
            connection.execute("DELETE FROM entity_sources WHERE run_id=?", (run["run_id"],))
            connection.execute(
                "DELETE FROM entity_field_evidence WHERE run_id=?", (run["run_id"],)
            )
            connection.execute(
                "DELETE FROM entity_verification WHERE run_id=?", (run["run_id"],)
            )
            connection.execute("DELETE FROM entities WHERE run_id=?", (run["run_id"],))
            for entity in result.entities:
                entity_dict = entity.to_dict()
                verification = entity_dict.get("verification") or {}
                connection.execute(
                    """INSERT INTO entities
                    (run_id, entity_id, primary_type, types_json, key, identifier,
                     fields_json, conflicts_json, sources_json, verification_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (run["run_id"], entity.entity_id, entity.primary_type,
                     _json(list(entity.types)), entity.key, entity.identifier,
                     _json(entity_dict["fields"]), _json(list(entity.conflicts)),
                     _json(entity.source_records()), _json(verification)),
                )
                for source in entity.sources:
                    connection.execute(
                        """INSERT OR IGNORE INTO entity_sources
                        (run_id, entity_id, annotation_id) VALUES (?, ?, ?)""",
                        (run["run_id"], entity.entity_id, source.annotation_id),
                    )
                if entity.verification is not None:
                    connection.execute(
                        """INSERT INTO entity_verification
                        (run_id, entity_id, verdict, mode, reasons_json,
                         eligible_annotation_ids_json, ineligible_annotation_ids_json,
                         failed_extractions_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (run["run_id"], entity.entity_id,
                         entity.verification.verdict, entity.verification.mode,
                         _json(list(entity.verification.reasons)),
                         _json(list(entity.verification.eligible_annotation_ids)),
                         _json(list(entity.verification.ineligible_annotation_ids)),
                         _json([dict(item) for item in entity.verification.failed_extractions])),
                    )
                    position = 0
                    for type_result in entity.verification.type_results:
                        for field_evidence in type_result.field_evidence:
                            connection.execute(
                                """INSERT INTO entity_field_evidence
                                (run_id, entity_id, entity_type, field_name, status,
                                 annotation_ids_json, locators_json, fragment_sha256_json,
                                 values_json, position)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                (run["run_id"], entity.entity_id,
                                 type_result.entity_type, field_evidence.field,
                                 field_evidence.status,
                                 _json(list(field_evidence.annotation_ids)),
                                 _json(list(field_evidence.locators)),
                                 _json(list(field_evidence.fragment_sha256)),
                                 _json(list(field_evidence.values)), position),
                            )
                            position += 1
            connection.execute("DELETE FROM issues WHERE run_id=?", (run["run_id"],))
            for issue in result.issues:
                issue_dict = issue.to_dict()
                connection.execute(
                    """INSERT INTO issues
                    (run_id, issue_id, code, severity, document_path, syntax, locator,
                     entity_type, field_name, evidence_sha256, message, details_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (run["run_id"], issue.issue_id, issue.code, issue.severity,
                     issue.doc_path, issue.syntax, issue.locator, issue.entity_type,
                     issue.field_name, issue.evidence_sha256, issue.message,
                     issue.details_json),
                )
            connection.execute("DELETE FROM reports WHERE run_id=?", (run["run_id"],))
            for artifact in report_artifacts:
                connection.execute(
                    """INSERT INTO reports (run_id, format, path, sha256, size)
                    VALUES (?, ?, ?, ?, ?)""",
                    (run["run_id"], artifact["format"], artifact["path"],
                     artifact["sha256"], artifact["size"]),
                )
    finally:
        connection.close()

from .queries import (  # noqa: E402,F401  (read-only API re-export)
    get_entity, get_run, latest_run, list_documents, list_entities,
    list_field_evidence, list_issues, report_rows,
)
