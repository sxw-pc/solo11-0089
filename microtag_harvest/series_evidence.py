"""Bounded local Event/Article/Person recurrence evidence intake.

This module validates source identities and contracts; it does not materialize
calendar occurrences or decide announcement effects.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .utils import canonical_json, sha256_hex


def _load(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if not source.is_file() or source.stat().st_size > 1024 * 1024:
        raise ValueError(f"series evidence file unavailable or too large: {source}")
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("series evidence must be an object")
    return value


def _time(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be ISO datetime")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be ISO datetime") from exc
    return result


def _entity(report: dict[str, Any], kind: str, identifier: str) -> dict[str, Any]:
    candidates = [item for item in report.get("entities", [])
                  if item.get("primary_type") == kind and item.get("key") == "@id:" + identifier]
    if len(candidates) != 1 or candidates[0].get("conflicts"):
        raise ValueError(f"{kind} identity missing or conflicted: {identifier}")
    return candidates[0]


def _field(entity: dict[str, Any], name: str) -> dict[str, Any]:
    values = entity.get("fields", {}).get(name, [])
    if len(values) != 1:
        raise ValueError(f"{name} must have one unambiguous source value")
    return values[0]


def load_series_evidence(report_path: str | Path, terms_path: str | Path,
                         notice_path: str | Path) -> dict[str, Any]:
    """Return valid identities, source stamps and recurrence terms, no schedule."""
    report, terms, notice = _load(report_path), _load(terms_path), _load(notice_path)
    if set(terms) != {"version", "event_id", "article_id", "organizer_id", "zone",
                      "local_start", "duration_minutes", "weekly_occurrences", "contracts"} \
            or terms["version"] != 1:
        raise ValueError("series terms fields or version differ")
    if any(not isinstance(terms[key], str) or not terms[key].startswith("https://example.invalid/")
           for key in ("event_id", "article_id", "organizer_id")):
        raise ValueError("series identity must use synthetic local fixture namespace")
    try:
        zone = ZoneInfo(terms["zone"])
    except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
        raise ValueError("series zone is unknown") from exc
    local = _time(terms["local_start"], "local_start")
    if local.tzinfo is not None:
        raise ValueError("local_start must be a local wall time without offset")
    if local.replace(tzinfo=zone).astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != local:
        raise ValueError("series anchor local_start does not exist")
    duration, count = terms["duration_minutes"], terms["weekly_occurrences"]
    if isinstance(duration, bool) or not isinstance(duration, int) or not 1 <= duration <= 1440:
        raise ValueError("duration_minutes invalid")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 16:
        raise ValueError("weekly_occurrences invalid")
    contracts = terms["contracts"]
    if not isinstance(contracts, list) or len(contracts) != 2 or any(
        not isinstance(item, dict) or set(item) != {"id", "promise", "gap"} for item in contracts
    ):
        raise ValueError("series contracts invalid")
    if {item["id"]: (item["promise"], item["gap"]) for item in contracts} != {
        "wall_clock": ("same_local_time", "skip"),
        "elapsed_168h": ("fixed_168_hours", "not_applicable"),
    }:
        raise ValueError("series contract authorization differs")

    event = _entity(report, "Event", terms["event_id"])
    article = _entity(report, "Article", terms["article_id"])
    person = _entity(report, "Person", terms["organizer_id"])
    event_start = _time(_field(event, "startDate")["value"], "Event.startDate")
    if event_start.tzinfo is None or event_start.astimezone(timezone.utc) != \
            local.replace(tzinfo=zone).astimezone(timezone.utc):
        raise ValueError("Event.startDate and series anchor differ")
    if _field(article, "author")["raw"] != terms["organizer_id"]:
        raise ValueError("Article author does not name organizer Person")
    if set(notice) != {"version", "notice_id", "article_id", "event_id", "kind",
                       "occurrence_index", "published_at"} or notice["version"] != 1:
        raise ValueError("series notice fields or version differ")
    if notice["article_id"] != terms["article_id"] or notice["event_id"] != terms["event_id"]:
        raise ValueError("notice does not match Article and Event identities")
    if notice["kind"] != "cancel_occurrence" or notice["notice_id"] != "N100" or \
            isinstance(notice["occurrence_index"], bool) or \
            not isinstance(notice["occurrence_index"], int) or \
            not 1 <= notice["occurrence_index"] <= count:
        raise ValueError("series notice action invalid")
    published = _time(notice["published_at"], "published_at")
    if published.tzinfo is None:
        raise ValueError("notice publication time needs an offset")
    return {"event_id": terms["event_id"], "article_id": terms["article_id"],
            "organizer_id": terms["organizer_id"], "event_start_utc": event_start.isoformat(),
            "terms": terms, "notice": notice,
            "source_fingerprint": sha256_hex(canonical_json([
                event["entity_id"], article["entity_id"], person["entity_id"],
                _field(event, "startDate")["fragment_sha256"],
                _field(article, "author")["fragment_sha256"], terms, notice]))}
