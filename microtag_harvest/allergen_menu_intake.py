"""Single-assay preview for a synthetic Event catering menu."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import RunConfig
from .pipeline import build_result


def single_assay_preview(data: dict, entities: list) -> dict:
    event_id = data["event_id"]
    matches = [entity for entity in entities if entity.identifier == event_id and "Event" in entity.types]
    if len(matches) != 1:
        raise ValueError("menu assay requires exactly one harvested Event")
    dishes = data["dishes"]
    pick = max(dishes, key=lambda dish: (dish["service_value"], dish["lot_id"]))
    return {"event_id": event_id, "first_assay_lot": pick["lot_id"],
            "reason": "largest individual dish service value"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preview a synthetic Event catering assay")
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args(argv)
    data = json.loads(args.fixture.read_text(encoding="utf-8"))
    root = Path(__file__).resolve().parents[1]
    result = build_result(RunConfig.create(
        input_dir=root / "fixtures/site", contract_path=root / "fixtures/contracts/site.yaml",
        out_dir=root / "out", now="2026-01-01T00:00:00Z",
    ))
    print(json.dumps(single_assay_preview(data, result.entities), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
