"""Descriptive route choices for an extracted event and offline timetable."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .merge import MergedEntity


def _field(entity: MergedEntity, name: str) -> Any:
    values = entity.fields.get(name, [])
    if len(values) != 1:
        raise ValueError(f"event needs one {name} value")
    return values[0].value


def scheduled_arrivals(event: MergedEntity, timetable: dict[str, Any]) -> dict[str, Any]:
    """Show timetable arrival before event start, without reliability promise."""
    if event.primary_type != "Event":
        raise ValueError("arrival assistant needs an Event entity")
    start = datetime.fromisoformat(str(_field(event, "startDate")).replace("Z", "+00:00"))
    location = _field(event, "location")
    if not isinstance(location, dict) or location.get("name") != timetable.get("venue"):
        raise ValueError("route venue does not match event location")
    services = []
    for row in timetable["services"]:
        depart = datetime.fromisoformat(row["depart"])
        arrive = datetime.fromisoformat(row["scheduled_arrive"])
        if depart.tzinfo is None or arrive.tzinfo is None or start.tzinfo is None:
            raise ValueError("timetable and event times require offsets")
        services.append({
            "id": row["id"], "fare_cents": int(row["fare_cents"]),
            "scheduled_on_time": arrive <= start,
            "scheduled_margin_minutes": int((start - arrive).total_seconds() // 60),
        })
    return {
        "event_id": event.identifier, "venue": timetable["venue"],
        "event_start": start.isoformat(), "origin": timetable["origin"],
        "services": services, "estimate_kind": "schedule_only",
    }
