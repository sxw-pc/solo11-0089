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
            offer = source.get("value")
            if not isinstance(offer, dict) or offer.get("@id") != offer_id:
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
