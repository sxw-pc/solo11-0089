"""Event series intake binds the archive identities without scheduling them."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from microtag_harvest.config import RunConfig
from microtag_harvest.pipeline import build_result
from microtag_harvest.report import build_report
from microtag_harvest.series_evidence import load_series_evidence

ROOT = Path(__file__).resolve().parents[1]
SERIES = ROOT / "fixtures" / "series"


def _report(tmp_path: Path) -> Path:
    config = RunConfig.create(
        ROOT / "fixtures" / "series-site", ROOT / "fixtures" / "contracts" / "site.yaml",
        tmp_path / "out", now="2025-03-01T00:00:00Z",
    )
    target = tmp_path / "report.json"
    target.write_text(json.dumps(build_report(build_result(config))), encoding="utf-8")
    return target


def test_valid_source_binds_event_article_and_person(tmp_path: Path) -> None:
    evidence = load_series_evidence(_report(tmp_path), SERIES / "terms.json", SERIES / "notice.json")
    assert evidence["event_start_utc"] == "2025-03-02T07:30:00+00:00"
    assert evidence["notice"]["occurrence_index"] == 3
    assert len(evidence["terms"]["contracts"]) == 2
    assert "occurrences" not in evidence


def test_wrong_event_and_conflicting_notice_are_rejected(tmp_path: Path) -> None:
    report = _report(tmp_path)
    with pytest.raises(ValueError, match="Event identity missing"):
        load_series_evidence(report, SERIES / "wrong-event.json", SERIES / "notice.json")
    with pytest.raises(ValueError, match="notice does not match"):
        load_series_evidence(report, SERIES / "terms.json", SERIES / "conflicting-notice.json")


def test_event_anchor_change_cannot_keep_old_series_terms(tmp_path: Path) -> None:
    report_path = _report(tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    event = next(item for item in report["entities"] if item["primary_type"] == "Event")
    event["fields"]["startDate"][0]["value"] = "2025-03-03T07:30:00Z"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="series anchor differ"):
        load_series_evidence(report_path, SERIES / "terms.json", SERIES / "notice.json")
