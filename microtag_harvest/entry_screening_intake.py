"""Prior-weighted preview of synthetic Event ticket entry checks."""

from __future__ import annotations

import argparse
import json
from fractions import Fraction
from pathlib import Path

from .config import RunConfig
from .pipeline import build_result


def preview(data: dict, entities: list) -> dict:
    event_id = data["event_id"]
    matches = [entity for entity in entities if entity.identifier == event_id and "Event" in entity.types]
    if len(matches) != 1:
        raise ValueError("entry policy requires exactly one harvested Event")
    scores = {}
    for action in data["checks"]:
        scores[action["check_id"]] = sum(
            Fraction(risk["prior"]) * Fraction(risk["loss"]) * Fraction(action["detection"][risk["risk_id"]])
            for risk in data["risks"]
        )
    best = max(scores, key=lambda name: (scores[name], name))
    return {"event_id": event_id, "check_id": best, "expected_avoided_loss": float(scores[best])}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preview synthetic Event entry checks")
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args(argv)
    data = json.loads(args.fixture.read_text(encoding="utf-8"))
    root = Path(__file__).resolve().parents[1]
    result = build_result(RunConfig.create(
        input_dir=root / "fixtures/site", contract_path=root / "fixtures/contracts/site.yaml",
        out_dir=root / "out", now="2026-01-01T00:00:00Z",
    ))
    print(json.dumps(preview(data, result.entities), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
