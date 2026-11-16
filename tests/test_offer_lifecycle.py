from __future__ import annotations

import copy
import json
from pathlib import Path

from microtag_harvest.config import RunConfig
from microtag_harvest.cli import main
from microtag_harvest.offer_lifecycle import effective_quote, lock_quote, review_price_dispute
from microtag_harvest.pipeline import build_result
from microtag_harvest.report import build_report


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "offer_lifecycle"
CONTRACT = ROOT / "fixtures" / "contracts" / "site.yaml"


def _report(stage: str, tmp_path: Path):
    config = RunConfig.create(
        FIXTURES / stage, CONTRACT, tmp_path / stage,
        now="2025-03-01T12:00:00Z",
    )
    return build_report(build_result(config))


def test_quote_lock_and_dispute_uses_archived_product_offer(tmp_path: Path):
    initial = _report("initial", tmp_path)
    current = _report("current", tmp_path)
    request = json.loads((FIXTURES / "order.json").read_text(encoding="utf-8"))
    locked = lock_quote(initial, request)
    assert locked["status"] == "locked"
    assert locked["receipt"]["quote"]["price"] == "19.00"
    result = review_price_dispute(locked["receipt"], initial, current)
    assert result["status"] == "honor_locked_price"
    assert result["current_price"] == "24.00"


def test_expired_offer_and_changed_receipt_are_rejected(tmp_path: Path):
    initial = _report("initial", tmp_path)
    request = json.loads((FIXTURES / "order.json").read_text(encoding="utf-8"))
    assert effective_quote(initial, request["product_id"], request["offer_id"],
                           "2026-02-01T00:00:00Z")["reason"] == "no_effective_offer"
    receipt = lock_quote(initial, request)["receipt"]
    altered = copy.deepcopy(receipt)
    altered["quote"]["price"] = "1.00"
    assert review_price_dispute(altered, initial, initial) == {
        "status": "rejected", "reason": "receipt_changed",
    }


def test_conflicting_offer_assertions_do_not_get_arbitrary_lock(tmp_path: Path):
    initial = _report("initial", tmp_path)
    request = json.loads((FIXTURES / "order.json").read_text(encoding="utf-8"))
    product = next(entity for entity in initial["entities"] if entity["primary_type"] == "Product")
    other = copy.deepcopy(product["fields"]["offers"][0])
    other["annotation_id"] = "other-annotation"
    other["value"]["price"] = "18.00"
    product["fields"]["offers"].append(other)
    assert lock_quote(initial, request) == {
        "status": "rejected", "reason": "conflicting_offer_terms",
    }


def test_quote_review_cli_reads_reports_and_request(tmp_path: Path, capsys):
    initial = _report("initial", tmp_path)
    current = _report("current", tmp_path)
    old_path, new_path = tmp_path / "old.json", tmp_path / "new.json"
    old_path.write_text(json.dumps(initial), encoding="utf-8")
    new_path.write_text(json.dumps(current), encoding="utf-8")
    assert main([
        "quote-review", "--original-report", str(old_path),
        "--current-report", str(new_path),
        "--order", str(FIXTURES / "order.json"),
    ]) == 0
    assert json.loads(capsys.readouterr().out)["review"]["locked_price"] == "19.00"
