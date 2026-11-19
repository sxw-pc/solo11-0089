"""Offline consumer of Product/Offer annotations for quote locks and review."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any

from .utils import canonical_json


def _instant(value: str) -> datetime | None:
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError):
        return None
    if instant.tzinfo is None:
        return None
    return instant.astimezone(timezone.utc)


def _money(value: Any) -> str | None:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return format(amount.quantize(Decimal("0.01")), ".2f")


def _digest(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _offer_rows(report: dict[str, Any], product_id: str, offer_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entity in report.get("entities", []):
        if entity.get("primary_type") != "Product" or entity.get("identifier") != product_id:
            continue
        sku_values = {str(row.get("value", "")) for row in entity.get("fields", {}).get("sku", [])}
        if len(sku_values) != 1:
            continue
        for source in entity.get("fields", {}).get("offers", []):
            raw_value = source.get("value")
            if isinstance(raw_value, dict):
                offer_values = [raw_value]
            elif isinstance(raw_value, list):
                # JSON-LD commonly nests offers as an array on one annotation.
                offer_values = [item for item in raw_value if isinstance(item, dict)]
            else:
                continue
            for offer in offer_values:
                if offer.get("@id") != offer_id:
                    continue
                price = _money(offer.get("price"))
                currency = offer.get("priceCurrency")
                seller = offer.get("seller")
                variant = offer.get("variant")
                start = _instant(offer.get("validFrom", ""))
                end = _instant(offer.get("validThrough", ""))
                if (price is None or not isinstance(currency, str) or len(currency) != 3
                        or not isinstance(seller, str) or not seller
                        or not isinstance(variant, str) or not variant
                        or start is None or end is None or start >= end):
                    continue
                rows.append({
                    "product_id": product_id,
                    "sku": next(iter(sku_values)),
                    "offer_id": offer_id,
                    "seller": seller,
                    "variant": variant,
                    "price": price,
                    "currency": currency.upper(),
                    "availability": offer.get("availability"),
                    "valid_from": start.isoformat().replace("+00:00", "Z"),
                    "valid_through": end.isoformat().replace("+00:00", "Z"),
                    "annotation_id": source.get("annotation_id", ""),
                    "fragment_sha256": source.get("fragment_sha256", ""),
                    "document": source.get("document", ""),
                })
    return sorted(rows, key=lambda row: (row["seller"], row["variant"], row["price"], row["annotation_id"]))


def effective_quote(
    report: dict[str, Any], product_id: str, offer_id: str, at: str,
) -> dict[str, Any]:
    """Return one unambiguous, in-stock quote supported by a source annotation."""
    instant = _instant(at)
    if instant is None:
        return {"status": "rejected", "reason": "invalid_time"}
    candidates = [
        row for row in _offer_rows(report, product_id, offer_id)
        if _instant(row["valid_from"]) <= instant < _instant(row["valid_through"])
    ]
    if not candidates:
        return {"status": "rejected", "reason": "no_effective_offer"}
    terms = [
        {key: row[key] for key in (
            "product_id", "sku", "offer_id", "seller", "variant", "price", "currency",
            "availability", "valid_from", "valid_through",
        )}
        for row in candidates
    ]
    unique = {_digest(item): item for item in terms}
    if len(unique) != 1:
        return {"status": "rejected", "reason": "conflicting_offer_terms"}
    selected = next(iter(unique.values()))
    if selected["availability"] != "https://example.invalid/vocab/InStock":
        return {"status": "rejected", "reason": "not_in_stock"}
    return {
        "status": "eligible",
        "quote": selected,
        "quote_sha256": _digest(selected),
        "sources": sorted({(row["annotation_id"], row["fragment_sha256"], row["document"]) for row in candidates}),
    }


def lock_quote(report: dict[str, Any], request: dict[str, str]) -> dict[str, Any]:
    """Issue a deterministic local receipt for the terms visible at order time."""
    order_id = request.get("order_id", "")
    if not order_id:
        return {"status": "rejected", "reason": "missing_order_id"}
    decision = effective_quote(
        report, request.get("product_id", ""), request.get("offer_id", ""),
        request.get("at", ""),
    )
    if decision["status"] != "eligible":
        return decision
    receipt = {
        "order_id": order_id,
        "locked_at": request["at"],
        "report_run_id": report.get("run", {}).get("run_id", ""),
        "quote": decision["quote"],
        "quote_sha256": decision["quote_sha256"],
    }
    receipt["receipt_sha256"] = _digest(receipt)
    return {"status": "locked", "receipt": receipt}


def review_price_dispute(
    receipt: dict[str, Any], original_report: dict[str, Any],
    current_report: dict[str, Any],
) -> dict[str, Any]:
    """Check an old lock against its archived quote and current visible terms."""
    signed = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    if receipt.get("receipt_sha256") != _digest(signed):
        return {"status": "rejected", "reason": "receipt_changed"}
    quote = receipt.get("quote", {})
    if not isinstance(quote, dict):
        return {"status": "rejected", "reason": "invalid_quote"}
    if receipt.get("report_run_id") != original_report.get("run", {}).get("run_id"):
        return {"status": "unverifiable", "reason": "original_report_missing"}
    historical = effective_quote(
        original_report, quote.get("product_id", ""), quote.get("offer_id", ""),
        receipt.get("locked_at", ""),
    )
    if historical.get("quote_sha256") != receipt.get("quote_sha256"):
        return {"status": "unverifiable", "reason": "original_terms_mismatch"}
    current = effective_quote(
        current_report, quote["product_id"], quote["offer_id"], receipt["locked_at"],
    )
    return {
        "status": "honor_locked_price",
        "order_id": receipt["order_id"],
        "locked_price": quote["price"],
        "currency": quote["currency"],
        "current_price": current["quote"]["price"] if current["status"] == "eligible" else None,
        "current_quote_status": current["status"],
    }


FULFILLMENT_MODES = ("original_seller", "substitution")


def _text_field(entry: dict[str, Any], name: str, where: str) -> str:
    value = entry.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where}.{name} must be a non-empty string")
    return value


def _validated_orders(orders: Any) -> list[dict[str, Any]]:
    if not isinstance(orders, list):
        raise ValueError("orders manifest must be a list")
    validated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(orders):
        where = f"orders[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{where} must be an object")
        order_id = _text_field(entry, "order_id", where)
        if order_id in seen:
            raise ValueError(f"duplicate order_id {order_id!r}")
        seen.add(order_id)
        at = _text_field(entry, "at", where)
        if _instant(at) is None:
            raise ValueError(f"{where}.at is not a valid ISO-8601 instant: {at!r}")
        quantity = entry.get("quantity", 1)
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            raise ValueError(f"{where}.quantity must be a positive integer")
        validated.append({
            "order_id": order_id,
            "product_id": _text_field(entry, "product_id", where),
            "offer_id": _text_field(entry, "offer_id", where),
            "at": at,
            "quantity": quantity,
        })
    return validated


def _validated_lots(lots: Any) -> list[dict[str, Any]]:
    if not isinstance(lots, list):
        raise ValueError("inventory manifest must be a list of lots")
    validated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(lots):
        where = f"lots[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{where} must be an object")
        lot_id = _text_field(entry, "lot_id", where)
        if lot_id in seen:
            raise ValueError(f"duplicate lot_id {lot_id!r}")
        seen.add(lot_id)
        variants = entry.get("variants")
        if (not isinstance(variants, list) or not variants
                or any(not isinstance(item, str) or not item for item in variants)):
            raise ValueError(f"{where}.variants must be a non-empty list of strings")
        quantity = entry.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
            raise ValueError(f"{where}.quantity must be a non-negative integer")
        validated.append({
            "lot_id": lot_id,
            "seller": _text_field(entry, "seller", where),
            "variants": sorted(set(variants)),
            "quantity": quantity,
        })
    return validated


def _validated_authorizations(authorizations: Any) -> dict[str, Any]:
    if authorizations is None:
        authorizations = {}
    if not isinstance(authorizations, dict):
        raise ValueError("authorizations manifest must be an object")
    consents: list[dict[str, Any]] = []
    for index, entry in enumerate(authorizations.get("buyer_consents") or []):
        where = f"buyer_consents[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{where} must be an object")
        consents.append({
            "order_id": _text_field(entry, "order_id", where),
            "product_id": _text_field(entry, "product_id", where),
            "offer_id": _text_field(entry, "offer_id", where),
            "receipt_sha256": _text_field(entry, "receipt_sha256", where),
            "quote_sha256": _text_field(entry, "quote_sha256", where),
        })
    acceptances: list[dict[str, Any]] = []
    for index, entry in enumerate(authorizations.get("seller_acceptances") or []):
        where = f"seller_acceptances[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{where} must be an object")
        acceptances.append({
            "seller": _text_field(entry, "seller", where),
            "product_id": _text_field(entry, "product_id", where),
            "offer_id": _text_field(entry, "offer_id", where),
            "quote_sha256": _text_field(entry, "quote_sha256", where),
        })
    raw_policy = authorizations.get("platform_policy")
    policy: dict[str, str] | None = None
    if raw_policy is not None:
        if not isinstance(raw_policy, dict):
            raise ValueError("platform_policy must be an object")
        cap = _money(raw_policy.get("max_subsidy_per_order"))
        if cap is None:
            raise ValueError("platform_policy.max_subsidy_per_order must be a non-negative amount")
        currency = raw_policy.get("currency")
        if not isinstance(currency, str) or len(currency) != 3:
            raise ValueError("platform_policy.currency must be a 3-letter code")
        policy = {"max_subsidy_per_order": cap, "currency": currency.upper()}
    return {
        "buyer_consents": consents,
        "seller_acceptances": acceptances,
        "platform_policy": policy,
    }


def _cents(amount: str) -> int:
    return int(Decimal(amount) * 100)


def _source_dicts(sources: list[Any]) -> list[dict[str, str]]:
    rows = []
    for item in sources:
        annotation_id, fragment_sha256, document = item
        rows.append({
            "annotation_id": annotation_id,
            "fragment_sha256": fragment_sha256,
            "document": document,
        })
    return rows


def _evaluate_substitution(
    receipt: dict[str, Any],
    consent: dict[str, Any],
    acceptances: list[dict[str, Any]],
    policy: dict[str, str] | None,
    lots: list[dict[str, Any]],
    current_report: dict[str, Any],
    plan_at: str,
) -> dict[str, Any]:
    """Check one buyer consent against the receipt, current offer, acceptance, and lots."""
    locked = receipt["quote"]
    reasons: list[str] = []
    decision = effective_quote(current_report, consent["product_id"], consent["offer_id"], plan_at)
    substitute = decision.get("quote") if decision["status"] == "eligible" else None
    if consent["receipt_sha256"] != receipt["receipt_sha256"]:
        reasons.append("buyer_consent_receipt_mismatch")
    if substitute is None:
        reasons.append(f"substitute_offer_not_effective:{decision['reason']}")
        return {"consent": consent, "quote": None, "quote_sha256": None,
                "sources": [], "reasons": reasons, "edges": []}
    quote_sha256 = decision["quote_sha256"]
    if consent["quote_sha256"] != quote_sha256:
        reasons.append("buyer_consent_quote_mismatch")
    matching = [
        entry for entry in acceptances
        if entry["seller"] == substitute["seller"]
        and entry["product_id"] == consent["product_id"]
        and entry["offer_id"] == consent["offer_id"]
    ]
    acceptance: dict[str, Any] | None = None
    if not matching:
        reasons.append("seller_acceptance_missing")
    else:
        matching.sort(key=lambda entry: entry["quote_sha256"])
        if not any(entry["quote_sha256"] == quote_sha256 for entry in matching):
            reasons.append("seller_acceptance_quote_mismatch")
        else:
            acceptance = next(entry for entry in matching if entry["quote_sha256"] == quote_sha256)
    subsidy = Decimal(substitute["price"]) - Decimal(locked["price"])
    if substitute["currency"] != locked["currency"]:
        reasons.append("currency_mismatch")
    elif subsidy < 0:
        reasons.append("substitute_price_below_locked")
    elif subsidy > 0:
        if policy is None:
            reasons.append("subsidy_policy_missing")
        elif policy["currency"] != locked["currency"]:
            reasons.append("currency_mismatch")
        elif subsidy > Decimal(policy["max_subsidy_per_order"]):
            reasons.append("subsidy_above_cap")
    compatible = [
        lot for lot in lots
        if lot["seller"] == substitute["seller"] and substitute["variant"] in lot["variants"]
        and lot["quantity"] > 0
    ]
    if not compatible:
        reasons.append("no_compatible_lot")
    edges = []
    if not reasons:
        for lot in compatible:
            edges.append({
                "kind": "substitution",
                "lot_id": lot["lot_id"],
                "seller": substitute["seller"],
                "variant": substitute["variant"],
                "product_id": substitute["product_id"],
                "offer_id": substitute["offer_id"],
                "quote_sha256": quote_sha256,
                "sources": _source_dicts(decision["sources"]),
                "buyer_pays": locked["price"],
                "seller_receives": substitute["price"],
                "platform_subsidy": format(subsidy.quantize(Decimal("0.01")), ".2f"),
                "currency": locked["currency"],
                "consent": consent,
                "acceptance": acceptance,
            })
    return {"consent": consent, "quote": substitute, "quote_sha256": quote_sha256,
            "sources": _source_dicts(decision["sources"]), "reasons": reasons, "edges": edges}


def _plan_key(
    assignment: dict[str, dict[str, Any] | None],
    priority: list[dict[str, Any]],
) -> tuple[Any, ...]:
    count = sum(1 for edge in assignment.values() if edge is not None)
    vector = tuple(
        1 if assignment.get(state["request"]["order_id"]) is not None else 0
        for state in priority
    )
    subsidy = sum(
        _cents(edge["platform_subsidy"]) for edge in assignment.values() if edge is not None
    )
    signature = tuple(sorted(
        (order_id, edge["lot_id"], edge["kind"], edge["offer_id"])
        for order_id, edge in assignment.items() if edge is not None
    ))
    return (count, vector, -subsidy, signature)


def _key_better(candidate: tuple[Any, ...], best: tuple[Any, ...]) -> bool:
    if candidate[0] != best[0]:
        return candidate[0] > best[0]
    if candidate[1] != best[1]:
        return candidate[1] > best[1]
    if candidate[2] != best[2]:
        return candidate[2] > best[2]
    return candidate[3] < best[3]


def _optimize(
    priority: list[dict[str, Any]],
    edges_by_order: dict[str, list[dict[str, Any]]],
    lots: list[dict[str, Any]],
) -> dict[str, dict[str, Any] | None]:
    """Joint assignment: maximize fulfilled orders, then lock-time priority, then subsidy."""
    remaining = {lot["lot_id"]: lot["quantity"] for lot in lots}
    best: dict[str, Any] = {"key": None, "assignment": None}
    assignment: dict[str, dict[str, Any] | None] = {}

    def recurse(index: int) -> None:
        if index == len(priority):
            key = _plan_key(assignment, priority)
            if best["key"] is None or _key_better(key, best["key"]):
                best["key"] = key
                best["assignment"] = dict(assignment)
            return
        state = priority[index]
        order_id = state["request"]["order_id"]
        quantity = state["request"]["quantity"]
        assignment[order_id] = None
        recurse(index + 1)
        del assignment[order_id]
        for edge in edges_by_order.get(order_id, []):
            if remaining[edge["lot_id"]] < quantity:
                continue
            remaining[edge["lot_id"]] -= quantity
            assignment[order_id] = edge
            recurse(index + 1)
            del assignment[order_id]
            remaining[edge["lot_id"]] += quantity

    recurse(0)
    return best["assignment"] or {}


def plan_fulfillment(
    original_report: dict[str, Any],
    current_report: dict[str, Any],
    orders: Any,
    lots: Any,
    authorizations: Any,
    mode: str,
    plan_at: str,
) -> dict[str, Any]:
    """Plan joint fulfillment for locked orders against local lots.

    Candidate edges are derived jointly from lock receipts, current offers,
    buyer/seller authorizations, and lot capabilities before any capacity is
    allocated; the optimizer then maximizes fulfilled orders, breaks ties by
    lock-time priority, and finally minimizes platform subsidy.
    """
    if mode not in FULFILLMENT_MODES:
        raise ValueError(f"unknown fulfillment mode: {mode!r}")
    instant = _instant(plan_at)
    if instant is None:
        raise ValueError(f"invalid plan_at instant: {plan_at!r}")
    plan_at = instant.isoformat().replace("+00:00", "Z")
    requests = _validated_orders(orders)
    lot_rows = _validated_lots(lots)
    auth = _validated_authorizations(authorizations)

    states: list[dict[str, Any]] = []
    for request in requests:
        state: dict[str, Any] = {"request": request, "lock": lock_quote(original_report, request)}
        if state["lock"]["status"] == "locked":
            receipt = state["lock"]["receipt"]
            state["review"] = review_price_dispute(receipt, original_report, current_report)
            if state["review"]["status"] == "honor_locked_price":
                historical = effective_quote(
                    original_report, receipt["quote"]["product_id"],
                    receipt["quote"]["offer_id"], receipt["locked_at"],
                )
                state["sources"] = _source_dicts(historical.get("sources", []))
        states.append(state)

    edges_by_order: dict[str, list[dict[str, Any]]] = {}
    checks_by_order: dict[str, list[dict[str, Any]]] = {}
    for state in states:
        request = state["request"]
        order_id = request["order_id"]
        if state.get("review", {}).get("status") != "honor_locked_price":
            continue
        receipt = state["lock"]["receipt"]
        locked = receipt["quote"]
        edges: list[dict[str, Any]] = []
        for lot in lot_rows:
            if lot["seller"] != locked["seller"] or locked["variant"] not in lot["variants"]:
                continue
            if lot["quantity"] <= 0:
                continue
            edges.append({
                "kind": "original",
                "lot_id": lot["lot_id"],
                "seller": locked["seller"],
                "variant": locked["variant"],
                "product_id": locked["product_id"],
                "offer_id": locked["offer_id"],
                "quote_sha256": receipt["quote_sha256"],
                "sources": state["sources"],
                "buyer_pays": locked["price"],
                "seller_receives": locked["price"],
                "platform_subsidy": "0.00",
                "currency": locked["currency"],
                "consent": None,
                "acceptance": None,
            })
        checks: list[dict[str, Any]] = []
        if mode == "substitution":
            for consent in auth["buyer_consents"]:
                if consent["order_id"] != order_id:
                    continue
                evaluation = _evaluate_substitution(
                    receipt, consent, auth["seller_acceptances"],
                    auth["platform_policy"], lot_rows, current_report, plan_at,
                )
                checks.append({
                    "product_id": consent["product_id"],
                    "offer_id": consent["offer_id"],
                    "status": "eligible" if evaluation["edges"] else "blocked",
                    "reasons": evaluation["reasons"],
                    "quote_sha256": evaluation["quote_sha256"],
                    "sources": evaluation["sources"],
                })
                edges.extend(evaluation["edges"])
        edges.sort(key=lambda edge: (
            edge["lot_id"], edge["kind"], edge["offer_id"], edge["quote_sha256"],
        ))
        edges_by_order[order_id] = edges
        checks_by_order[order_id] = sorted(checks, key=lambda item: item["offer_id"])

    honored = [state for state in states if state.get("review", {}).get("status") == "honor_locked_price"]
    priority = sorted(
        honored,
        key=lambda state: (_instant(state["request"]["at"]), state["request"]["order_id"]),
    )
    assignment = _optimize(priority, edges_by_order, lot_rows)

    reserved: dict[str, list[str]] = {lot["lot_id"]: [] for lot in lot_rows}
    for state in priority:
        order_id = state["request"]["order_id"]
        edge = assignment.get(order_id)
        if edge is not None:
            reserved[edge["lot_id"]].append(order_id)

    order_entries: list[dict[str, Any]] = []
    for state in sorted(
            states, key=lambda item: (_instant(item["request"]["at"]), item["request"]["order_id"])):
        request = state["request"]
        order_id = request["order_id"]
        lock = state["lock"]
        entry: dict[str, Any] = {
            "order_id": order_id,
            "quantity": request["quantity"],
            "locked_at": request["at"],
            "lock": {"status": lock["status"]},
        }
        if lock["status"] == "locked":
            receipt = lock["receipt"]
            locked = receipt["quote"]
            entry["lock"].update({
                "receipt_sha256": receipt["receipt_sha256"],
                "quote_sha256": receipt["quote_sha256"],
                "product_id": locked["product_id"],
                "offer_id": locked["offer_id"],
                "seller": locked["seller"],
                "variant": locked["variant"],
                "price": locked["price"],
                "currency": locked["currency"],
                "sources": state.get("sources", []),
            })
            entry["review_status"] = state["review"]["status"]
        else:
            entry["lock"]["reason"] = lock["reason"]
        edge = assignment.get(order_id)
        if edge is not None:
            assignment_entry: dict[str, Any] = {
                "kind": edge["kind"],
                "lot_id": edge["lot_id"],
                "seller": edge["seller"],
                "variant": edge["variant"],
                "quantity": request["quantity"],
                "product_id": edge["product_id"],
                "offer_id": edge["offer_id"],
                "quote_sha256": edge["quote_sha256"],
                "receipt_sha256": lock["receipt"]["receipt_sha256"],
                "sources": edge["sources"],
                "payment": {
                    "currency": edge["currency"],
                    "buyer_pays": edge["buyer_pays"],
                    "seller_receives": edge["seller_receives"],
                    "platform_subsidy": edge["platform_subsidy"],
                },
            }
            if edge["kind"] == "substitution":
                assignment_entry["authorizations"] = {
                    "buyer_consent_sha256": _digest(edge["consent"]),
                    "seller_acceptance_sha256": _digest(edge["acceptance"]),
                }
            entry["fulfilled"] = True
            entry["assignment"] = assignment_entry
            entry["reasons"] = []
        else:
            reasons: list[str] = []
            if lock["status"] != "locked":
                reasons.append(f"lock_rejected:{lock['reason']}")
            elif state["review"]["status"] != "honor_locked_price":
                reasons.append(f"receipt_not_honored:{state['review']['status']}")
            else:
                edges = edges_by_order.get(order_id, [])
                if not any(item["kind"] == "original" for item in edges):
                    reasons.append("no_direct_lot")
                checks = checks_by_order.get(order_id, [])
                if mode == "substitution" and not checks:
                    reasons.append("no_substitution_consent")
                for check in checks:
                    reasons.extend(check["reasons"])
                if edges or any(check["status"] == "eligible" for check in checks):
                    reasons.append("lot_capacity_exhausted")
            entry["fulfilled"] = False
            entry["assignment"] = None
            entry["reasons"] = sorted(set(reasons))
        if mode == "substitution":
            entry["substitution_checks"] = checks_by_order.get(order_id, [])
        order_entries.append(entry)

    lot_entries = []
    for lot in sorted(lot_rows, key=lambda item: item["lot_id"]):
        consumers = sorted(reserved[lot["lot_id"]])
        used = sum(
            state["request"]["quantity"]
            for state in priority
            if state["request"]["order_id"] in consumers
        )
        lot_entries.append({
            "lot_id": lot["lot_id"],
            "seller": lot["seller"],
            "variants": lot["variants"],
            "quantity": lot["quantity"],
            "reserved": used,
            "remaining": lot["quantity"] - used,
            "orders": consumers,
        })

    fulfilled_edges = [edge for edge in assignment.values() if edge is not None]
    currencies = {edge["currency"] for edge in fulfilled_edges}
    total_subsidy = sum(_cents(edge["platform_subsidy"]) for edge in fulfilled_edges)
    fulfilled = sum(1 for entry in order_entries if entry["fulfilled"])
    return {
        "schema_version": 1,
        "mode": mode,
        "plan_at": plan_at,
        "reports": {
            "original_run_id": original_report.get("run", {}).get("run_id", ""),
            "current_run_id": current_report.get("run", {}).get("run_id", ""),
        },
        "totals": {
            "orders": len(order_entries),
            "locked": sum(1 for state in states if state["lock"]["status"] == "locked"),
            "fulfilled": fulfilled,
            "unfulfilled": len(order_entries) - fulfilled,
            "platform_subsidy": format(Decimal(total_subsidy) / 100, ".2f"),
            "currency": next(iter(currencies)) if len(currencies) == 1 else None,
        },
        "orders": order_entries,
        "lots": lot_entries,
    }
