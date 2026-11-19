"""Materialize the bounded Event occurrence table from validated series evidence.

This is a calendar *consumption* layer. It never extracts entities and never
re-verifies the harvest contract itself; instead it re-runs
:func:`series_evidence.load_series_evidence` so that every occurrence is backed
by the same identity, anchor and notice checks. Two explicit time promises are
evaluated independently and are never substituted for one another:

* ``wall_clock`` (``same_local_time`` + ``gap: skip``): every logical index is
  scheduled at the same local wall time. A local time that does not exist
  (a spring-forward gap) never becomes a real instant; the logical index is
  recorded as ``gap_skipped`` without a UTC instant.
* ``elapsed_168h`` (``fixed_168_hours``): every index is exactly 168 hours after
  the first real UTC instant, then rendered in the series zone for local
  display, even when DST changes the displayed local clock time.

The cancellation notice binds to a *logical* ``occurrence_index``, so it lands
on the same index under both contracts regardless of their differing UTC
instants or displayed local times.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .series_evidence import _entity, _field, load_series_evidence
from .utils import canonical_json, sha256_hex

SCHEMA_VERSION = 1
WEEK_HOURS = 168

#: A real instant a subscriber can book.
SUBSCRIBABLE = "subscribable"
#: The promised local wall time does not exist on that date (DST gap); skipped
#: by contract, never turned into a fabricated UTC instant.
GAP_SKIPPED = "gap_skipped"
#: A real instant revoked by the validated cancellation notice at this index.
CANCELLED = "cancelled"


def _parse_iso(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be ISO datetime")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be ISO datetime") from exc


def _local_exists(naive_local: datetime, zone: ZoneInfo) -> bool:
    """True when the naive wall time names at least one real instant in zone."""
    aware = naive_local.replace(tzinfo=zone)
    roundtrip = (
        aware.astimezone(timezone.utc)
        .astimezone(zone)
        .replace(tzinfo=None)
    )
    return roundtrip == naive_local


def _iso(value: datetime) -> str:
    return value.isoformat()


def _provenance(report: dict[str, Any], kind: str, identifier: str,
                field_name: str) -> dict[str, Any]:
    """Bind one occurrence back to the harvested entity and source fragment."""
    entity = _entity(report, kind, identifier)
    field = _field(entity, field_name)
    return {
        "entity_id": entity["entity_id"],
        "key": entity["key"],
        "primary_type": kind,
        "field": field_name,
        "document": field["document"],
        "annotation_id": field["annotation_id"],
        "fragment_sha256": field["fragment_sha256"],
    }


def _occurrence_id(event_id: str, contract_id: str, index: int) -> str:
    digest = sha256_hex(canonical_json([event_id, contract_id, index]))
    return f"occ-{digest[:16]}"


def _sources(report: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "event": _provenance(report, "Event", evidence["event_id"], "startDate"),
        "article": _provenance(report, "Article", evidence["article_id"], "author"),
        "person": _provenance(report, "Person", evidence["organizer_id"], "name"),
    }


def _base_record(contract_id: str, index: int, evidence: dict[str, Any],
                 sources: dict[str, Any]) -> dict[str, Any]:
    event_id = evidence["event_id"]
    return {
        "occurrence_id": _occurrence_id(event_id, contract_id, index),
        "occurrence_index": index,
        "contract_id": contract_id,
        "event_id": event_id,
        "article_id": evidence["article_id"],
        "organizer_id": evidence["organizer_id"],
        "sources": sources,
    }


def _finalize(record: dict[str, Any]) -> dict[str, Any]:
    record["fingerprint"] = sha256_hex(canonical_json(record))
    return record


def _wall_clock_table(evidence: dict[str, Any], zone: ZoneInfo,
                      sources: dict[str, Any],
                      cancel_index: int) -> list[dict[str, Any]]:
    terms = evidence["terms"]
    local_anchor = _parse_iso(terms["local_start"], "local_start")
    count = terms["weekly_occurrences"]
    table: list[dict[str, Any]] = []
    for index in range(1, count + 1):
        scheduled_local = local_anchor + timedelta(weeks=index - 1)
        record = _base_record("wall_clock", index, evidence, sources)
        record["scheduled_local"] = _iso(scheduled_local)
        record["status_source"] = "contract:wall_clock:same_local_time"
        if not _local_exists(scheduled_local, zone):
            # The gap clause says skip: no UTC instant may be fabricated for a
            # wall time the zone never reaches.
            record.update({
                "status": GAP_SKIPPED,
                "local_time": None,
                "utc_instant": None,
                "utc_offset_minutes": None,
                "status_reason": "local_wall_time_does_not_exist_gap_skip",
                "notice": None,
            })
        else:
            instant = scheduled_local.replace(tzinfo=zone)
            local_time = instant.astimezone(zone)
            if index == cancel_index:
                record.update({
                    "status": CANCELLED,
                    "status_source": f"notice:{evidence['notice']['notice_id']}",
                    "status_reason": "cancelled_by_notice_occurrence_index",
                    "notice": {
                        "notice_id": evidence["notice"]["notice_id"],
                        "occurrence_index": cancel_index,
                        "published_at": evidence["notice"]["published_at"],
                    },
                })
            else:
                record.update({
                    "status": SUBSCRIBABLE,
                    "status_reason": "promised_local_time_exists",
                    "notice": None,
                })
            record.update({
                "local_time": _iso(local_time),
                "utc_instant": _iso(instant.astimezone(timezone.utc)),
                "utc_offset_minutes": int(instant.utcoffset().total_seconds() // 60),
            })
        table.append(_finalize(record))
    return table


def _elapsed_table(evidence: dict[str, Any], zone: ZoneInfo,
                   anchor_utc: datetime, sources: dict[str, Any],
                   cancel_index: int) -> list[dict[str, Any]]:
    count = evidence["terms"]["weekly_occurrences"]
    table: list[dict[str, Any]] = []
    for index in range(1, count + 1):
        instant_utc = anchor_utc + timedelta(hours=WEEK_HOURS * (index - 1))
        local_time = instant_utc.astimezone(zone)
        record = _base_record("elapsed_168h", index, evidence, sources)
        record.update({
            "scheduled_local": _iso(local_time.replace(tzinfo=None)),
            "local_time": _iso(local_time),
            "utc_instant": _iso(instant_utc),
            "utc_offset_minutes": int(local_time.utcoffset().total_seconds() // 60),
            "status_source": "contract:elapsed_168h:fixed_168_hours",
            "notice": None,
        })
        if index == cancel_index:
            record.update({
                "status": CANCELLED,
                "status_source": f"notice:{evidence['notice']['notice_id']}",
                "status_reason": "cancelled_by_notice_occurrence_index",
                "notice": {
                    "notice_id": evidence["notice"]["notice_id"],
                    "occurrence_index": cancel_index,
                    "published_at": evidence["notice"]["published_at"],
                },
            })
        else:
            record.update({
                "status": SUBSCRIBABLE,
                "status_reason": "fixed_168_hour_elapsed_instant",
            })
        table.append(_finalize(record))
    return table


def build_event_series(report_path: str | Path, terms_path: str | Path,
                       notice_path: str | Path) -> dict[str, Any]:
    """Return the per-contract occurrence table for one validated Event series.

    Raises ``ValueError`` (via the series-evidence intake) whenever the report,
    terms or notice fail identity, anchor or notice checks; no subscribable
    occurrence is produced for unknown/conflicted sources.
    """
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("report must be an object")
    # Re-run every identity/anchor/notice check before materializing anything.
    evidence = load_series_evidence(report_path, terms_path, notice_path)

    terms = evidence["terms"]
    zone = ZoneInfo(terms["zone"])
    anchor_utc = _parse_iso(evidence["event_start_utc"], "event_start_utc")
    if anchor_utc.tzinfo is None:
        raise ValueError("event_start_utc must carry an offset")
    anchor_utc = anchor_utc.astimezone(timezone.utc)
    anchor_local = anchor_utc.astimezone(zone)
    cancel_index = evidence["notice"]["occurrence_index"]
    sources = _sources(report, evidence)

    tables = {
        "wall_clock": _wall_clock_table(
            evidence, zone, sources, cancel_index),
        "elapsed_168h": _elapsed_table(
            evidence, zone, anchor_utc, sources, cancel_index),
    }
    summary = {
        contract_id: {
            "total_logical_occurrences": len(rows),
            "subscribable": sum(row["status"] == SUBSCRIBABLE for row in rows),
            "gap_skipped": sum(row["status"] == GAP_SKIPPED for row in rows),
            "cancelled": sum(row["status"] == CANCELLED for row in rows),
        }
        for contract_id, rows in tables.items()
    }
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "event_id": evidence["event_id"],
        "article_id": evidence["article_id"],
        "organizer_id": evidence["organizer_id"],
        "zone": terms["zone"],
        "duration_minutes": terms["duration_minutes"],
        "weekly_occurrences": terms["weekly_occurrences"],
        "anchor": {
            "local_start": terms["local_start"],
            "event_start_utc": evidence["event_start_utc"],
            "anchor_local_time": _iso(anchor_local),
        },
        "notice": {
            "notice_id": evidence["notice"]["notice_id"],
            "kind": evidence["notice"]["kind"],
            "occurrence_index": cancel_index,
            "published_at": evidence["notice"]["published_at"],
        },
        "contracts": [
            {
                "id": contract["id"],
                "promise": contract["promise"],
                "gap": contract["gap"],
                "occurrences": tables[contract["id"]],
            }
            for contract in terms["contracts"]
        ],
        "summary": summary,
        "sources": sources,
        "source_fingerprint": evidence["source_fingerprint"],
    }
    result["series_fingerprint"] = sha256_hex(canonical_json({
        key: value for key, value in result.items()
        if key not in ("series_fingerprint",)
    }))
    return result
