from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

from microtag_harvest.config import RunConfig
from microtag_harvest.entry_screening_intake import preview
from microtag_harvest.pipeline import build_result


ROOT = Path(__file__).resolve().parents[1]


def test_prior_preview_lacks_expiry_cover(tmp_path: Path):
    data = json.loads((ROOT / "fixtures/entry_screening/intake.json").read_text(encoding="utf-8"))
    result = build_result(RunConfig.create(
        input_dir=ROOT / "fixtures/site", contract_path=ROOT / "fixtures/contracts/site.yaml",
        out_dir=tmp_path / "out", now="2026-01-01T00:00:00Z",
    ))
    assert preview(data, result.entities) == {
        "event_id": data["event_id"], "check_id": "dual-quick", "expected_avoided_loss": 6.225,
    }
    assert Fraction(data["risks"][2]["loss"]) * (1 - Fraction(data["checks"][3]["detection"]["expired-pass"])) == 8


def test_exact_minimax_primal_and_adversary_witness():
    data = json.loads((ROOT / "fixtures/entry_screening/intake.json").read_text(encoding="utf-8"))
    witness = json.loads((ROOT / "fixtures/entry_screening/witness.json").read_text(encoding="utf-8"))
    policy = {k: Fraction(v) for k, v in witness["deterrence_policy"].items()}
    adversary = {k: Fraction(v) for k, v in witness["adversary_policy"].items()}
    value = Fraction(witness["optimal_worst_loss"])
    assert sum(policy.values()) == sum(adversary.values()) == 1
    residual = {
        (action["check_id"], risk["risk_id"]):
        Fraction(risk["loss"]) * (1 - Fraction(action["detection"][risk["risk_id"]]))
        for action in data["checks"] for risk in data["risks"]
    }
    assert max(sum(policy[a] * residual[a, r] for a in policy) for r in adversary) == value
    assert min(sum(adversary[r] * residual[a, r] for r in adversary) for a in policy) == value
    assert value < 6 < 8
