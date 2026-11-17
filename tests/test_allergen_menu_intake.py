from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

from microtag_harvest.allergen_menu_intake import single_assay_preview
from microtag_harvest.config import RunConfig
from microtag_harvest.pipeline import build_result


ROOT = Path(__file__).resolve().parents[1]


def _fixture():
    return json.loads((ROOT / "fixtures/allergen_menu/intake.json").read_text(encoding="utf-8"))


def test_greedy_assay_preview_is_event_bound(tmp_path: Path):
    data = _fixture()
    result = build_result(RunConfig.create(
        input_dir=ROOT / "fixtures/site", contract_path=ROOT / "fixtures/contracts/site.yaml",
        out_dir=tmp_path / "out", now="2026-01-01T00:00:00Z",
    ))
    assert single_assay_preview(data, result.entities)["first_assay_lot"] == "E"
    assert sum(world["prior_weight"] for world in data["possible_worlds"]) == 48


def test_witness_policies_release_only_dishes_safe_in_every_remaining_world():
    data = _fixture()
    witness = json.loads((ROOT / "fixtures/allergen_menu/witness.json").read_text(encoding="utf-8"))
    worlds = data["possible_worlds"]
    values = {dish["lot_id"]: dish["service_value"] for dish in data["dishes"]}
    for contract, policy in witness.items():
        leaves = {}
        for world in worlds:
            first = policy["first_assay"]
            first_result = first in world["contaminated_lots"]
            second = policy["second_if_contaminated"] if first_result else policy["second_if_clean"]
            second_result = second in world["contaminated_lots"]
            key = (first_result, second_result)
            compatible = [row for row in worlds if (first in row["contaminated_lots"], second in row["contaminated_lots"]) == key]
            safe = [lot for lot in values if all(lot not in row["contaminated_lots"] for row in compatible)]
            leaves[world["world_id"]] = sum(values[lot] for lot in safe)
        if contract == "worst_menu":
            assert min(leaves.values()) == policy["minimum_safe_value"] == 12
        else:
            average = sum(world["prior_weight"] * leaves[world["world_id"]] for world in worlds) / 48
            assert Fraction(average).limit_denominator() == Fraction(policy["prior_expected_safe_value"])
