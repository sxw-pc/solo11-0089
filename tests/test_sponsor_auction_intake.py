from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from microtag_harvest.config import RunConfig
from microtag_harvest.merge import MergedEntity
from microtag_harvest.pipeline import build_result
from microtag_harvest.sponsor_auction_intake import (
    AuctionRejection,
    SponsorBid,
    atomic_preview,
    auction_identity,
    event_from_entities,
    externality_price_invoice,
    first_price_invoice,
    issue_auction,
    issue_from_payload,
    main,
    search_allocation,
    solve_allocation,
    validate_intake,
    verify_result,
)

ROOT = Path(__file__).resolve().parents[1]
INTAKE_PATH = ROOT / "fixtures/sponsor_auction/intake.json"


def _build_entities(tmp_path: Path):
    return build_result(RunConfig.create(
        input_dir=ROOT / "fixtures/site", contract_path=ROOT / "fixtures/contracts/site.yaml",
        out_dir=tmp_path / "out", now="2026-01-01T00:00:00Z",
    )).entities


@pytest.fixture(scope="module")
def entities() -> list[MergedEntity]:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        return _build_entities(Path(tmp))


@pytest.fixture(scope="module")
def fixture_intake() -> dict:
    return json.loads(INTAKE_PATH.read_text(encoding="utf-8"))


def _bids(rows) -> list[SponsorBid]:
    return [SponsorBid(row["bid_id"], row["advertiser"], tuple(row["placements"]), row["amount"])
            for row in rows]


def _fixture_bids(data: dict) -> list[SponsorBid]:
    return _bids(data["bids"])


def _event(entities: list[MergedEntity], data: dict) -> MergedEntity:
    return event_from_entities(entities, data["event_id"])


# ---------------------------------------------------------------------------
# Baseline binding and the legacy atomic preview
# ---------------------------------------------------------------------------

def test_sponsor_preview_binds_to_harvested_event(tmp_path: Path):
    intake = json.loads(INTAKE_PATH.read_text(encoding="utf-8"))
    event = event_from_entities(_build_entities(tmp_path), intake["event_id"])
    assert event.identifier == intake["event_id"]
    bids = _fixture_bids(intake)
    assert atomic_preview(intake["placements"], bids) == {
        "bid_ids": ["b6", "b3", "b4"], "welfare": 24, "first_price_total": 24,
    }


def test_missing_harvested_event_is_rejected(tmp_path: Path):
    result = build_result(RunConfig.create(
        input_dir=ROOT / "fixtures/site", contract_path=ROOT / "fixtures/contracts/site.yaml",
        out_dir=tmp_path / "out", now="2026-01-01T00:00:00Z",
    ))
    with pytest.raises(AuctionRejection) as caught:
        event_from_entities(result.entities, "https://example.invalid/events/missing#event")
    assert caught.value.code == "EVENT_NOT_UNIQUE"
    assert "found 0" in str(caught.value)


def test_non_event_entity_is_rejected(entities, fixture_intake):
    person = next(entity for entity in entities if "Person" in entity.types)
    with pytest.raises(AuctionRejection) as caught:
        issue_auction(person, fixture_intake["placements"], _fixture_bids(fixture_intake))
    assert caught.value.code == "EVENT_TYPE_INVALID"


# ---------------------------------------------------------------------------
# Bundle optimisation versus the atomic preview
# ---------------------------------------------------------------------------

def test_bundle_allocation_beats_atomic_preview(entities, fixture_intake):
    allocation, certificate = search_allocation(
        fixture_intake["placements"], _fixture_bids(fixture_intake)
    )
    assert allocation.bid_ids == ("b1", "b4")
    assert allocation.total_value == 25
    assert certificate.max_total_value == 25
    assert certificate.allocations_at_max_total == 1
    assert certificate.candidates_at_best == (("b1", "b4"),)
    assert atomic_preview(fixture_intake["placements"], _fixture_bids(fixture_intake))["welfare"] == 24
    assert allocation.total_value > 24
    assert allocation.occupied_placements() == {"hero": "b1", "sidebar": "b1", "footer": "b4"}


def test_same_advertiser_alternatives_are_mutually_exclusive(entities, fixture_intake):
    allocation = solve_allocation(
        fixture_intake["placements"], _fixture_bids(fixture_intake)
    )
    winners = {winner.bid_id: winner.advertiser for winner in allocation.winners}
    assert ("b1" in winners) != ("b6" in winners)
    assert list(winners.values()).count("adv1") <= 1
    assert "b6" not in winners


def test_placement_conflicts_prevent_double_selling(entities, fixture_intake):
    allocation = solve_allocation(
        fixture_intake["placements"], _fixture_bids(fixture_intake)
    )
    sold = allocation.occupied_placements()
    assert sorted(sold) == sorted(fixture_intake["placements"])
    assert len(sold) == len(set(sold))
    # b1 occupies hero+sidebar, so b2 and b3 cannot also win.
    assert "b2" not in allocation.bid_ids
    assert "b3" not in allocation.bid_ids


def test_bundle_loses_when_singles_are_more_valuable():
    placements = ["hero", "sidebar"]
    bids = [
        SponsorBid("z1", "adv1", ("hero", "sidebar"), 10),
        SponsorBid("a1", "adv2", ("hero",), 9),
        SponsorBid("a2", "adv3", ("sidebar",), 9),
    ]
    allocation = solve_allocation(placements, bids)
    assert allocation.bid_ids == ("a1", "a2")
    assert allocation.total_value == 18


# ---------------------------------------------------------------------------
# Tie-breaks and deterministic input handling
# ---------------------------------------------------------------------------

def test_equal_value_prefers_fewer_winning_advertisers():
    placements = ["hero", "sidebar"]
    bids = [
        SponsorBid("x1", "adv9", ("hero", "sidebar"), 20),
        SponsorBid("a1", "adv1", ("hero",), 10),
        SponsorBid("a2", "adv2", ("sidebar",), 10),
    ]
    allocation, certificate = search_allocation(placements, bids)
    assert allocation.bid_ids == ("x1",)
    assert allocation.total_value == 20
    assert allocation.advertiser_count == 1
    assert certificate.allocations_at_max_total == 2
    assert certificate.minimum_advertiser_count_at_max == 1


def test_equal_value_uses_sorted_bid_id_tuple():
    placements = ["hero", "sidebar"]
    bids = [
        SponsorBid("b1", "adv3", ("hero",), 5),
        SponsorBid("b2", "adv4", ("sidebar",), 5),
        SponsorBid("a2", "adv2", ("sidebar",), 5),
        SponsorBid("a1", "adv1", ("hero",), 5),
    ]
    allocation, certificate = search_allocation(placements, bids)
    assert allocation.bid_ids == ("a1", "a2")
    assert certificate.allocations_at_max_total == 4
    assert certificate.candidates_at_best[0] == ("a1", "a2")
    assert list(certificate.candidates_at_best) == sorted(certificate.candidates_at_best)


def test_input_reordering_is_stable(entities, fixture_intake):
    placements = fixture_intake["placements"]
    bids = _fixture_bids(fixture_intake)
    baseline = issue_auction(_event(entities, fixture_intake), placements, bids)
    shuffled = [
        *reversed(bids),
    ]
    reordered_placements = list(reversed(placements))
    disturbed = issue_auction(
        _event(entities, fixture_intake), reordered_placements, shuffled
    )
    assert disturbed.allocation.to_dict() == baseline.allocation.to_dict()
    assert disturbed.certificate.to_dict() == baseline.certificate.to_dict()
    assert disturbed.identity_sha256 == baseline.identity_sha256
    assert auction_identity(_event(entities, fixture_intake), placements, bids) == baseline.identity_sha256


# ---------------------------------------------------------------------------
# Invoices: first price and externality price
# ---------------------------------------------------------------------------

def test_first_price_invoice_charges_winning_bids(entities, fixture_intake):
    issuance = issue_auction(
        _event(entities, fixture_intake),
        fixture_intake["placements"],
        _fixture_bids(fixture_intake),
        contracts=["first_price"],
    )
    invoice = issuance.invoices["first_price"]
    assert [(line.bid_id, line.payment) for line in invoice.lines] == [("b1", 20), ("b4", 5)]
    assert invoice.seller_total == 25


def test_externality_invoice_removes_all_alternatives(entities, fixture_intake):
    issuance = issue_auction(
        _event(entities, fixture_intake),
        fixture_intake["placements"],
        _fixture_bids(fixture_intake),
        contracts=["externality_price"],
    )
    invoice = issuance.invoices["externality_price"]
    lines = {line.bid_id: line for line in invoice.lines}
    assert lines["b1"].payment == 18
    assert lines["b1"].counterfactual["excluded_bid_ids"] == ["b1", "b6"]
    assert lines["b1"].counterfactual["counterfactual_winning_bid_ids"] == ["b2", "b3", "b4"]
    assert lines["b1"].counterfactual["counterfactual_total_value"] == 23
    assert lines["b1"].counterfactual["other_winners_value"] == 5
    assert lines["b4"].payment == 0
    assert lines["b4"].counterfactual["counterfactual_total_value"] == 20
    assert lines["b4"].counterfactual["other_winners_value"] == 20
    assert invoice.seller_total == 18


def test_forgetting_a_same_advertiser_alternative_would_overcharge(entities, fixture_intake):
    """Dropping only the winning bid yields 19; removing b6 as well yields 18."""
    placements = fixture_intake["placements"]
    bids = _fixture_bids(fixture_intake)
    issuance = issue_auction(
        _event(entities, fixture_intake), placements, bids,
        contracts=["externality_price"],
    )
    correct = issuance.invoices["externality_price"].lines[0].payment
    assert correct == 18

    # Naive counterfactual: remove b1 only, leaving the sibling b6 in the round.
    naive_round = [bid for bid in bids if bid.bid_id != "b1"]
    naive_best = solve_allocation(placements, naive_round)
    naive_payment = naive_best.total_value - 5
    # b6 now takes hero, so the round is worth b6+b3+b4 = 24, not 23.
    assert naive_best.bid_ids == ("b3", "b4", "b6")
    assert naive_payment == 19
    assert naive_payment != correct


def test_invoices_share_delivery_but_differ_in_revenue(entities, fixture_intake):
    issuance = issue_auction(
        _event(entities, fixture_intake),
        fixture_intake["placements"],
        _fixture_bids(fixture_intake),
    )
    first = issuance.invoices["first_price"]
    external = issuance.invoices["externality_price"]
    delivery = [(line.bid_id, line.placements) for line in first.lines]
    assert delivery == [(line.bid_id, line.placements) for line in external.lines]
    assert first.seller_total == 25
    assert external.seller_total == 18
    assert first.seller_total != external.seller_total
    assert issuance.to_dict()["welfare"] == 25


def test_negative_externality_price_is_rejected_not_clamped():
    placements = ["hero"]
    bids = [
        SponsorBid("a1", "adv1", ("hero",), 100),
        SponsorBid("a2", "adv2", ("hero",), 100),
        SponsorBid("a3", "adv3", ("hero",), 100),
    ]
    allocation = solve_allocation(placements, bids)
    # Forge an allocation that double-sells hero to make the supplied certificate
    # inconsistent: the other winners' value exceeds any feasible counterfactual.
    forged = replace(
        allocation,
        winners=tuple(
            type(allocation.winners[0])(
                bid.bid_id, bid.advertiser, bid.placements, bid.amount
            )
            for bid in bids
        ),
        total_value=300,
        advertiser_count=3,
    )
    with pytest.raises(AuctionRejection) as caught:
        externality_price_invoice(forged, placements, bids)
    assert caught.value.code == "NEGATIVE_EXTERNALITY_PRICE"
    assert caught.value.details["payment"] < 0


# ---------------------------------------------------------------------------
# Validation and rejection reasons
# ---------------------------------------------------------------------------

def test_unknown_event_is_rejected(entities):
    payload = {
        "event_id": "https://example.invalid/events/unknown#event",
        "placements": ["hero"], "bids": [],
    }
    with pytest.raises(AuctionRejection) as caught:
        issue_from_payload(payload, entities)
    assert caught.value.code == "EVENT_NOT_UNIQUE"


def test_unknown_placement_is_rejected(entities, fixture_intake):
    bids = _fixture_bids(fixture_intake) + [
        SponsorBid("b7", "adv5", ("banner",), 4)
    ]
    with pytest.raises(AuctionRejection) as caught:
        issue_auction(_event(entities, fixture_intake), fixture_intake["placements"], bids)
    assert caught.value.code == "PLACEMENT_UNKNOWN"
    assert caught.value.details["placement"] == "banner"


def test_duplicate_bid_id_is_rejected(entities, fixture_intake):
    bids = _fixture_bids(fixture_intake) + [
        SponsorBid("b2", "adv5", ("footer",), 1)
    ]
    with pytest.raises(AuctionRejection) as caught:
        issue_auction(_event(entities, fixture_intake), fixture_intake["placements"], bids)
    assert caught.value.code == "BID_ID_DUPLICATE"


def test_duplicate_placement_in_bid_is_rejected(entities, fixture_intake):
    bids = [SponsorBid("b1", "adv1", ("hero", "hero"), 20)]
    with pytest.raises(AuctionRejection) as caught:
        issue_auction(_event(entities, fixture_intake), fixture_intake["placements"], bids)
    assert caught.value.code == "BID_PLACEMENT_DUPLICATE"


def test_duplicate_placement_inventory_is_rejected(entities, fixture_intake):
    with pytest.raises(AuctionRejection) as caught:
        issue_auction(
            _event(entities, fixture_intake), ["hero", "hero"], _fixture_bids(fixture_intake)
        )
    assert caught.value.code == "PLACEMENT_DUPLICATE"


def test_blank_advertiser_is_rejected(entities, fixture_intake):
    bids = [SponsorBid("b1", "  ", ("hero",), 20)]
    with pytest.raises(AuctionRejection) as caught:
        issue_auction(_event(entities, fixture_intake), fixture_intake["placements"], bids)
    assert caught.value.code == "ADVERTISER_EMPTY"


def test_bid_without_placements_is_rejected(entities, fixture_intake):
    bids = [SponsorBid("b1", "adv1", (), 20)]
    with pytest.raises(AuctionRejection) as caught:
        issue_auction(_event(entities, fixture_intake), fixture_intake["placements"], bids)
    assert caught.value.code == "BID_PLACEMENTS_EMPTY"


@pytest.mark.parametrize("amount", [-1, -100])
def test_negative_amount_is_rejected(entities, fixture_intake, amount):
    bids = [SponsorBid("b1", "adv1", ("hero",), amount)]
    with pytest.raises(AuctionRejection) as caught:
        issue_auction(_event(entities, fixture_intake), fixture_intake["placements"], bids)
    assert caught.value.code == "AMOUNT_NEGATIVE"


@pytest.mark.parametrize("amount", [1.5, "20", True, None])
def test_non_integer_amount_is_rejected(entities, fixture_intake, amount):
    bids = [SponsorBid("b1", "adv1", ("hero",), amount)]
    with pytest.raises(AuctionRejection) as caught:
        issue_auction(_event(entities, fixture_intake), fixture_intake["placements"], bids)
    assert caught.value.code == "AMOUNT_INVALID"


def test_no_bids_is_rejected(entities, fixture_intake):
    with pytest.raises(AuctionRejection) as caught:
        issue_auction(_event(entities, fixture_intake), fixture_intake["placements"], [])
    assert caught.value.code == "NO_BIDS"


def test_bounded_search_rejects_oversized_inputs(entities, fixture_intake):
    too_many_placements = ["p1", "p2", "p3", "p4", "p5"]
    bids = [SponsorBid("b1", "adv1", ("p1",), 1)]
    with pytest.raises(AuctionRejection) as caught:
        validate_intake(too_many_placements, bids)
    assert caught.value.code == "SEARCH_BOUND_EXCEEDED"

    too_many_bids = [
        SponsorBid(f"b{i}", f"adv{i}", ("hero",), i) for i in range(1, 10)
    ]
    with pytest.raises(AuctionRejection) as caught:
        validate_intake(fixture_intake["placements"], too_many_bids)
    assert caught.value.code == "SEARCH_BOUND_EXCEEDED"


def test_eight_bids_and_four_placements_are_supported(entities, fixture_intake):
    placements = ["hero", "sidebar", "footer", "rail"]
    bids = [
        SponsorBid("b1", "adv1", ("hero",), 8),
        SponsorBid("b2", "adv2", ("sidebar",), 7),
        SponsorBid("b3", "adv3", ("footer",), 6),
        SponsorBid("b4", "adv4", ("rail",), 5),
        SponsorBid("b5", "adv5", ("hero", "sidebar"), 10),
        SponsorBid("b6", "adv6", ("footer", "rail"), 9),
        SponsorBid("b7", "adv7", ("hero", "rail"), 4),
        SponsorBid("b8", "adv8", ("sidebar", "footer"), 3),
    ]
    allocation, certificate = search_allocation(placements, bids)
    assert certificate.subsets_considered == 256
    assert allocation.total_value == 26
    assert allocation.bid_ids == ("b1", "b2", "b3", "b4")


# ---------------------------------------------------------------------------
# Refusing to reuse a stale result
# ---------------------------------------------------------------------------

def test_result_is_bound_to_event_identity(entities, fixture_intake):
    event = _event(entities, fixture_intake)
    issuance = issue_auction(event, fixture_intake["placements"], _fixture_bids(fixture_intake))
    assert verify_result(
        issuance, event, fixture_intake["placements"], _fixture_bids(fixture_intake)
    ).bid_ids == ("b1", "b4")

    changed_event = replace(event, entity_id="000000000000")
    with pytest.raises(AuctionRejection) as caught:
        verify_result(
            issuance, changed_event, fixture_intake["placements"], _fixture_bids(fixture_intake)
        )
    assert caught.value.code == "IDENTITY_CHANGED"


def test_result_is_bound_to_bid_identity(entities, fixture_intake):
    event = _event(entities, fixture_intake)
    bids = _fixture_bids(fixture_intake)
    issuance = issue_auction(event, fixture_intake["placements"], bids)
    changed_bids = [
        SponsorBid(bid.bid_id, bid.advertiser, bid.placements, bid.amount + 1)
        for bid in bids
    ]
    with pytest.raises(AuctionRejection) as caught:
        verify_result(issuance, event, fixture_intake["placements"], changed_bids)
    assert caught.value.code == "IDENTITY_CHANGED"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_issues_both_invoices(capsys):
    assert main(["--fixture", str(INTAKE_PATH)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "issued"
    assert payload["allocation"]["winning_bid_ids"] == ["b1", "b4"]
    assert payload["total_value"] == 25
    assert payload["invoices"]["first_price"]["seller_total"] == 25
    assert payload["invoices"]["externality_price"]["seller_total"] == 18
    assert payload["certificate"]["optimality"]["exact"] is True


def test_cli_rejection_returns_nonzero(tmp_path: Path, capsys):
    fixture = tmp_path / "bad.json"
    fixture.write_text(json.dumps({
        "event_id": "https://example.invalid/events/missing#event",
        "placements": ["hero"],
        "bids": [{"bid_id": "b1", "advertiser": "adv1", "placements": ["hero"], "amount": 1}],
    }), encoding="utf-8")
    assert main(["--fixture", str(fixture)]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "rejected"
    assert payload["rejection"]["code"] == "EVENT_NOT_UNIQUE"
