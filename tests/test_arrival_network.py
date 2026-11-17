from pathlib import Path

import pytest

from microtag_harvest.arrival_network import TimetablePages, inspect_arrival_network
from microtag_harvest.config import RunConfig
from microtag_harvest.pipeline import build_result


ROOT = Path(__file__).resolve().parents[1]


def _event(tmp_path):
    config = RunConfig.create(ROOT / "fixtures/arrival",
                              ROOT / "fixtures/contracts/site.yaml",
                              tmp_path / "out", now="2026-02-15T00:00:00Z")
    result = build_result(config)
    return next(entity for entity in result.entities if entity.primary_type == "Event")


def test_paged_timetable_is_bound_to_real_event(tmp_path):
    actual = inspect_arrival_network(_event(tmp_path), ROOT / "fixtures/arrival_network")
    assert actual["route_count"] == 1024
    assert actual["page_count"] == 32
    assert actual["max_page_fetches"] == 2
    assert actual["earliest_arrival_proof"] is None


def test_reader_enforces_two_distinct_pages():
    reader = TimetablePages(ROOT / "fixtures/arrival_network", 2)
    assert len(reader.fetch(0)) == 32
    assert len(reader.fetch(0)) == 32
    assert len(reader.fetch(1)) == 32
    assert reader.fetches == 2
    with pytest.raises(ValueError, match="budget exceeded"):
        reader.fetch(2)
