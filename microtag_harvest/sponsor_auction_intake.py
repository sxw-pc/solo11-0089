"""Preview sponsor inventory attached to a harvested Event entity.

The current intake books each placement separately. Bundle bids and seller
settlement are deliberately left for the sponsorship release workflow.
"""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
from pathlib import Path
from typing import Iterable

from .config import RunConfig
from .merge import MergedEntity
from .pipeline import build_result


@dataclass(frozen=True)
class SponsorBid:
    bid_id: str
    advertiser: str
    placements: tuple[str, ...]
    amount: int


def event_from_entities(entities: Iterable[MergedEntity], event_id: str) -> MergedEntity:
    matches = [entity for entity in entities if entity.identifier == event_id and "Event" in entity.types]
    if len(matches) != 1:
        raise ValueError(f"expected one harvested Event for {event_id!r}; found {len(matches)}")
    return matches[0]


def atomic_preview(placements: Iterable[str], bids: Iterable[SponsorBid]) -> dict[str, object]:
    """Return the legacy single-placement booking preview."""
    inventory = tuple(placements)
    offers = tuple(bids)
    chosen = []
    used_advertisers: set[str] = set()
    for placement in inventory:
        available = [
            bid for bid in offers
            if bid.placements == (placement,) and bid.advertiser not in used_advertisers
        ]
        if available:
            winner = max(available, key=lambda bid: (bid.amount, bid.bid_id))
            chosen.append(winner)
            used_advertisers.add(winner.advertiser)
    return {
        "bid_ids": [bid.bid_id for bid in chosen],
        "welfare": sum(bid.amount for bid in chosen),
        "first_price_total": sum(bid.amount for bid in chosen),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preview synthetic Event sponsor inventory")
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args(argv)
    data = json.loads(args.fixture.read_text(encoding="utf-8"))
    root = Path(__file__).resolve().parents[1]
    result = build_result(RunConfig.create(
        input_dir=root / "fixtures/site", contract_path=root / "fixtures/contracts/site.yaml",
        out_dir=root / "out", now="2026-01-01T00:00:00Z",
    ))
    event = event_from_entities(result.entities, data["event_id"])
    bids = [SponsorBid(row["bid_id"], row["advertiser"], tuple(row["placements"]), row["amount"])
            for row in data["bids"]]
    print(json.dumps({"event_id": event.identifier, **atomic_preview(data["placements"], bids)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
