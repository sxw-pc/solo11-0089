from __future__ import annotations

import json
from pathlib import Path

from microtag_harvest.config import RunConfig
from microtag_harvest.pipeline import build_result
from microtag_harvest.sponsor_auction_intake import SponsorBid, atomic_preview, event_from_entities


ROOT = Path(__file__).resolve().parents[1]


def test_sponsor_preview_binds_to_harvested_event(tmp_path: Path):
    intake = json.loads((ROOT / "fixtures/sponsor_auction/intake.json").read_text(encoding="utf-8"))
    result = build_result(RunConfig.create(
        input_dir=ROOT / "fixtures/site", contract_path=ROOT / "fixtures/contracts/site.yaml",
        out_dir=tmp_path / "out", now="2026-01-01T00:00:00Z",
    ))
    event = event_from_entities(result.entities, intake["event_id"])
    assert event.identifier == intake["event_id"]
    bids = [SponsorBid(row["bid_id"], row["advertiser"], tuple(row["placements"]), row["amount"])
            for row in intake["bids"]]
    assert atomic_preview(intake["placements"], bids) == {
        "bid_ids": ["b6", "b3", "b4"], "welfare": 24, "first_price_total": 24,
    }


def test_missing_harvested_event_is_rejected(tmp_path: Path):
    result = build_result(RunConfig.create(
        input_dir=ROOT / "fixtures/site", contract_path=ROOT / "fixtures/contracts/site.yaml",
        out_dir=tmp_path / "out", now="2026-01-01T00:00:00Z",
    ))
    try:
        event_from_entities(result.entities, "https://example.invalid/events/missing#event")
    except ValueError as exc:
        assert "found 0" in str(exc)
    else:
        raise AssertionError("missing event was accepted")
