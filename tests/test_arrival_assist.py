import json
from pathlib import Path

from microtag_harvest.arrival_assist import scheduled_arrivals
from microtag_harvest.config import RunConfig
from microtag_harvest.pipeline import build_result


ROOT = Path(__file__).resolve().parents[1]


def test_extracted_event_drives_offline_arrival_options(tmp_path):
    config = RunConfig.create(
        ROOT / "fixtures" / "arrival", ROOT / "fixtures" / "contracts" / "site.yaml",
        tmp_path / "out", now="2026-02-15T00:00:00Z",
    )
    result = build_result(config)
    event = next(entity for entity in result.entities if entity.primary_type == "Event")
    timetable = json.loads((ROOT / "fixtures" / "arrival" / "routes.json").read_text())
    arrival = scheduled_arrivals(event, timetable)
    assert arrival["event_start"] == "2026-02-15T02:00:00+00:00"
    assert arrival["venue"] == "Sample Hall"
    assert [(service["id"], service["scheduled_margin_minutes"])
            for service in arrival["services"]] == [("H", 20), ("D", 10)]
    assert all(service["scheduled_on_time"] for service in arrival["services"])
    assert arrival["estimate_kind"] == "schedule_only"


def test_reliability_fixture_is_a_complete_two_state_signal_case():
    fixture = json.loads((ROOT / "fixtures" / "arrival" / "reliability.json").read_text())
    assert sum(fixture["state_prior"].values()) == 1
    assert all(sum(probs.values()) == 1 for probs in fixture["signal_likelihood"].values())
    assert set(fixture["actual_arrivals"]) == {"H", "D"}
    assert fixture["tickets"]["fixed_H"]["allowed_services"] == ["H"]
    assert fixture["tickets"]["flexible"]["allowed_services"] == ["H", "D"]
