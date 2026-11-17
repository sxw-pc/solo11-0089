"""Verified identity and declaration intake for a synthetic ownership graph."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .pipeline import PipelineResult


@dataclass(frozen=True)
class Holding:
    owner: str
    company: str
    economic_share: Decimal
    proxy_vote_share: Decimal


@dataclass(frozen=True)
class OwnershipIntake:
    run_id: str
    target: str
    people: tuple[str, ...]
    organizations: tuple[str, ...]
    holdings: tuple[Holding, ...]
    contract_ids: tuple[str, ...]
    control_decision: None = None


def inspect_ownership(result: PipelineResult, declarations_path: Path) -> OwnershipIntake:
    """Validate graph identities and exact direct shares without inferring control."""
    raw = json.loads(declarations_path.read_text(encoding="utf-8"), parse_float=Decimal)
    people = {item.identifier for item in result.entities if item.primary_type == "Person"}
    organizations = {item.identifier for item in result.entities if item.primary_type == "Organization"}
    if raw.get("target") not in organizations:
        raise ValueError("target organization was not harvested")
    contracts = raw.get("contracts")
    if not isinstance(contracts, list) or len(contracts) != 2:
        raise ValueError("ownership needs two declared contracts")
    contract_ids = []
    for contract in contracts:
        if contract.get("share_field") not in ("economic_share", "proxy_vote_share"):
            raise ValueError("unknown share field")
        if contract.get("control_threshold") != Decimal("0.5"):
            raise ValueError("control threshold must be one half")
        contract_ids.append(contract.get("contract_id"))
    if len(set(contract_ids)) != 2 or any(not isinstance(value, str) or not value for value in contract_ids):
        raise ValueError("contract IDs must be distinct")
    holdings = []
    seen = set()
    totals: dict[str, list[Decimal]] = {}
    for item in raw.get("declarations", []):
        owner, company = item.get("owner"), item.get("company")
        if owner not in people | organizations or company not in organizations:
            raise ValueError("holding references an unharvested identity")
        if (owner, company) in seen:
            raise ValueError("duplicate holding declaration")
        seen.add((owner, company))
        econ, vote = item.get("economic_share"), item.get("proxy_vote_share")
        if not all(isinstance(value, Decimal) and Decimal(0) <= value <= Decimal(1)
                   for value in (econ, vote)):
            raise ValueError("share must be a decimal fraction")
        holdings.append(Holding(owner, company, econ, vote))
        totals.setdefault(company, [Decimal(0), Decimal(0)])
        totals[company][0] += econ
        totals[company][1] += vote
    if not holdings or any(values != [Decimal(1), Decimal(1)] for values in totals.values()):
        raise ValueError("direct shares for each declared company must total one")
    return OwnershipIntake(result.run["run_id"], raw["target"], tuple(sorted(people)),
                           tuple(sorted(organizations)), tuple(holdings), tuple(contract_ids))
