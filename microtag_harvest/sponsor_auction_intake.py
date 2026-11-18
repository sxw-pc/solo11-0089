"""Issue sponsor placement allocations and invoices for a harvested Event.

Bids may bundle several placements.  A placement can be sold to at most one
winning bid, and an advertiser may submit mutually exclusive alternative bids
but can win at most once.  The allocation is the exact optimum of a bounded
subset search over at most eight bids and four placements.

Two settlement contracts are supported for the same allocation:

``first_price``
    Each winner pays its own bid amount.

``externality_price``
    Each winning advertiser pays the opportunity cost imposed on everybody
    else: remove *all* of that advertiser's bids (including losing
    alternatives), re-optimise the remaining bids, and subtract the value of
    the other winners from the original allocation.

Everything is synchronous, deterministic, and offline.  No auction service or
network request is involved.
"""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from .config import RunConfig
from .merge import MergedEntity
from .pipeline import build_result
from .utils import canonical_json, sha256_hex


MAX_BIDS = 8
MAX_PLACEMENTS = 4

FIRST_PRICE = "first_price"
EXTERNALITY_PRICE = "externality_price"
KNOWN_CONTRACTS = (FIRST_PRICE, EXTERNALITY_PRICE)
DEFAULT_CONTRACTS = KNOWN_CONTRACTS

TIE_BREAK_RULE = (
    "maximize the total winning amount; then minimize the number of winning "
    "advertisers; then choose the lexicographically smallest sorted bid_id tuple"
)


class AuctionRejection(ValueError):
    """Refuse to issue an allocation or invoice, with a machine-readable code."""

    def __init__(self, code: str, reason: str, details: dict[str, Any] | None = None):
        self.code = code
        self.reason = reason
        self.details = details or {}
        super().__init__(f"{code}: {reason}")

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "reason": self.reason, "details": self.details}


@dataclass(frozen=True)
class SponsorBid:
    bid_id: str
    advertiser: str
    placements: tuple[str, ...]
    amount: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "bid_id": self.bid_id,
            "advertiser": self.advertiser,
            "placements": list(self.placements),
            "amount": self.amount,
        }


@dataclass(frozen=True)
class WinningBid:
    bid_id: str
    advertiser: str
    placements: tuple[str, ...]
    amount: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "bid_id": self.bid_id,
            "advertiser": self.advertiser,
            "placements": list(self.placements),
            "amount": self.amount,
        }


@dataclass(frozen=True)
class Allocation:
    winners: tuple[WinningBid, ...]
    total_value: int
    advertiser_count: int

    @property
    def bid_ids(self) -> tuple[str, ...]:
        return tuple(winner.bid_id for winner in self.winners)

    def occupied_placements(self) -> dict[str, str]:
        """Map each sold placement to the winning bid that occupies it."""
        occupied: dict[str, str] = {}
        for winner in self.winners:
            for placement in winner.placements:
                occupied[placement] = winner.bid_id
        return occupied

    def to_dict(self) -> dict[str, Any]:
        return {
            "winners": [winner.to_dict() for winner in self.winners],
            "winning_bid_ids": list(self.bid_ids),
            "winning_advertisers": [winner.advertiser for winner in self.winners],
            "total_value": self.total_value,
            "winning_advertiser_count": self.advertiser_count,
            "occupied_placements": self.occupied_placements(),
        }


@dataclass(frozen=True)
class SearchCertificate:
    bid_count: int
    placement_count: int
    subsets_considered: int
    feasible_allocations: int
    max_total_value: int
    allocations_at_max_total: int
    minimum_advertiser_count_at_max: int
    candidates_at_best: tuple[tuple[str, ...], ...]
    selected_bid_ids: tuple[str, ...]
    selected_advertiser_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "optimality": {
                "method": "bounded_exhaustive_feasible_subset_search",
                "exact": True,
                "bid_count": self.bid_count,
                "placement_count": self.placement_count,
                "subsets_considered": self.subsets_considered,
                "feasible_allocations": self.feasible_allocations,
                "max_total_value": self.max_total_value,
                "allocations_at_max_total": self.allocations_at_max_total,
                "selected_total_value": self.max_total_value,
            },
            "tie_break": {
                "rule": TIE_BREAK_RULE,
                "minimum_winning_advertisers_at_max": self.minimum_advertiser_count_at_max,
                "candidates_after_value_and_advertiser_tie_break": [
                    list(candidate) for candidate in self.candidates_at_best
                ],
                "selected_bid_ids": list(self.selected_bid_ids),
                "selected_winning_advertiser_count": self.selected_advertiser_count,
            },
        }


@dataclass(frozen=True)
class InvoiceLine:
    bid_id: str
    advertiser: str
    placements: tuple[str, ...]
    bid_amount: int
    payment: int
    counterfactual: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "bid_id": self.bid_id,
            "advertiser": self.advertiser,
            "placements": list(self.placements),
            "bid_amount": self.bid_amount,
            "payment": self.payment,
        }
        if self.counterfactual is not None:
            row["counterfactual"] = self.counterfactual
        return row


@dataclass(frozen=True)
class Invoice:
    contract: str
    lines: tuple[InvoiceLine, ...]
    seller_total: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract": self.contract,
            "lines": [line.to_dict() for line in self.lines],
            "seller_total": self.seller_total,
        }


@dataclass(frozen=True)
class AuctionIssuance:
    event: dict[str, Any]
    identity_sha256: str
    placements: tuple[str, ...]
    bids: tuple[SponsorBid, ...]
    allocation: Allocation
    certificate: SearchCertificate
    invoices: dict[str, Invoice]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "issued",
            "event_id": self.event["identifier"],
            "event": self.event,
            "identity_sha256": self.identity_sha256,
            "placements": list(self.placements),
            "bids": [bid.to_dict() for bid in self.bids],
            "bid_ids": list(self.allocation.bid_ids),
            "welfare": self.allocation.total_value,
            "total_value": self.allocation.total_value,
            "allocation": self.allocation.to_dict(),
            "certificate": self.certificate.to_dict(),
            "invoices": {
                name: invoice.to_dict() for name, invoice in self.invoices.items()
            },
        }


@dataclass(frozen=True)
class _Candidate:
    total: int
    advertiser_count: int
    bid_ids: tuple[str, ...]
    winners: tuple[WinningBid, ...]


def event_from_entities(entities: Iterable[MergedEntity], event_id: str) -> MergedEntity:
    matches = [entity for entity in entities if entity.identifier == event_id and "Event" in entity.types]
    if len(matches) != 1:
        raise AuctionRejection(
            "EVENT_NOT_UNIQUE",
            f"expected one harvested Event for {event_id!r}; found {len(matches)}",
            {"event_id": event_id, "matches": len(matches)},
        )
    return matches[0]


def atomic_preview(placements: Iterable[str], bids: Iterable[SponsorBid]) -> dict[str, object]:
    """Return the legacy single-placement booking preview (kept for comparison)."""
    inventory = tuple(placements)
    offers = tuple(bids)
    chosen = []
    used_advertisers: set[str] = set()
    for placement in inventory:
        available = [
            bid for bid in offers
            if bid.placements == (placement,) and bid.advertiser not in used_advertisers
        ]
        if available:
            winner = max(available, key=lambda bid: (bid.amount, bid.bid_id))
            chosen.append(winner)
            used_advertisers.add(winner.advertiser)
    return {
        "bid_ids": [bid.bid_id for bid in chosen],
        "welfare": sum(bid.amount for bid in chosen),
        "first_price_total": sum(bid.amount for bid in chosen),
    }


def _nonempty_string(value: Any, code: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AuctionRejection(code, f"{label} must be a non-empty string")
    return value.strip()


def validate_intake(
    placements: Iterable[str], bids: Iterable[SponsorBid]
) -> tuple[tuple[str, ...], tuple[SponsorBid, ...]]:
    """Validate and deterministically normalise an intake round."""
    raw_placements = list(placements)
    if not raw_placements:
        raise AuctionRejection("PLACEMENTS_EMPTY", "placement inventory must not be empty")
    if len(raw_placements) > MAX_PLACEMENTS:
        raise AuctionRejection(
            "SEARCH_BOUND_EXCEEDED",
            f"at most {MAX_PLACEMENTS} placements are supported",
            {"placements": len(raw_placements)},
        )
    inventory: list[str] = []
    seen_placements: set[str] = set()
    for raw in raw_placements:
        name = _nonempty_string(raw, "PLACEMENT_INVALID", "placement")
        if name in seen_placements:
            raise AuctionRejection(
                "PLACEMENT_DUPLICATE", f"duplicate placement {name!r}", {"placement": name}
            )
        seen_placements.add(name)
        inventory.append(name)

    raw_bids = list(bids)
    if not raw_bids:
        raise AuctionRejection("NO_BIDS", "at least one sponsor bid is required")
    if len(raw_bids) > MAX_BIDS:
        raise AuctionRejection(
            "SEARCH_BOUND_EXCEEDED",
            f"at most {MAX_BIDS} bids are supported",
            {"bids": len(raw_bids)},
        )

    normalized: list[SponsorBid] = []
    seen_ids: set[str] = set()
    for raw in raw_bids:
        if not isinstance(raw, SponsorBid):
            raise AuctionRejection("BID_INVALID", "bid must be a SponsorBid")
        bid_id = _nonempty_string(raw.bid_id, "BID_ID_INVALID", "bid_id")
        if bid_id in seen_ids:
            raise AuctionRejection(
                "BID_ID_DUPLICATE", f"duplicate bid_id {bid_id!r}", {"bid_id": bid_id}
            )
        seen_ids.add(bid_id)
        advertiser = _nonempty_string(raw.advertiser, "ADVERTISER_EMPTY", "advertiser")
        if not isinstance(raw.placements, (tuple, list)):
            raise AuctionRejection(
                "BID_PLACEMENTS_INVALID", f"bid {bid_id!r} placements must be a list"
            )
        requested_raw = list(raw.placements)
        if not requested_raw:
            raise AuctionRejection(
                "BID_PLACEMENTS_EMPTY", f"bid {bid_id!r} must request at least one placement"
            )
        requested: list[str] = []
        for item in requested_raw:
            name = _nonempty_string(
                item, "BID_PLACEMENT_INVALID", f"bid {bid_id!r} placement"
            )
            if name in requested:
                raise AuctionRejection(
                    "BID_PLACEMENT_DUPLICATE",
                    f"bid {bid_id!r} requests placement {name!r} more than once",
                    {"bid_id": bid_id, "placement": name},
                )
            if name not in seen_placements:
                raise AuctionRejection(
                    "PLACEMENT_UNKNOWN",
                    f"bid {bid_id!r} requests unknown placement {name!r}",
                    {"bid_id": bid_id, "placement": name},
                )
            requested.append(name)
        amount = raw.amount
        if isinstance(amount, bool) or not isinstance(amount, int):
            raise AuctionRejection(
                "AMOUNT_INVALID", f"bid {bid_id!r} amount must be an integer",
                {"bid_id": bid_id, "amount": amount},
            )
        if amount < 0:
            raise AuctionRejection(
                "AMOUNT_NEGATIVE", f"bid {bid_id!r} amount must be non-negative",
                {"bid_id": bid_id, "amount": amount},
            )
        normalized.append(
            SponsorBid(bid_id, advertiser, tuple(sorted(requested)), amount)
        )
    return tuple(inventory), tuple(sorted(normalized, key=lambda bid: bid.bid_id))


def _search(
    placements: tuple[str, ...], bids: tuple[SponsorBid, ...]
) -> tuple[Allocation, SearchCertificate]:
    """Enumerate every feasible subset; bounded by 2**8 subsets."""
    placement_set = set(placements)
    for bid in bids:  # Defence in depth for internal callers.
        if not set(bid.placements) <= placement_set:
            raise AuctionRejection(
                "PLACEMENT_UNKNOWN",
                f"bid {bid.bid_id!r} requests a placement outside the inventory",
            )

    candidates: list[_Candidate] = []
    bid_count = len(bids)
    for mask in range(1 << bid_count):
        chosen: list[SponsorBid] = []
        occupied: set[str] = set()
        advertisers: set[str] = set()
        total = 0
        feasible = True
        for index in range(bid_count):
            if not (mask >> index) & 1:
                continue
            bid = bids[index]
            if bid.advertiser in advertisers or occupied.intersection(bid.placements):
                feasible = False
                break
            advertisers.add(bid.advertiser)
            occupied.update(bid.placements)
            chosen.append(bid)
            total += bid.amount
        if not feasible:
            continue
        winners = tuple(
            WinningBid(bid.bid_id, bid.advertiser, bid.placements, bid.amount)
            for bid in sorted(chosen, key=lambda item: item.bid_id)
        )
        candidates.append(_Candidate(
            total=total,
            advertiser_count=len(advertisers),
            bid_ids=tuple(winner.bid_id for winner in winners),
            winners=winners,
        ))

    max_total = max(candidate.total for candidate in candidates)
    at_max = [candidate for candidate in candidates if candidate.total == max_total]
    min_advertisers = min(candidate.advertiser_count for candidate in at_max)
    at_best = [
        candidate for candidate in at_max if candidate.advertiser_count == min_advertisers
    ]
    at_best.sort(key=lambda candidate: candidate.bid_ids)
    selected = at_best[0]
    allocation = Allocation(
        winners=selected.winners,
        total_value=selected.total,
        advertiser_count=selected.advertiser_count,
    )
    certificate = SearchCertificate(
        bid_count=bid_count,
        placement_count=len(placements),
        subsets_considered=1 << bid_count,
        feasible_allocations=len(candidates),
        max_total_value=max_total,
        allocations_at_max_total=len(at_max),
        minimum_advertiser_count_at_max=min_advertisers,
        candidates_at_best=tuple(candidate.bid_ids for candidate in at_best),
        selected_bid_ids=selected.bid_ids,
        selected_advertiser_count=selected.advertiser_count,
    )
    return allocation, certificate


def search_allocation(
    placements: Iterable[str], bids: Iterable[SponsorBid]
) -> tuple[Allocation, SearchCertificate]:
    """Validate an intake round and return the exact optimal allocation."""
    inventory, normalized = validate_intake(placements, bids)
    return _search(inventory, normalized)


def solve_allocation(
    placements: Iterable[str], bids: Iterable[SponsorBid]
) -> Allocation:
    """Return only the exact optimal allocation for an intake round."""
    return search_allocation(placements, bids)[0]


def _event_public_identity(event: MergedEntity) -> dict[str, Any]:
    """The Event facts an issuance is bound to; JSON-safe and order-stable."""
    fields = {
        name: [value.value for value in values]
        for name, values in sorted(event.fields.items())
    }
    return {
        "identifier": event.identifier,
        "entity_id": event.entity_id,
        "key": event.key,
        "types": list(event.types),
        "fields": json.loads(canonical_json(fields)),
        "sources": event.source_records(),
    }


def _event_fingerprint(event: MergedEntity) -> dict[str, Any]:
    return _event_public_identity(event)


def auction_identity(
    event: MergedEntity, placements: Iterable[str], bids: Iterable[SponsorBid]
) -> str:
    """Digest everything that makes an issued auction result non-reusable.

    The Event's resolved identity and field values, the placement inventory,
    and every bid's identity, advertiser, placements, and amount participate.
    Bid and placement order do not: inputs are normalised first.
    """
    inventory, normalized = validate_intake(placements, bids)
    payload = {
        "event": _event_fingerprint(event),
        "placements": sorted(inventory),
        "bids": [bid.to_dict() for bid in normalized],
    }
    return sha256_hex(canonical_json(payload))


def verify_result(
    result: AuctionIssuance,
    event: MergedEntity,
    placements: Iterable[str],
    bids: Iterable[SponsorBid],
) -> Allocation:
    """Reject reuse of a prior result after any key identity changed."""
    current = auction_identity(event, placements, bids)
    if current != result.identity_sha256:
        raise AuctionRejection(
            "IDENTITY_CHANGED",
            "the harvested Event or auction inputs changed; the prior allocation cannot be reused",
            {"expected": result.identity_sha256, "actual": current},
        )
    return result.allocation


def parse_contracts(value: Any) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_CONTRACTS
    if not isinstance(value, (list, tuple)) or not value:
        raise AuctionRejection(
            "CONTRACTS_INVALID", "contracts must be a non-empty list",
            {"contracts": value},
        )
    parsed: list[str] = []
    for item in value:
        name = _nonempty_string(item, "CONTRACT_INVALID", "contract")
        if name not in KNOWN_CONTRACTS:
            raise AuctionRejection(
                "CONTRACT_UNKNOWN", f"unknown sales contract {name!r}",
                {"contract": name, "known": list(KNOWN_CONTRACTS)},
            )
        if name in parsed:
            raise AuctionRejection(
                "CONTRACT_DUPLICATE", f"duplicate sales contract {name!r}",
                {"contract": name},
            )
        parsed.append(name)
    return tuple(parsed)


def _verify_invoice_allocation(
    allocation: Allocation, bid_map: dict[str, SponsorBid]
) -> int:
    """Ensure invoice lines refer to the supplied round and are internally sum-consistent."""
    total = 0
    seen: set[str] = set()
    for winner in allocation.winners:
        if winner.bid_id in seen:
            raise AuctionRejection(
                "ALLOCATION_MISMATCH",
                f"bid {winner.bid_id!r} appears more than once in the allocation",
            )
        seen.add(winner.bid_id)
        source = bid_map.get(winner.bid_id)
        if source is None:
            raise AuctionRejection(
                "ALLOCATION_MISMATCH",
                f"winning bid {winner.bid_id!r} is not part of this intake",
            )
        if (
            source.advertiser != winner.advertiser
            or source.placements != winner.placements
            or source.amount != winner.amount
        ):
            raise AuctionRejection(
                "ALLOCATION_MISMATCH",
                f"winning bid {winner.bid_id!r} does not match the intake bid",
            )
        total += winner.amount
    if total != allocation.total_value:
        raise AuctionRejection(
            "ALLOCATION_MISMATCH",
            "allocation total_value does not equal the sum of winning bid amounts",
            {"total_value": allocation.total_value, "winner_amounts": total},
        )
    return total


def first_price_invoice(allocation: Allocation) -> Invoice:
    """Each winning bid pays exactly its own amount."""
    lines = tuple(
        InvoiceLine(
            bid_id=winner.bid_id,
            advertiser=winner.advertiser,
            placements=winner.placements,
            bid_amount=winner.amount,
            payment=winner.amount,
        )
        for winner in allocation.winners
    )
    return Invoice(
        contract=FIRST_PRICE,
        lines=lines,
        seller_total=sum(line.payment for line in lines),
    )


def externality_price_invoice(
    allocation: Allocation,
    placements: Iterable[str],
    bids: Iterable[SponsorBid],
) -> Invoice:
    """Charge each winner the opportunity cost of excluding its advertiser.

    For every winning advertiser, *all* of that advertiser's bids are removed
    before re-optimising.  This is what prevents a same-advertiser alternative
    from being counted as if it were still available to the rest of the round.
    A negative price means the supplied certificate is inconsistent and is a
    rejection, never a clamp to zero.
    """
    inventory, normalized = validate_intake(placements, bids)
    bid_map = {bid.bid_id: bid for bid in normalized}
    total = _verify_invoice_allocation(allocation, bid_map)

    lines: list[InvoiceLine] = []
    for winner in allocation.winners:
        excluded_bid_ids = tuple(
            bid.bid_id for bid in normalized if bid.advertiser == winner.advertiser
        )
        remaining = tuple(
            bid for bid in normalized if bid.advertiser != winner.advertiser
        )
        counterfactual, _ = _search(inventory, remaining)
        other_winners_value = total - winner.amount
        payment = counterfactual.total_value - other_winners_value
        if payment < 0:
            raise AuctionRejection(
                "NEGATIVE_EXTERNALITY_PRICE",
                f"externality price for bid {winner.bid_id!r} is negative; "
                "the optimality certificate is inconsistent",
                {
                    "bid_id": winner.bid_id,
                    "counterfactual_total_value": counterfactual.total_value,
                    "other_winners_value": other_winners_value,
                    "payment": payment,
                },
            )
        lines.append(InvoiceLine(
            bid_id=winner.bid_id,
            advertiser=winner.advertiser,
            placements=winner.placements,
            bid_amount=winner.amount,
            payment=payment,
            counterfactual={
                "excluded_advertiser": winner.advertiser,
                "excluded_bid_ids": list(excluded_bid_ids),
                "counterfactual_winning_bid_ids": list(counterfactual.bid_ids),
                "counterfactual_total_value": counterfactual.total_value,
                "other_winners_value": other_winners_value,
                "formula": "counterfactual_total_value - other_winners_value",
            },
        ))
    return Invoice(
        contract=EXTERNALITY_PRICE,
        lines=tuple(lines),
        seller_total=sum(line.payment for line in lines),
    )


def issue_auction(
    event: MergedEntity,
    placements: Iterable[str],
    bids: Iterable[SponsorBid],
    contracts: Any = DEFAULT_CONTRACTS,
) -> AuctionIssuance:
    """Issue one optimal allocation and the authorised invoices for it."""
    if "Event" not in event.types:
        raise AuctionRejection(
            "EVENT_TYPE_INVALID",
            f"entity {event.identifier!r} is not a harvested Event",
            {"identifier": event.identifier, "types": list(event.types)},
        )
    contract_names = parse_contracts(contracts)
    inventory, normalized = validate_intake(placements, bids)
    allocation, certificate = _search(inventory, normalized)
    invoices: dict[str, Invoice] = {}
    for name in contract_names:
        if name == FIRST_PRICE:
            invoices[name] = first_price_invoice(allocation)
        elif name == EXTERNALITY_PRICE:
            invoices[name] = externality_price_invoice(allocation, inventory, normalized)
    return AuctionIssuance(
        event=_event_public_identity(event),
        identity_sha256=auction_identity(event, inventory, normalized),
        placements=inventory,
        bids=normalized,
        allocation=allocation,
        certificate=certificate,
        invoices=invoices,
    )


def _bid_from_row(row: Any) -> SponsorBid:
    if not isinstance(row, dict):
        raise AuctionRejection("BID_INVALID", "each bid must be a JSON object")
    missing = [key for key in ("bid_id", "advertiser", "placements", "amount")
               if key not in row]
    if missing:
        raise AuctionRejection(
            "BID_INVALID", "bid is missing required fields",
            {"missing": missing},
        )
    raw_placements = row["placements"]
    if not isinstance(raw_placements, (list, tuple)):
        raise AuctionRejection(
            "BID_PLACEMENTS_INVALID", "bid placements must be a list",
            {"bid_id": row.get("bid_id")},
        )
    return SponsorBid(
        bid_id=row["bid_id"],
        advertiser=row["advertiser"],
        placements=tuple(raw_placements),
        amount=row["amount"],
    )


def issue_from_payload(data: Any, entities: Iterable[MergedEntity]) -> AuctionIssuance:
    """Resolve the Event, validate the intake document, and issue invoices."""
    if not isinstance(data, dict):
        raise AuctionRejection("INTAKE_INVALID", "intake must be a JSON object")
    event_id = _nonempty_string(data.get("event_id"), "EVENT_ID_INVALID", "event_id")
    event = event_from_entities(entities, event_id)
    placements = data.get("placements")
    if not isinstance(placements, (list, tuple)):
        raise AuctionRejection(
            "PLACEMENTS_INVALID", "placements must be a list",
            {"placements": placements},
        )
    rows = data.get("bids")
    if not isinstance(rows, (list, tuple)):
        raise AuctionRejection("BIDS_INVALID", "bids must be a list", {"bids": rows})
    bids = tuple(_bid_from_row(row) for row in rows)
    return issue_auction(
        event, placements, bids, parse_contracts(data.get("contracts"))
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Issue synthetic Event sponsor inventory")
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args(argv)
    data = json.loads(args.fixture.read_text(encoding="utf-8"))
    root = Path(__file__).resolve().parents[1]
    result = build_result(RunConfig.create(
        input_dir=root / "fixtures/site", contract_path=root / "fixtures/contracts/site.yaml",
        out_dir=root / "out", now="2026-01-01T00:00:00Z",
    ))
    try:
        issuance = issue_from_payload(data, result.entities)
    except AuctionRejection as exc:
        print(json.dumps({"status": "rejected", "rejection": exc.to_dict()}, sort_keys=True))
        return 2
    print(json.dumps(issuance.to_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
