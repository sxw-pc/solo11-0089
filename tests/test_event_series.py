"""Event occurrence table across two time promises and the spring DST gap.

The table is a consumption result built only from a valid report plus validated
series evidence; it never guesses the anchor from Article.datePublished or any
displayed time string.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from microtag_harvest.cli import main
from microtag_harvest.config import RunConfig
from microtag_harvest.event_series import (
    CANCELLED,
    GAP_SKIPPED,
    SUBSCRIBABLE,
    build_event_series,
)
from microtag_harvest.pipeline import build_result
from microtag_harvest.report import build_report
from microtag_harvest.utils import pretty_json, sha256_hex

ROOT = Path(__file__).resolve().parents[1]
SERIES = ROOT / "fixtures" / "series"
SITE = ROOT / "fixtures" / "series-site"
SHIFT_SITE = ROOT / "fixtures" / "series-shift"
CONTRACT = ROOT / "fixtures" / "contracts" / "site.yaml"
TERMS = SERIES / "terms.json"
NOTICE = SERIES / "notice.json"


def _write_report(site: Path, target: Path, now: str = "2025-03-01T00:00:00Z") -> Path:
    config = RunConfig.create(
        site, CONTRACT, target.parent / "out", now=now,
    )
    target.write_text(
        json.dumps(build_report(build_result(config))), encoding="utf-8"
    )
    return target


def _by_index(contract: dict) -> dict[int, dict]:
    return {row["occurrence_index"]: row for row in contract["occurrences"]}


def _contract(series: dict, contract_id: str) -> dict:
    return next(item for item in series["contracts"] if item["id"] == contract_id)


@pytest.fixture
def report_path(tmp_path: Path) -> Path:
    return _write_report(SITE, tmp_path / "report.json")


@pytest.fixture
def series(report_path: Path) -> dict:
    return build_event_series(report_path, TERMS, NOTICE)


def test_wall_clock_skips_the_nonexistent_local_hour_without_a_utc_instant(
    series: dict,
) -> None:
    wall = _by_index(_contract(series, "wall_clock"))
    first, second, third, fourth = (wall[i] for i in range(1, 5))

    assert first["status"] == SUBSCRIBABLE
    assert first["local_time"] == "2025-03-02T02:30:00-05:00"
    assert first["utc_instant"] == "2025-03-02T07:30:00+00:00"

    # 2025-03-09T02:30 never happens in America/New_York: a syntactically valid
    # 02:30 datetime must never be silently treated as a bookable instant.
    assert second["status"] == GAP_SKIPPED
    assert second["scheduled_local"] == "2025-03-09T02:30:00"
    assert second["local_time"] is None
    assert second["utc_instant"] is None
    assert second["utc_offset_minutes"] is None
    assert second["notice"] is None

    assert third["status"] == CANCELLED
    assert third["local_time"] == "2025-03-16T02:30:00-04:00"
    assert third["utc_instant"] == "2025-03-16T06:30:00+00:00"
    assert third["notice"] == {
        "notice_id": "N100",
        "occurrence_index": 3,
        "published_at": "2025-02-28T12:00:00Z",
    }
    assert fourth["status"] == SUBSCRIBABLE
    assert series["summary"]["wall_clock"] == {
        "total_logical_occurrences": 4,
        "subscribable": 2,
        "gap_skipped": 1,
        "cancelled": 1,
    }


def test_elapsed_contract_keeps_168_hour_utc_cadence_and_shows_local_03_30(
    series: dict,
) -> None:
    elapsed = _by_index(_contract(series, "elapsed_168h"))
    assert [row["utc_instant"] for row in (elapsed[i] for i in range(1, 5))] == [
        "2025-03-02T07:30:00+00:00",
        "2025-03-09T07:30:00+00:00",
        "2025-03-16T07:30:00+00:00",
        "2025-03-23T07:30:00+00:00",
    ]
    second = elapsed[2]
    assert second["status"] == SUBSCRIBABLE
    assert second["local_time"] == "2025-03-09T03:30:00-04:00"
    assert second["utc_offset_minutes"] == -240
    assert all(
        row["status"] != GAP_SKIPPED for row in elapsed.values()
    )
    assert series["summary"]["elapsed_168h"] == {
        "total_logical_occurrences": 4,
        "subscribable": 3,
        "gap_skipped": 0,
        "cancelled": 1,
    }


def test_cancel_notice_binds_to_logical_index_not_utc_or_local_strings(
    series: dict,
) -> None:
    wall = _by_index(_contract(series, "wall_clock"))
    elapsed = _by_index(_contract(series, "elapsed_168h"))

    # The third logical occurrence has different UTC instants (and displayed
    # local times) under the two promises, yet both carry the same cancellation.
    assert wall[3]["status"] == CANCELLED
    assert elapsed[3]["status"] == CANCELLED
    assert wall[3]["utc_instant"] != elapsed[3]["utc_instant"]
    assert wall[3]["local_time"] != elapsed[3]["local_time"]
    assert wall[3]["status_source"] == "notice:N100"
    assert elapsed[3]["status_source"] == "notice:N100"
    assert wall[3]["occurrence_index"] == elapsed[3]["occurrence_index"] == 3
    # Neither contract may cancel by matching a time string elsewhere.
    assert all(
        row["status"] != CANCELLED
        for contract in series["contracts"]
        for row in contract["occurrences"]
        if row["occurrence_index"] != 3
    )


def test_each_occurrence_keeps_identities_contract_id_stable_id_and_fingerprint(
    series: dict,
) -> None:
    for contract in series["contracts"]:
        for row in contract["occurrences"]:
            assert row["event_id"] == series["event_id"]
            assert row["article_id"] == series["article_id"]
            assert row["organizer_id"] == series["organizer_id"]
            assert row["contract_id"] == contract["id"]
            assert row["occurrence_id"].startswith("occ-")
            assert len(row["occurrence_id"]) == 20
            assert row["sources"]["event"]["primary_type"] == "Event"
            assert row["sources"]["article"]["primary_type"] == "Article"
            assert row["sources"]["person"]["primary_type"] == "Person"
            assert row["sources"]["event"]["field"] == "startDate"
            assert row["sources"]["article"]["field"] == "author"
            # Fingerprints cover the full occurrence record including sources.
            payload = {k: v for k, v in row.items() if k != "fingerprint"}
            assert row["fingerprint"] == sha256_hex(
                json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), default=str)
            )
    # Stable occurrence ID: same event + contract + index always hashes alike.
    wall_one = _by_index(_contract(series, "wall_clock"))[1]
    assert wall_one["occurrence_id"] == "occ-e7115138538d1968"
    assert series["source_fingerprint"]
    assert series["series_fingerprint"]


def test_reruns_are_byte_stable_and_contracts_are_not_swapped(
    report_path: Path,
) -> None:
    first = pretty_json(build_event_series(report_path, TERMS, NOTICE))
    second = pretty_json(build_event_series(report_path, TERMS, NOTICE))
    assert first == second
    series = json.loads(first)
    # The two promises retain their own authorization; wall_clock never borrows
    # the 168-hour cadence and elapsed_168h never gains a gap clause.
    promises = {c["id"]: (c["promise"], c["gap"]) for c in series["contracts"]}
    assert promises == {
        "wall_clock": ("same_local_time", "skip"),
        "elapsed_168h": ("fixed_168_hours", "not_applicable"),
    }


def test_html_anchor_change_rejects_old_terms_and_recomputes_with_new_terms(
    tmp_path: Path,
) -> None:
    shifted_report = _write_report(SHIFT_SITE, tmp_path / "shifted-report.json")
    # Old anchor terms must be refused: no stale occurrences survive.
    with pytest.raises(ValueError, match="series anchor differ"):
        build_event_series(shifted_report, TERMS, NOTICE)
    # Terms re-anchored to the new HTML startDate recompute every instant.
    shifted = build_event_series(
        shifted_report, SERIES / "terms-shift.json", NOTICE
    )
    wall = _by_index(_contract(shifted, "wall_clock"))
    elapsed = _by_index(_contract(shifted, "elapsed_168h"))
    assert shifted["anchor"]["event_start_utc"] == "2025-03-02T08:30:00+00:00"
    assert wall[1]["utc_instant"] == "2025-03-02T08:30:00+00:00"
    assert elapsed[1]["utc_instant"] == "2025-03-02T08:30:00+00:00"
    # 03:30 local exists on the gap Sunday, so wall_clock index 2 is real now.
    assert wall[2]["status"] == SUBSCRIBABLE
    assert wall[2]["local_time"] == "2025-03-09T03:30:00-04:00"
    assert shifted["summary"]["wall_clock"]["gap_skipped"] == 0
    # The cancellation still lands on logical index 3.
    assert wall[3]["status"] == CANCELLED
    assert elapsed[3]["status"] == CANCELLED


def test_mutated_report_anchor_is_refused_without_reusing_old_instances(
    report_path: Path, tmp_path: Path,
) -> None:
    baseline = build_event_series(report_path, TERMS, NOTICE)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    event = next(item for item in report["entities"]
                 if item["primary_type"] == "Event")
    event["fields"]["startDate"][0]["value"] = "2025-03-03T07:30:00Z"
    mutated = tmp_path / "mutated-report.json"
    mutated.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="series anchor differ"):
        build_event_series(mutated, TERMS, NOTICE)
    # And the source fingerprint changes, proving the table is bound to source.
    assert baseline["source_fingerprint"] != (
        build_event_series(
            _write_report(SHIFT_SITE, tmp_path / "other-report.json"),
            SERIES / "terms-shift.json", NOTICE,
        )["source_fingerprint"]
    )


def test_renumbered_notice_moves_cancellation_to_the_new_logical_index(
    report_path: Path,
) -> None:
    moved = build_event_series(
        report_path, TERMS, SERIES / "notice-index4.json"
    )
    for contract_id in ("wall_clock", "elapsed_168h"):
        rows = _by_index(_contract(moved, contract_id))
        assert rows[4]["status"] == CANCELLED
        assert rows[4]["status_source"] == "notice:N100"
        assert rows[3]["status"] != CANCELLED
    # The wall-clock gap is untouched by the renumbered notice.
    assert _by_index(_contract(moved, "wall_clock"))[2]["status"] == GAP_SKIPPED


def test_unknown_event_conflicted_notice_and_unknown_person_block_subscription(
    report_path: Path,
) -> None:
    with pytest.raises(ValueError, match="Event identity missing"):
        build_event_series(report_path, SERIES / "wrong-event.json", NOTICE)
    with pytest.raises(ValueError, match="notice does not match"):
        build_event_series(report_path, TERMS, SERIES / "conflicting-notice.json")
    with pytest.raises(ValueError, match="Person identity missing"):
        build_event_series(report_path, SERIES / "unknown-person.json", NOTICE)


def test_cli_event_series_writes_stable_artifact_and_rejects_invalid_sources(
    tmp_path: Path, capsys,
) -> None:
    report = _write_report(SITE, tmp_path / "report.json")
    output = tmp_path / "event-series.json"
    code = main([
        "event-series", "--report", str(report),
        "--terms", str(TERMS), "--notice", str(NOTICE),
        "--output", str(output),
    ])
    assert code == 0
    first_bytes = output.read_bytes()
    assert main([
        "event-series", "--report", str(report),
        "--terms", str(TERMS), "--notice", str(NOTICE),
        "--output", str(output),
    ]) == 0
    assert output.read_bytes() == first_bytes
    captured = capsys.readouterr()
    assert captured.out.count("event-series written") == 2

    # Without --output the table goes to stdout and nothing is written.
    code = main([
        "event-series", "--report", str(report),
        "--terms", str(TERMS), "--notice", str(NOTICE),
    ])
    assert code == 0
    streamed = json.loads(capsys.readouterr().out)
    assert streamed["summary"]["wall_clock"]["subscribable"] == 2
    assert streamed["summary"]["elapsed_168h"]["subscribable"] == 3

    bad_output = tmp_path / "bad.json"
    assert main([
        "event-series", "--report", str(report),
        "--terms", str(SERIES / "wrong-event.json"),
        "--notice", str(NOTICE), "--output", str(bad_output),
    ]) == 2
    assert not bad_output.exists()
    assert "Event identity missing" in capsys.readouterr().err
