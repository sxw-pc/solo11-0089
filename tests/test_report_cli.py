from __future__ import annotations

import json
from pathlib import Path

from conftest import CONTRACT, FIXED_NOW, SITE
from microtag_harvest.cli import main
from microtag_harvest.report import compare_reports, golden_summary


def test_report_contract_and_html_are_offline(executed, expected_path: Path):
    _config, result, artifacts = executed
    report = json.loads(artifacts["report.json"].read_text(encoding="utf-8"))
    assert report["run"]["generated_at"] == FIXED_NOW
    assert report["run"]["contract"]["name"] == "sample-site"
    assert len(report["documents"]) == 6
    assert len(report["entities"]) == len(result.entities)
    assert report["issue_groups"]["total"] == len(report["issues"])
    assert report["issues"] == sorted(
        report["issues"],
        key=lambda issue: (
            issue["document"].casefold(), issue["locator"], issue["code"],
            issue["field"], issue["entity_type"], issue["issue_id"],
        ),
    )
    html = artifacts["report.html"].read_text(encoding="utf-8")
    assert "<style>" in html
    assert 'href="http' not in html
    assert "<script src" not in html
    assert "@import" not in html
    markdown = artifacts["report.md"].read_text(encoding="utf-8")
    assert markdown.startswith("# Microtag Harvest report\n")
    assert "## Issues" in markdown
    summary = golden_summary(report)
    assert expected_path.exists()
    assert summary == json.loads(expected_path.read_text(encoding="utf-8"))


def test_compare_reports_is_deterministic(executed):
    _config, _result, artifacts = executed
    baseline = json.loads(artifacts["report.json"].read_text(encoding="utf-8"))
    current = json.loads(artifacts["report.json"].read_text(encoding="utf-8"))
    current["issues"] = current["issues"][1:]
    current["entities"] = [
        entity for entity in current["entities"]
        if entity["primary_type"] != "Metadata"
    ]
    result = compare_reports(baseline, current)
    assert result["issues"]["counts"]["fixed"] == 1
    assert result["issues"]["counts"]["new"] == 0
    assert result["issues"]["counts"]["remaining"] == len(current["issues"])
    assert result["entities"]["delta"] < 0
    assert result["entities"]["removed_ids"] == sorted(
        entity["entity_id"] for entity in baseline["entities"]
        if entity["primary_type"] == "Metadata"
    )


def test_cli_run_scan_compare_and_golden(tmp_path: Path, capsys):
    out = tmp_path / "out"
    code = main([
        "run", "--input", str(SITE), "--contract", str(CONTRACT),
        "--out", str(out), "--now", FIXED_NOW,
    ])
    assert code == 0
    output = capsys.readouterr().out
    assert "documents=6" in output
    assert "entities=" in output
    report_path = out / "report.json"
    assert report_path.exists()

    scan_out = tmp_path / "scan"
    assert main(["scan", "--input", str(SITE), "--out", str(scan_out)]) == 0
    documents = json.loads((scan_out / "documents.json").read_text(encoding="utf-8"))
    assert len(documents) == 6

    current = json.loads(report_path.read_text(encoding="utf-8"))
    baseline = json.loads(report_path.read_text(encoding="utf-8"))
    current["issues"] = current["issues"][1:]
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    current_path.write_text(json.dumps(current), encoding="utf-8")
    compare_path = tmp_path / "compare.json"
    assert main([
        "compare", str(baseline_path), str(current_path), "--output", str(compare_path)
    ]) == 0
    compared = json.loads(compare_path.read_text(encoding="utf-8"))
    assert compared["issues"]["counts"]["fixed"] == 1

    expected = tmp_path / "expected.json"
    assert main([
        "golden", "--report", str(report_path), "--expected", str(expected), "--write"
    ]) == 0
    assert main([
        "golden", "--report", str(report_path), "--expected", str(expected)
    ]) == 0
    expected_data = json.loads(expected.read_text(encoding="utf-8"))
    expected_data["counts"]["entities"] += 1
    expected.write_text(json.dumps(expected_data), encoding="utf-8")
    assert main([
        "golden", "--report", str(report_path), "--expected", str(expected)
    ]) == 1


def test_cli_invalid_contract_returns_nonzero_with_line(tmp_path: Path, capsys):
    contract = tmp_path / "invalid.yaml"
    contract.write_text(
        "name: bad\nversion: 1\nunknown_type: sometimes\ntypes:\n  Person:\n    fields: {}\n",
        encoding="utf-8",
    )
    code = main([
        "run", "--input", str(SITE), "--contract", str(contract),
        "--out", str(tmp_path / "out"), "--now", FIXED_NOW,
    ])
    assert code == 2
    error = capsys.readouterr().err
    assert "invalid.yaml:3:" in error
    assert "unknown_type" in error
