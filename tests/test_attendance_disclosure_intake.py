from __future__ import annotations

import json
from pathlib import Path

from microtag_harvest.attendance_disclosure_intake import naive_preview
from microtag_harvest.config import RunConfig
from microtag_harvest.pipeline import build_result


ROOT = Path(__file__).resolve().parents[1]


def test_naive_mask_reconstructs_small_cell(tmp_path: Path):
    data = json.loads((ROOT / "fixtures/attendance_disclosure/intake.json").read_text(encoding="utf-8"))
    result = build_result(RunConfig.create(
        input_dir=ROOT / "fixtures/site", contract_path=ROOT / "fixtures/contracts/site.yaml",
        out_dir=tmp_path / "out", now="2026-01-01T00:00:00Z",
    ))
    report = naive_preview(data, result.entities)
    assert report["masked_cells"] == ["local:morning"]
    assert report["row_totals"] == {"local": 13, "visiting": 19}
    assert report["column_totals"] == {"morning": 9, "afternoon": 10, "evening": 13}
    assert report["row_totals"]["local"] - report["visible_cells"]["local:afternoon"] - report["visible_cells"]["local:evening"] == 2
    assert report["column_totals"]["morning"] - report["visible_cells"]["visiting:morning"] == 2


def test_alternative_grids_preserve_margins_but_change_sensitive_count():
    data = json.loads((ROOT / "fixtures/attendance_disclosure/intake.json").read_text(encoding="utf-8"))
    witness = json.loads((ROOT / "fixtures/attendance_disclosure/witness.json").read_text(encoding="utf-8"))
    originals = [data["counts"], *witness["alternative_grids"]]
    assert {grid["local"]["morning"] for grid in originals} == {1, 2, 3}
    for grid in originals:
        assert [sum(grid[row][col] for col in data["columns"]) for row in data["rows"]] == [13, 19]
        assert [sum(grid[row][col] for row in data["rows"]) for col in data["columns"]] == [9, 10, 13]
        assert grid["local"]["evening"] == 5 and grid["visiting"]["evening"] == 8
