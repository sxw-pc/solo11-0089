from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from microtag_harvest.cli import main
from microtag_harvest.config import RunConfig
from microtag_harvest.offer_lifecycle import (
    effective_quote,
    lock_quote,
    plan_fulfillment,
)
from microtag_harvest.pipeline import build_result
from microtag_harvest.report import build_report
from microtag_harvest.utils import canonical_json


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "offer_fulfillment"
PRODUCT = "https://example.invalid/catalog/gadget#product"
T_OFFER = "https://example.invalid/catalog/gadget#offer-t-red"
PLAN_AT = "2025-03-02T00:00:00Z"
NOW = "2025-03-01T12:00:00Z"


def _build_report(site_dir: Path, tmp_path: Path) -> dict:
    config = RunConfig.create(site_dir, FIXTURES / "contract.yaml", tmp_path / "out", now=NOW)
    return build_report(build_result(config))


@pytest.fixture
def report(tmp_path: Path) -> dict:
    return _build_report(FIXTURES / "site", tmp_path)


def _orders() -> list[dict]:
    return json.loads((FIXTURES / "orders.json").read_text(encoding="utf-8"))["orders"]


def _lots() -> list[dict]:
    return json.loads((FIXTURES / "inventory.json").read_text(encoding="utf-8"))["lots"]


def _authorizations(report: dict) -> dict:
    """Bind consents to the digests produced by *this* report, never to similarity."""
    locked_a = lock_quote(report, _orders()[0])
    assert locked_a["status"] == "locked"
    substitute = effective_quote(report, PRODUCT, T_OFFER, PLAN_AT)
    assert substitute["status"] == "eligible"
    return {
        "buyer_consents": [{
            "order_id": "order-a",
            "product_id": PRODUCT,
            "offer_id": T_OFFER,
            "receipt_sha256": locked_a["receipt"]["receipt_sha256"],
            "quote_sha256": substitute["quote_sha256"],
        }],
        "seller_acceptances": [{
            "seller": "seller-t",
            "product_id": PRODUCT,
            "offer_id": T_OFFER,
            "quote_sha256": substitute["quote_sha256"],
        }],
        "platform_policy": {"currency": "USD", "max_subsidy_per_order": "2.00"},
    }


def _plan(report: dict, mode: str, current: dict | None = None, auth: dict | None = None) -> dict:
    return plan_fulfillment(
        report, current if current is not None else report,
        orders=_orders(), lots=_lots(),
        authorizations=_authorizations(report) if auth is None else auth,
        mode=mode, plan_at=PLAN_AT,
    )


def _order(plan: dict, order_id: str) -> dict:
    return next(entry for entry in plan["orders"] if entry["order_id"] == order_id)


def _lot(plan: dict, lot_id: str) -> dict:
    return next(entry for entry in plan["lots"] if entry["lot_id"] == lot_id)


def test_locks_alone_do_not_imply_joint_fulfillment(report: dict):
    locked_a = lock_quote(report, _orders()[0])
    locked_b = lock_quote(report, _orders()[1])
    assert locked_a["status"] == locked_b["status"] == "locked"
    assert effective_quote(report, PRODUCT, T_OFFER, PLAN_AT)["status"] == "eligible"
    plan = _plan(report, "original_seller")
    assert plan["totals"]["locked"] == 2
    assert plan["totals"]["fulfilled"] == 1
    assert plan["totals"]["unfulfilled"] == 1


def test_original_seller_contract_prioritizes_earlier_lock(report: dict):
    plan = _plan(report, "original_seller")
    order_a, order_b = _order(plan, "order-a"), _order(plan, "order-b")
    assert order_a["fulfilled"] is True
    assert order_a["assignment"]["kind"] == "original"
    assert order_a["assignment"]["lot_id"] == "lot-l"
    assert order_a["assignment"]["seller"] == "seller-s"
    assert order_a["assignment"]["variant"] == "red"
    assert order_b["fulfilled"] is False
    assert order_b["assignment"] is None
    assert order_b["reasons"] == ["lot_capacity_exhausted"]
    assert _lot(plan, "lot-l")["orders"] == ["order-a"]
    assert _lot(plan, "lot-m")["orders"] == []
    assert plan["totals"]["platform_subsidy"] == "0.00"


def test_substitution_contract_fulfills_both_jointly(report: dict):
    plan = _plan(report, "substitution")
    assert plan["totals"]["fulfilled"] == 2
    order_a, order_b = _order(plan, "order-a"), _order(plan, "order-b")
    assert order_a["assignment"]["kind"] == "substitution"
    assert order_a["assignment"]["lot_id"] == "lot-m"
    assert order_a["assignment"]["seller"] == "seller-t"
    assert order_a["assignment"]["offer_id"] == T_OFFER
    assert order_a["assignment"]["payment"] == {
        "currency": "USD",
        "buyer_pays": "10.00",
        "seller_receives": "12.00",
        "platform_subsidy": "2.00",
    }
    assert order_b["assignment"]["kind"] == "original"
    assert order_b["assignment"]["lot_id"] == "lot-l"
    assert _lot(plan, "lot-l")["orders"] == ["order-b"]
    assert _lot(plan, "lot-m")["orders"] == ["order-a"]
    assert plan["totals"]["platform_subsidy"] == "2.00"


def _auth_variant(report: dict, mutate) -> dict:
    auth = _authorizations(report)
    mutate(auth)
    return auth


@pytest.mark.parametrize("mutation,expected_reason", [
    (lambda auth: auth.update(buyer_consents=[]), "no_substitution_consent"),
    (lambda auth: auth.update(seller_acceptances=[]), "seller_acceptance_missing"),
    (lambda auth: auth["buyer_consents"][0].update(quote_sha256="0" * 64),
     "buyer_consent_quote_mismatch"),
    (lambda auth: auth["buyer_consents"][0].update(receipt_sha256="0" * 64),
     "buyer_consent_receipt_mismatch"),
    (lambda auth: auth["seller_acceptances"][0].update(quote_sha256="0" * 64),
     "seller_acceptance_quote_mismatch"),
    (lambda auth: auth["platform_policy"].update(max_subsidy_per_order="1.00"),
     "subsidy_above_cap"),
    (lambda auth: auth.update(platform_policy=None), "subsidy_policy_missing"),
])
def test_missing_or_mismatched_authorization_removes_substitution(
        report: dict, mutation, expected_reason: str):
    auth = _auth_variant(report, mutation)
    plan = _plan(report, "substitution", auth=auth)
    assert plan["totals"]["fulfilled"] == 1
    order_a = _order(plan, "order-a")
    assert order_a["fulfilled"] is True
    assert order_a["assignment"]["kind"] == "original"
    assert order_a["assignment"]["lot_id"] == "lot-l"
    order_b = _order(plan, "order-b")
    assert order_b["fulfilled"] is False
    checks = order_a["substitution_checks"]
    if expected_reason == "no_substitution_consent":
        assert checks == []
        assert "no_substitution_consent" in order_b["reasons"]
    else:
        assert checks[0]["status"] == "blocked"
        assert expected_reason in checks[0]["reasons"]
        assert order_b["reasons"] == ["lot_capacity_exhausted", "no_substitution_consent"]


def test_expired_substitute_offer_removes_substitution_edge(report: dict):
    current = copy.deepcopy(report)
    product = next(e for e in current["entities"] if e["primary_type"] == "Product")
    offers = product["fields"]["offers"][0]["value"]
    for offer in offers:
        if offer["@id"] == T_OFFER:
            offer["validThrough"] = "2025-03-01T00:00:00Z"
    plan = _plan(report, "substitution", current=current)
    assert plan["totals"]["fulfilled"] == 1
    order_a = _order(plan, "order-a")
    assert order_a["assignment"]["kind"] == "original"
    check = order_a["substitution_checks"][0]
    assert check["status"] == "blocked"
    assert "substitute_offer_not_effective:no_effective_offer" in check["reasons"]
    assert _lot(plan, "lot-m")["orders"] == []


def test_conflicting_original_quote_receipt_is_not_allocated(report: dict):
    conflicted = copy.deepcopy(report)
    product = next(e for e in conflicted["entities"] if e["primary_type"] == "Product")
    offers_field = product["fields"]["offers"]
    extra = copy.deepcopy(offers_field[0])
    extra["annotation_id"] = "conflicting-annotation"
    extra["value"] = [
        {**offer, "price": "11.00"}
        for offer in extra["value"]
        if offer["@id"].endswith("#offer-s-red")
    ]
    offers_field.append(extra)
    assert lock_quote(conflicted, _orders()[0])["status"] == "rejected"
    plan = _plan(conflicted, "substitution", auth=_authorizations(report))
    order_a = _order(plan, "order-a")
    assert order_a["fulfilled"] is False
    assert order_a["reasons"] == ["lock_rejected:conflicting_offer_terms"]
    assert _lot(plan, "lot-l")["orders"] == ["order-b"]
    assert _lot(plan, "lot-m")["orders"] == []
    assert plan["totals"]["fulfilled"] == 1


def test_missing_original_quote_receipt_is_not_allocated(report: dict):
    orders = _orders()
    orders[0] = {**orders[0], "offer_id": "https://example.invalid/catalog/gadget#offer-gone"}
    plan = plan_fulfillment(
        report, report, orders=orders, lots=_lots(),
        authorizations=_authorizations(report), mode="original_seller", plan_at=PLAN_AT,
    )
    order_a = _order(plan, "order-a")
    assert order_a["fulfilled"] is False
    assert order_a["reasons"] == ["lock_rejected:no_effective_offer"]
    assert all("order-a" not in lot["orders"] for lot in plan["lots"])


def test_payment_and_capacity_conservation(report: dict):
    for mode in ("original_seller", "substitution"):
        plan = _plan(report, mode)
        for entry in plan["orders"]:
            if not entry["fulfilled"]:
                continue
            payment = entry["assignment"]["payment"]
            assert payment["buyer_pays"] == entry["lock"]["price"]
            if entry["assignment"]["kind"] == "original":
                assert payment["seller_receives"] == payment["buyer_pays"]
                assert payment["platform_subsidy"] == "0.00"
            else:
                assert payment["seller_receives"] == "12.00"
                assert payment["platform_subsidy"] == "2.00"
        for lot in plan["lots"]:
            assert 0 <= lot["reserved"] <= lot["quantity"]
            assert lot["remaining"] == lot["quantity"] - lot["reserved"]
            assert lot["reserved"] == sum(
                entry["quantity"] for entry in plan["orders"]
                if entry["fulfilled"] and entry["assignment"]["lot_id"] == lot["lot_id"]
            )


def test_json_preserves_provenance_and_blocking_reasons(report: dict):
    plan = _plan(report, "substitution")
    product = next(e for e in report["entities"] if e["primary_type"] == "Product")
    known_annotations = {source["annotation_id"] for source in product["sources"]}
    known_fragments = {source["fragment_sha256"] for source in product["sources"]}
    order_a = _order(plan, "order-a")
    assignment = order_a["assignment"]
    assert assignment["receipt_sha256"] == order_a["lock"]["receipt_sha256"]
    assert assignment["lot_id"] == "lot-m"
    assert assignment["sources"], "substitute offer provenance must be kept"
    for source in assignment["sources"]:
        assert source["annotation_id"] in known_annotations
        assert source["fragment_sha256"] in known_fragments
    assert assignment["authorizations"]["buyer_consent_sha256"]
    assert assignment["authorizations"]["seller_acceptance_sha256"]
    original_sources = _order(plan, "order-b")["assignment"]["sources"]
    assert original_sources and original_sources[0]["annotation_id"] in known_annotations

    blocked = _plan(report, "substitution", auth=_auth_variant(
        report, lambda auth: auth.update(buyer_consents=[])))
    order_b = _order(blocked, "order-b")
    assert order_b["fulfilled"] is False
    assert order_b["reasons"]
    assert "schema_version" in blocked and blocked["reports"]["original_run_id"]


def test_substitution_rights_are_never_inferred(report: dict):
    # A boolean-style flag without digest bindings is not a valid consent.
    with pytest.raises(ValueError):
        _plan(report, "substitution", auth={
            "buyer_consents": [{"order_id": "order-a", "allow_substitution": True}],
        })
    # Same seller, same price, and same product name create no substitution edge:
    # order-b has no consent, so it can never touch seller-t's lot.
    plan = _plan(report, "substitution", auth=_auth_variant(
        report, lambda auth: auth.update(buyer_consents=[])))
    assert _lot(plan, "lot-m")["orders"] == []
    assert _order(plan, "order-b")["substitution_checks"] == []


def test_pipeline_offer_change_flows_into_plan(tmp_path: Path):
    site = tmp_path / "site"
    site.mkdir()
    markup = (FIXTURES / "site" / "product.html").read_text(encoding="utf-8")
    (site / "product.html").write_text(markup.replace("12.00", "11.00"), encoding="utf-8")
    original = _build_report(FIXTURES / "site", tmp_path)
    current = _build_report(site, tmp_path)
    substitute = effective_quote(current, PRODUCT, T_OFFER, PLAN_AT)
    assert substitute["quote"]["price"] == "11.00"
    auth = _authorizations(original)
    auth["buyer_consents"][0]["quote_sha256"] = substitute["quote_sha256"]
    auth["seller_acceptances"][0]["quote_sha256"] = substitute["quote_sha256"]
    plan = _plan(original, "substitution", current=current, auth=auth)
    payment = _order(plan, "order-a")["assignment"]["payment"]
    assert payment == {
        "currency": "USD", "buyer_pays": "10.00",
        "seller_receives": "11.00", "platform_subsidy": "1.00",
    }
    assert plan["totals"]["platform_subsidy"] == "1.00"


def test_committed_report_matches_pipeline(tmp_path: Path):
    committed = json.loads((FIXTURES / "report.initial.json").read_text(encoding="utf-8"))
    rebuilt = _build_report(FIXTURES / "site", tmp_path)
    assert {k: v for k, v in rebuilt.items() if k != "run"} == {
        k: v for k, v in committed.items() if k != "run"
    }


def test_plan_is_byte_stable_across_runs(report: dict):
    first = _plan(report, "substitution")
    second = _plan(report, "substitution")
    assert canonical_json(first) == canonical_json(second)


def _cli_args(mode: str, authorizations: Path | None = None) -> list[str]:
    args = [
        "fulfillment-plan",
        "--original-report", str(FIXTURES / "report.initial.json"),
        "--orders", str(FIXTURES / "orders.json"),
        "--inventory", str(FIXTURES / "inventory.json"),
        "--mode", mode,
        "--at", PLAN_AT,
    ]
    if authorizations is not None:
        args += ["--authorizations", str(authorizations)]
    return args


def test_fulfillment_cli_exit_codes_and_stable_json(capsys):
    auth = FIXTURES / "authorizations.json"
    assert main(_cli_args("substitution", auth)) == 0
    first = capsys.readouterr().out
    assert json.loads(first)["totals"]["fulfilled"] == 2
    assert main(_cli_args("substitution", auth)) == 0
    assert capsys.readouterr().out == first
    assert main(_cli_args("original_seller", auth)) == 1
    original = json.loads(capsys.readouterr().out)
    assert original["totals"]["fulfilled"] == 1
    assert _order(original, "order-b")["reasons"] == ["lot_capacity_exhausted"]


def test_fulfillment_cli_blocked_authorization_exits_one(tmp_path: Path, capsys):
    auth = json.loads((FIXTURES / "authorizations.json").read_text(encoding="utf-8"))
    auth["platform_policy"]["max_subsidy_per_order"] = "1.00"
    broken = tmp_path / "authorizations.json"
    broken.write_text(json.dumps(auth), encoding="utf-8")
    assert main(_cli_args("substitution", broken)) == 1
    plan = json.loads(capsys.readouterr().out)
    assert plan["totals"]["fulfilled"] == 1
    check = _order(plan, "order-a")["substitution_checks"][0]
    assert "subsidy_above_cap" in check["reasons"]


def test_fulfillment_cli_input_errors_exit_two(tmp_path: Path, capsys):
    assert main(_cli_args("substitution", tmp_path / "missing.json")) == 2
    capsys.readouterr()
    bad_orders = tmp_path / "orders.json"
    bad_orders.write_text(json.dumps({"orders": [{"order_id": "x"}]}), encoding="utf-8")
    args = _cli_args("original_seller")
    args[args.index("--orders") + 1] = str(bad_orders)
    assert main(args) == 2
    capsys.readouterr()


def test_fulfillment_cli_builds_report_from_html(tmp_path: Path, capsys):
    assert main([
        "fulfillment-plan",
        "--input", str(FIXTURES / "site"),
        "--contract", str(FIXTURES / "contract.yaml"),
        "--now", NOW,
        "--orders", str(FIXTURES / "orders.json"),
        "--inventory", str(FIXTURES / "inventory.json"),
        "--mode", "original_seller",
        "--at", PLAN_AT,
    ]) == 1
    plan = json.loads(capsys.readouterr().out)
    assert plan["totals"] == {
        "orders": 2, "locked": 2, "fulfilled": 1, "unfulfilled": 1,
        "platform_subsidy": "0.00", "currency": "USD",
    }
    assert _order(plan, "order-a")["assignment"]["lot_id"] == "lot-l"
