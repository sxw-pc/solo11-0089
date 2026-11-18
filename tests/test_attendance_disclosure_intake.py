"""CLI and Event-identity tests for the safe attendance disclosure intake.

The legacy naive preview is kept only as a leak diagnostic; these tests pin
down the exact 13-6-5 / 9-7 reconstruction it leaks and prove the new safe
publications close that reconstruction path.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from microtag_harvest.attendance_disclosure import (
    CONTRACT_DETAIL_PRIORITY,
    CONTRACT_MARGIN_PRIORITY,
    DEFAULT_CELL_CAP,
)
from microtag_harvest.attendance_disclosure_intake import (
    UNSAFE_NAIVE_PREVIEW,
    EventIdentityError,
    build_intake_report,
    naive_preview,
    verify_event,
)
from microtag_harvest.config import RunConfig
from microtag_harvest.pipeline import build_result

ROOT = Path(__file__).resolve().parents[1]
INTAKE = ROOT / "fixtures" / "attendance_disclosure" / "intake.json"
WITNESS = ROOT / "fixtures" / "attendance_disclosure" / "witness.json"


def load_intake() -> dict:
    return json.loads(INTAKE.read_text(encoding="utf-8"))


def subprocess_env() -> dict:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{ROOT}{os.pathsep}{existing}" if existing else str(ROOT)
    )
    return env


@pytest.fixture
def harvested_entities(tmp_path: Path):
    result = build_result(RunConfig.create(
        input_dir=ROOT / "fixtures/site",
        contract_path=ROOT / "fixtures/contracts/site.yaml",
        out_dir=tmp_path / "out",
        now="2026-01-01T00:00:00Z",
    ))
    return result.entities


def test_naive_preview_leaks_masked_cell_via_either_margin():
    data = load_intake()
    preview = naive_preview(data)
    assert preview["status"] == UNSAFE_NAIVE_PREVIEW
    assert preview["safe_to_publish"] is False
    assert preview["masked_cells"] == ["local:morning"]
    assert preview["row_totals"] == {"local": 13, "visiting": 19}
    assert preview["column_totals"] == {
        "morning": 9,
        "afternoon": 10,
        "evening": 13,
    }
    leak = next(
        item
        for item in preview["leak_demonstrations"]
        if item["cell"] == "local:morning"
    )
    # 13 - 6 - 5 == 2 and 9 - 7 == 2: exact reconstruction from both margins.
    assert leak["reconstructed_from_row_total"] == 2
    assert leak["reconstructed_from_column_total"] == 2
    assert leak["exact_reconstruction_possible"] is True


def test_safe_report_blocks_the_naive_reconstruction(harvested_entities):
    data = load_intake()
    report = build_intake_report(data, harvested_entities)
    margin = report["publications"][CONTRACT_MARGIN_PRIORITY]
    # The masked cell is absent from every published local statistic...
    assert "local:morning" not in margin["visible"]["cells"]
    # ...and the 2x2 cycle removes the two cells needed to subtract it out.
    assert set(margin["hidden"]["cells"]) == {
        "local:morning",
        "local:afternoon",
        "visiting:morning",
        "visiting:afternoon",
    }
    values = margin["sensitive_cell_possible_values"]["local:morning"]
    assert len(set(values)) >= 3 and 2 in values
    detail = report["publications"][CONTRACT_DETAIL_PRIORITY]
    assert detail["hidden"]["row_totals"] == ["row_total:local"]
    assert detail["hidden"]["column_totals"] == ["column_total:morning"]


def test_report_binds_to_exactly_one_harvested_event(harvested_entities):
    data = load_intake()
    report = build_intake_report(data, harvested_entities)
    assert report["event"]["identifier"] == data["event_id"]
    assert "Event" in report["event"]["types"]
    assert report["event"]["sources"], "the Event must trace to harvested sources"


def test_unknown_event_identifier_refuses_publication(harvested_entities):
    data = load_intake()
    data["event_id"] = "https://example.invalid/events/does-not-exist#event"
    with pytest.raises(EventIdentityError, match="exactly one harvested Event"):
        build_intake_report(data, harvested_entities)


def test_duplicate_event_identifiers_refuse_publication():
    data = load_intake()
    entities = [
        _StubEntity(data["event_id"], ("Event",)),
        _StubEntity(data["event_id"], ("Event",)),
    ]
    with pytest.raises(EventIdentityError, match="exactly one harvested Event"):
        verify_event(data, entities)


def test_event_without_event_type_is_not_accepted(harvested_entities):
    data = load_intake()
    entities = [_StubEntity(data["event_id"], ("Article",))]
    with pytest.raises(EventIdentityError, match="exactly one harvested Event"):
        verify_event(data, entities)


def test_fingerprint_changes_when_source_counts_change(harvested_entities):
    data = load_intake()
    first = build_intake_report(data, harvested_entities)
    changed = json.loads(json.dumps(data))
    changed["counts"]["local"]["morning"] = 1
    second = build_intake_report(changed, harvested_entities)
    assert first["source_fingerprint"] != second["source_fingerprint"]
    # Margins are recomputed, not carried over from the old disclosure.
    assert (
        second["publications"][CONTRACT_MARGIN_PRIORITY]["visible"]["row_totals"]
        == {"local": 12, "visiting": 19}
    )


def test_witness_fixture_is_not_hard_coded_by_the_engine(harvested_entities):
    data = load_intake()
    saved = json.loads(WITNESS.read_text(encoding="utf-8"))
    report = build_intake_report(data, harvested_entities)
    margin = report["publications"][CONTRACT_MARGIN_PRIORITY]
    produced = {
        json.dumps(witness["grid"], sort_keys=True)
        for witness in margin["witness_grids"]
    }
    for alternative in saved["alternative_grids"]:
        # Each shipped witness is independently re-derived and re-validated.
        assert json.dumps(alternative, sort_keys=True) in produced
    # The engine also produces witnesses beyond the two shipped ones.
    assert len(produced) >= 3


def test_cli_emits_safe_publications_and_unsafe_preview():
    env = subprocess_env()
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "microtag_harvest.attendance_disclosure_intake",
            "--fixture",
            str(INTAKE),
        ],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(completed.stdout)
    assert set(report["publications"]) == {
        CONTRACT_MARGIN_PRIORITY,
        CONTRACT_DETAIL_PRIORITY,
    }
    assert report[UNSAFE_NAIVE_PREVIEW]["safe_to_publish"] is False
    assert report["cell_cap"] == DEFAULT_CELL_CAP
    assert report["synthetic_data_notice"]


def test_cli_refuses_unknown_event(tmp_path: Path):
    data = load_intake()
    data["event_id"] = "https://example.invalid/events/missing#event"
    fixture = tmp_path / "broken.json"
    fixture.write_text(json.dumps(data), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "microtag_harvest.attendance_disclosure_intake",
            "--fixture",
            str(fixture),
        ],
        cwd=ROOT,
        env=subprocess_env(),
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["status"] == "refused"
    assert payload["error_type"] == "EventIdentityError"


def test_cli_refuses_invalid_negative_input(tmp_path: Path):
    data = load_intake()
    data["counts"]["local"]["morning"] = -4
    fixture = tmp_path / "negative.json"
    fixture.write_text(json.dumps(data), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "microtag_harvest.attendance_disclosure_intake",
            "--fixture",
            str(fixture),
        ],
        cwd=ROOT,
        env=subprocess_env(),
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["status"] == "refused"
    assert payload["error_type"] == "DisclosureError"


class _StubEntity:
    def __init__(self, identifier: str, types: tuple[str, ...]):
        self.identifier = identifier
        self.types = types

    def source_records(self) -> list[dict]:
        return [{"annotation_id": "stub", "document": "stub.html"}]
