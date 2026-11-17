"""Preview a synthetic Event attendance grid with naive small-cell masking."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import RunConfig
from .pipeline import build_result


def naive_preview(data: dict, entities: list) -> dict:
    event_id = data["event_id"]
    matches = [entity for entity in entities if entity.identifier == event_id and "Event" in entity.types]
    if len(matches) != 1:
        raise ValueError("attendance grid requires exactly one harvested Event")
    rows = data["rows"]
    columns = data["columns"]
    counts = data["counts"]
    threshold = data["mask_below"]
    return {
        "event_id": event_id,
        "visible_cells": {
            f"{row}:{column}": counts[row][column]
            for row in rows for column in columns if counts[row][column] >= threshold
        },
        "masked_cells": [f"{row}:{column}" for row in rows for column in columns
                         if counts[row][column] < threshold],
        "row_totals": {row: sum(counts[row][column] for column in columns) for row in rows},
        "column_totals": {column: sum(counts[row][column] for row in rows) for column in columns},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preview synthetic Event attendance disclosure")
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args(argv)
    data = json.loads(args.fixture.read_text(encoding="utf-8"))
    root = Path(__file__).resolve().parents[1]
    result = build_result(RunConfig.create(
        input_dir=root / "fixtures/site", contract_path=root / "fixtures/contracts/site.yaml",
        out_dir=root / "out", now="2026-01-01T00:00:00Z",
    ))
    print(json.dumps(naive_preview(data, result.entities), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
