"""Validate paged route evidence while leaving earliest-arrival proof open."""

from __future__ import annotations

import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from .arrival_assist import scheduled_arrivals
from .merge import MergedEntity


class TimetablePages:
    def __init__(self, root: Path, limit: int):
        self.root = root
        self.limit = limit
        self.fetches = 0
        self.manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self._cache: dict[int, list[dict[str, Any]]] = {}

    def fetch(self, page: int) -> list[dict[str, Any]]:
        if page in self._cache:
            return self._cache[page]
        if self.fetches >= self.limit:
            raise ValueError("timetable page budget exceeded")
        metadata = next((item for item in self.manifest["pages"] if item["page"] == page), None)
        if metadata is None:
            raise ValueError("unknown timetable page")
        page_bytes = (self.root / metadata["path"]).read_bytes()
        if hashlib.sha256(page_bytes).hexdigest() != metadata.get("sha256"):
            raise ValueError("timetable page digest mismatch")
        routes = json.loads(page_bytes.decode("utf-8"))
        self._cache[page] = routes
        self.fetches += 1
        return routes


def inspect_arrival_network(event: MergedEntity, root: Path) -> dict[str, Any]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    origin, venue = manifest.get("origin"), manifest.get("venue")
    bound = scheduled_arrivals(event, {"origin": origin, "venue": venue, "services": []})
    if manifest.get("event_id") != bound["event_id"]:
        raise ValueError("timetable Event identity mismatch")
    if (manifest.get("max_page_fetches") != 2
            or manifest.get("minimum_transfer_minutes") != 5
            or manifest.get("contracts") != ["general_access", "step_free_access"]):
        raise ValueError("invalid timetable decision contract")
    pages = manifest.get("pages")
    if not isinstance(pages, list) or len(pages) != 32:
        raise ValueError("timetable needs 32 pages")
    seen_routes: set[str] = set()
    for index, metadata in enumerate(pages):
        if metadata.get("page") != index or metadata.get("path") != f"pages/{index:02d}.json":
            raise ValueError("timetable page directory is not canonical")
        page_bytes = (root / metadata["path"]).read_bytes()
        if hashlib.sha256(page_bytes).hexdigest() != metadata.get("sha256"):
            raise ValueError("timetable page digest mismatch")
        routes = json.loads(page_bytes.decode("utf-8"))
        if not isinstance(routes, list) or len(routes) != 32:
            raise ValueError("timetable page needs 32 routes")
        general: list[str] = []
        accessible: list[str] = []
        for route in routes:
            if route.get("route_id") in seen_routes:
                raise ValueError("duplicate route identity")
            seen_routes.add(route["route_id"])
            first, second = route["first"], route["second"]
            if (first.get("from") != origin or first.get("to") != "Hub"
                    or second.get("from") != "Hub" or second.get("to") != venue):
                raise ValueError("route endpoints differ from event timetable")
            times = [datetime.fromisoformat(value)
                     for value in (first["depart"], first["arrive"],
                                   second["depart"], second["arrive"])]
            if any(value.utcoffset() is None for value in times):
                raise ValueError("route must use offset-aware times")
            general.append(second["arrive"])
            if route.get("accessible") is True:
                accessible.append(second["arrive"])
        if metadata.get("general_lower_bound_arrive") > min(general):
            raise ValueError("invalid general arrival lower bound")
        if accessible and metadata.get("step_free_lower_bound_arrive") > min(accessible):
            raise ValueError("invalid accessible arrival lower bound")
        if not accessible and metadata.get("step_free_lower_bound_arrive") is not None:
            raise ValueError("inaccessible page cannot advertise accessible routes")
    return {"event_id": bound["event_id"], "event_start": bound["event_start"],
            "route_count": len(seen_routes), "page_count": len(pages),
            "max_page_fetches": 2, "earliest_arrival_proof": None}
