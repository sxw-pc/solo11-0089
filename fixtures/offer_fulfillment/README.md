# Offer fulfillment fixtures

Synthetic, offline-only fixtures for the joint fulfillment planner. Nothing here
comes from a real shop, payment system, or order system.

Scenario (all timestamps fixed):

- `site/product.html` — one JSON-LD Product (`gadget#product`) whose `offers`
  array carries three offers: seller-s red USD 10.00, seller-s blue USD 10.00,
  seller-t red USD 12.00, all valid 2025-01-01 through 2026-01-01.
- `orders.json` — order-a locks seller-s red at 2025-03-01T12:00:00Z, order-b
  locks seller-s blue at 2025-03-01T12:05:00Z; one unit each.
- `inventory.json` — lot-l (seller-s, red or blue, 1 unit) and lot-m
  (seller-t, red only, 1 unit).
- `authorizations.json` — buyer consent binding order-a's lock receipt digest
  and seller-t's current quote digest, seller-t's acceptance binding the same
  quote digest, and a platform subsidy cap of USD 2.00 per order.
- `report.initial.json` — pipeline report for `site/`, committed so the
  authorization digests stay reproducible regardless of checkout path.

With these inputs the `original_seller` contract fulfills only order-a (lot-l,
earlier lock wins), while `substitution` fulfills both: order-a via lot-m
(buyer pays locked 10.00, seller-t receives current 12.00, platform subsidizes
2.00) and order-b via lot-l.

Regenerate the committed report and authorizations from the repository root:

```bash
python - <<'EOF'
import json
from pathlib import Path
from microtag_harvest.config import RunConfig
from microtag_harvest.pipeline import build_result
from microtag_harvest.report import build_report
from microtag_harvest.offer_lifecycle import effective_quote, lock_quote
from microtag_harvest.utils import pretty_json, write_text

config = RunConfig.create(
    "fixtures/offer_fulfillment/site",
    "fixtures/offer_fulfillment/contract.yaml",
    "out",
    now="2025-03-01T12:00:00Z",
)
report = build_report(build_result(config))
write_text(Path("fixtures/offer_fulfillment/report.initial.json"), pretty_json(report))

orders = json.loads(Path("fixtures/offer_fulfillment/orders.json").read_text())["orders"]
product = "https://example.invalid/catalog/gadget#product"
t_offer = "https://example.invalid/catalog/gadget#offer-t-red"
lock_a = lock_quote(report, orders[0])
t_quote = effective_quote(report, product, t_offer, "2025-03-02T00:00:00Z")
auth = {
    "buyer_consents": [{
        "order_id": "order-a",
        "product_id": product,
        "offer_id": t_offer,
        "receipt_sha256": lock_a["receipt"]["receipt_sha256"],
        "quote_sha256": t_quote["quote_sha256"],
    }],
    "seller_acceptances": [{
        "seller": "seller-t",
        "product_id": product,
        "offer_id": t_offer,
        "quote_sha256": t_quote["quote_sha256"],
    }],
    "platform_policy": {"currency": "USD", "max_subsidy_per_order": "2.00"},
}
write_text(Path("fixtures/offer_fulfillment/authorizations.json"), pretty_json(auth))
EOF
```

Run `pytest -q` afterwards; `tests/test_offer_fulfillment.py` rebuilds the
report from the HTML and compares it with the committed copy.
