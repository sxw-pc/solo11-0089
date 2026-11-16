from __future__ import annotations

import sqlite3
from pathlib import Path

from microtag_harvest.config import RunConfig
from microtag_harvest.pipeline import execute
from microtag_harvest.queries import (
    get_entity, get_run, latest_run, list_documents, list_entities, list_issues,
    report_rows,
)
from microtag_harvest.store import connect


def test_ledger_tables_and_queries(executed):
    config, result, artifacts = executed
    assert config.db_path.exists()
    connection = connect(config.db_path)
    try:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "documents", "runs", "annotations", "entities", "entity_sources",
            "issues", "contracts", "reports", "run_documents",
        } <= tables
        run = latest_run(connection)
        assert run is not None
        assert run["run_id"] == result.run["run_id"]
        assert run["counts"] == result.counts
        assert get_run(connection, run["run_id"])["contract_name"] == "sample-site"
        documents = list_documents(connection, run["run_id"])
        assert len(documents) == 6
        assert [doc["path"] for doc in documents] == sorted(
            [doc["path"] for doc in documents], key=str.casefold
        )
        entities = list_entities(connection, run["run_id"])
        assert len(entities) == result.counts["entities"]
        assert all(entity["source_count"] >= 1 for entity in entities)
        issues = list_issues(connection, run["run_id"])
        assert len(issues) == result.counts["issues"]["total"]
        assert list_issues(connection, run["run_id"], severity="error")
        assert all(issue["severity"] == "error"
                   for issue in list_issues(connection, run["run_id"], severity="error"))
        reports = report_rows(connection, run["run_id"])
        assert [row["format"] for row in reports] == ["html", "json", "md"]
        assert all(len(row["sha256"]) == 64 for row in reports)
        assert connection.execute("SELECT COUNT(*) FROM contracts").fetchone()[0] == 1
        product = next(entity for entity in result.entities if entity.primary_type == "Product"
                       and entity.identifier.endswith("#product"))
        detail = get_entity(connection, run["run_id"], product.entity_id)
        assert detail is not None
        assert len(detail["source_fragments"]) == 3
        assert all("fragment" in source for source in detail["source_fragments"])
    finally:
        connection.close()
    assert artifacts["report.json"].exists()


def test_annotations_are_idempotent_and_reports_byte_stable(executed):
    config, result, artifacts = executed
    first_report = artifacts["report.json"].read_bytes()
    connection = connect(config.db_path)
    first_annotations = connection.execute("SELECT COUNT(*) FROM annotations").fetchone()[0]
    first_runs = connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    connection.close()

    second_result, second_artifacts = execute(config)
    assert second_result.run["run_id"] == result.run["run_id"]
    assert second_artifacts["report.json"].read_bytes() == first_report

    connection = connect(config.db_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM annotations").fetchone()[0] == (
            first_annotations
        )
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == first_runs
        assert connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == (
            len(result.entities)
        )
    finally:
        connection.close()


def test_new_run_keeps_global_annotations_without_growth(executed, tmp_path: Path):
    config, result, _artifacts = executed
    connection = connect(config.db_path)
    annotation_count = connection.execute("SELECT COUNT(*) FROM annotations").fetchone()[0]
    connection.close()
    other = RunConfig.create(
        input_dir=config.input_dir,
        contract_path=config.contract_path,
        out_dir=tmp_path / "out2",
        now="2026-02-02T00:00:00Z",
        db_path=config.db_path,
    )
    other_result, _ = execute(other)
    assert other_result.run["run_id"] != result.run["run_id"]
    connection = connect(config.db_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM annotations").fetchone()[0] == (
            annotation_count
        )
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2
        latest = latest_run(connection)
        assert latest["run_id"] == other_result.run["run_id"]
        assert len(list_entities(connection, latest["run_id"])) == len(result.entities)
    finally:
        connection.close()
