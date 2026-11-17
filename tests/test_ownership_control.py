from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from microtag_harvest.config import RunConfig
from microtag_harvest.ownership_control import inspect_ownership
from microtag_harvest.pipeline import build_result


ROOT = Path(__file__).resolve().parents[1]


def _harvest(tmp_path: Path):
    config = RunConfig.create(ROOT / "fixtures/ownership",
                              ROOT / "fixtures/contracts/site.yaml",
                              tmp_path / "out", now="2026-02-15T00:00:00Z")
    return build_result(config)


def test_ownership_uses_real_harvested_people_and_organizations(tmp_path: Path):
    result = _harvest(tmp_path)
    intake = inspect_ownership(result, ROOT / "fixtures/ownership/holdings.json")
    assert len(intake.people) == 2
    assert len(intake.organizations) == 5
    assert len(intake.holdings) == 9
    assert intake.control_decision is None
    assert set(intake.contract_ids) == {"economic_entitlement", "proxy_vote_entitlement"}
    target_edges = [edge for edge in intake.holdings if edge.company == intake.target]
    assert sum((edge.economic_share for edge in target_edges), Decimal(0)) == 1
    assert sum((edge.proxy_vote_share for edge in target_edges), Decimal(0)) == 1


def test_ownership_rejects_unharvested_owner(tmp_path: Path):
    result = _harvest(tmp_path)
    raw = json.loads((ROOT / "fixtures/ownership/holdings.json").read_text())
    raw["declarations"][0]["owner"] = "https://example.invalid/missing"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="unharvested"):
        inspect_ownership(result, path)


def test_ownership_rejects_unbalanced_rights(tmp_path: Path):
    result = _harvest(tmp_path)
    raw = json.loads((ROOT / "fixtures/ownership/holdings.json").read_text())
    raw["declarations"][0]["economic_share"] = 0.56
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="total one"):
        inspect_ownership(result, path)
