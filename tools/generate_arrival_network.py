"""Generate a synthetic paged two-leg timetable for one extracted Event."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "fixtures/arrival_network"
ZONE = timezone(timedelta(hours=8))
DAY = datetime(2026, 2, 15, tzinfo=ZONE)


def stamp(hour: int, minute: int) -> str:
    return DAY.replace(hour=hour, minute=minute).isoformat()


def generate() -> None:
    pages = []
    for page in range(32):
        routes = []
        for position in range(32):
            first_arrive = stamp(9, 55)
            second_depart = stamp(10, 0)
            second_arrive = stamp(10, 20 + min(page, 31))
            accessible = page >= 2
            if page == 0 and position == 0:
                first_arrive, second_depart, second_arrive = (
                    stamp(9, 30), stamp(9, 20), stamp(9, 25))
            if page == 1 and position == 0:
                first_arrive, second_depart, second_arrive = (
                    stamp(9, 10), stamp(9, 15), stamp(9, 32))
            if page == 2 and position == 0:
                first_arrive, second_depart, second_arrive = (
                    stamp(9, 30), stamp(9, 40), stamp(10, 5))
            routes.append({
                "route_id": f"route-{page:02d}-{position:02d}",
                "accessible": accessible,
                "first": {"from": "Central Stop", "to": "Hub",
                          "depart": stamp(9, 0), "arrive": first_arrive},
                "second": {"from": "Hub", "to": "Sample Hall",
                           "depart": second_depart, "arrive": second_arrive},
            })
        path = f"pages/{page:02d}.json"
        target = ROOT / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(routes, sort_keys=True, separators=(",", ":")) + "\n",
                          encoding="utf-8")
        general = min(route["second"]["arrive"] for route in routes)
        step_free = min((route["second"]["arrive"] for route in routes
                         if route["accessible"]), default=None)
        pages.append({"page": page, "path": path,
                      "general_lower_bound_arrive": general,
                      "step_free_lower_bound_arrive": step_free})
    manifest = {
        "event_id": "https://example.invalid/events/arrival#event",
        "origin": "Central Stop", "venue": "Sample Hall",
        "minimum_transfer_minutes": 5,
        "max_page_fetches": 2,
        "contracts": ["general_access", "step_free_access"],
        "pages": pages,
    }
    (ROOT / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8")


if __name__ == "__main__":
    generate()
