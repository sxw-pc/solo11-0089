"""Synthetic Event attendance disclosure intake.

Two layers live here:

* :func:`naive_preview` reproduces the legacy "mask small cells, publish every
  margin" preview.  It is retained strictly as a *leak diagnostic*: every
  response it produces is explicitly marked unsafe and shows how a masked cell
  can be reconstructed from the published margins.  It must never be treated
  as a published statistics product.
* :func:`build_intake_report` binds the intake to the single harvested Event
  verified by the collector and produces the publishable, privacy-checked
  disclosures from :mod:`microtag_harvest.attendance_disclosure`.

All attendance numbers and categories are synthetic drill data.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .attendance_disclosure import (
    DEFAULT_CELL_CAP,
    DisclosureError,
    UnsafeDisclosureError,
    build_safe_disclosures,
    cell_id,
    table_from_payload,
)
from .config import RunConfig
from .pipeline import build_result

UNSAFE_NAIVE_PREVIEW = "unsafe_naive_preview_do_not_publish"


class EventIdentityError(ValueError):
    """Raised when the intake cannot be bound to exactly one harvested Event."""


def verify_event(data: dict[str, Any], entities: list) -> dict[str, Any]:
    """Bind the intake payload to exactly one harvested Event entity."""

    event_id = data.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        raise EventIdentityError("intake payload requires a non-empty event_id")
    matches = [
        entity
        for entity in entities
        if entity.identifier == event_id and "Event" in entity.types
    ]
    if len(matches) != 1:
        raise EventIdentityError(
            f"attendance disclosure requires exactly one harvested Event "
            f"matching {event_id!r}; found {len(matches)}"
        )
    event = matches[0]
    return {
        "identifier": event.identifier,
        "types": list(event.types),
        "sources": event.source_records(),
    }


def naive_preview(data: dict[str, Any]) -> dict[str, Any]:
    """Legacy small-cell mask preview, annotated with the leak it contains."""

    rows = data["rows"]
    columns = data["columns"]
    counts = data["counts"]
    threshold = data["mask_below"]
    row_totals = {
        row: sum(counts[row][column] for column in columns) for row in rows
    }
    column_totals = {
        column: sum(counts[row][column] for row in rows) for column in columns
    }
    masked = [
        cell_id(row, column)
        for row in rows
        for column in columns
        if counts[row][column] < threshold
    ]
    visible_cells = {
        cell_id(row, column): counts[row][column]
        for row in rows
        for column in columns
        if counts[row][column] >= threshold
    }
    # Demonstrate the reconstruction rather than leaving it implicit.
    reconstructions: list[dict[str, Any]] = []
    for row in rows:
        for column in columns:
            label = cell_id(row, column)
            if label not in masked:
                continue
            via_row = row_totals[row] - sum(
                counts[row][other] for other in columns if other != column
            )
            via_column = column_totals[column] - sum(
                counts[other][column] for other in rows if other != row
            )
            reconstructions.append(
                {
                    "cell": label,
                    "true_value": counts[row][column],
                    "reconstructed_from_row_total": via_row,
                    "reconstructed_from_column_total": via_column,
                    "exact_reconstruction_possible": (
                        via_row == counts[row][column]
                        and via_column == counts[row][column]
                    ),
                }
            )
    return {
        "status": UNSAFE_NAIVE_PREVIEW,
        "safe_to_publish": False,
        "event_id": data["event_id"],
        "visible_cells": visible_cells,
        "masked_cells": masked,
        "row_totals": row_totals,
        "column_totals": column_totals,
        "leak_demonstrations": reconstructions,
    }


def build_intake_report(
    data: dict[str, Any],
    entities: list,
    *,
    cap: int = DEFAULT_CELL_CAP,
) -> dict[str, Any]:
    """Verify the Event, then build the full privacy-checked disclosure report."""

    event = verify_event(data, entities)
    table = table_from_payload(data, cap=cap)
    contract_names = list(data.get("contracts", {}).keys())
    disclosures = build_safe_disclosures(table, contract_names)
    return {
        "synthetic_data_notice": (
            "All attendance counts and categories are synthetic drill data."
        ),
        "event": event,
        "source_fingerprint": disclosures["source_fingerprint"],
        "grid_shape": disclosures["grid_shape"],
        "mask_below": disclosures["mask_below"],
        "cell_cap": disclosures["cell_cap"],
        "sensitive_cells": disclosures["sensitive_cells"],
        "publications": disclosures["publications"],
        "refusals": disclosures["refusals"],
        UNSAFE_NAIVE_PREVIEW: naive_preview(data),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build privacy-checked synthetic Event attendance disclosures"
    )
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument(
        "--cap",
        type=int,
        default=DEFAULT_CELL_CAP,
        help="inclusive upper bound applied to every cell (default: 20)",
    )
    args = parser.parse_args(argv)
    data = json.loads(args.fixture.read_text(encoding="utf-8"))
    root = Path(__file__).resolve().parents[1]
    result = build_result(RunConfig.create(
        input_dir=root / "fixtures/site",
        contract_path=root / "fixtures/contracts/site.yaml",
        out_dir=root / "out",
        now="2026-01-01T00:00:00Z",
    ))
    try:
        report = build_intake_report(data, result.entities, cap=args.cap)
    except (EventIdentityError, DisclosureError, UnsafeDisclosureError) as exc:
        error_report = {
            "status": "refused",
            "safe_to_publish": False,
            "event_id": data.get("event_id"),
            "reason": str(exc),
            "error_type": type(exc).__name__,
        }
        print(json.dumps(error_report, sort_keys=True))
        return 2

    print(json.dumps(report, sort_keys=True))
    return 0 if report["publications"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
