"""Synthetic Event ticket entry screening: prior preview and randomized policy issuance."""

from __future__ import annotations

import argparse
import json
import sys
from fractions import Fraction
from pathlib import Path

from .config import RunConfig
from .entry_policy import EntryPolicyError, issue_policies
from .pipeline import build_result


def preview(data: dict, entities: list) -> dict:
    """Prior-weighted single-check preview (kept for backward compatibility).

    This deliberately reports only the prior-average score of one pure check;
    it cannot promise a worst-case bound because, for example, ``dual-quick``
    has zero detection against ``expired-pass``.  Use :func:`issue_policies`
    for the ex-ante randomized policy and its optimality certificates.
    """
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
    parser = argparse.ArgumentParser(
        description="Issue randomized synthetic Event entry-check policies"
    )
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.fixture.read_text(encoding="utf-8"))
        root = Path(__file__).resolve().parents[1]
        result = build_result(RunConfig.create(
            input_dir=root / "fixtures/site", contract_path=root / "fixtures/contracts/site.yaml",
            out_dir=root / "out", now="2026-01-01T00:00:00Z",
        ))
        # The collector-side entity identity and the screening-side action matrix
        # stay on one input chain: entities flow straight from build_result into
        # issuance, and every certificate is fingerprinted to this chain.
        report = issue_policies(data, result.entities)
        report["prior_preview"] = preview(data, result.entities)
    except (EntryPolicyError, ValueError, json.JSONDecodeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
