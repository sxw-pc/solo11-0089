"""Exact rational randomized entry-screening policy issuance for a verified Event.

This module turns the prior-weighted single-check preview into an *ex ante*
randomized policy: each round executes exactly one check drawn from a public
probability distribution.  Two independent contracts are supported:

* ``deterrence_cover`` minimizes the worst-case undetected loss.  The adversary
  observes the published probabilities before choosing a single fabricated-entry
  risk (the fixture model allows at most one per round) but never sees the
  current draw, so the worst case is a maximum over risks, never a prior
  average.
* ``throughput_yield`` maximizes the prior-weighted expected avoided loss, i.e.
  buys as much screening throughput as possible for one check per round.

Both optima are solved with an exact rational linear program implemented as a
bounded vertex search over :class:`fractions.Fraction` arithmetic (at most four
risks and five checks, so every basis is enumerated).  Every issued policy
carries an offline, independently re-checkable optimality certificate; Monte
Carlo samples are never accepted as proof.  The certificate binds to the
verified Event identity and to a fingerprint of the risk/check matrix, so a
changed Event or a perturbed matrix invalidates stale certificates.

All numbers are synthetic drill parameters and make no real-world security
performance claim.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Iterable, Sequence

from .utils import canonical_json, sha256_hex

MAX_RISKS = 4
MAX_CHECKS = 5

_ZERO = Fraction(0)
_ONE = Fraction(1)


class EntryPolicyError(ValueError):
    """Raised when fixture input cannot support a certifiable entry policy."""


# ---------------------------------------------------------------------------
# Exact linear programming: bounded vertex search over Fraction arithmetic.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Constraint:
    """row . x >= rhs (equalities are added as two opposite rows)."""

    row: tuple[Fraction, ...]
    rhs: Fraction
    label: str


def _solve_basis(
    rows: list[list[Fraction]],
    rhs: list[Fraction],
) -> list[Fraction] | None:
    """Solve a square rational system via Gauss-Jordan elimination."""
    n = len(rhs)
    aug = [row[:] + [rhs[i]] for i, row in enumerate(rows)]
    row = 0
    for col in range(n):
        pivot = next((r for r in range(row, n) if aug[r][col] != _ZERO), None)
        if pivot is None:
            return None
        aug[row], aug[pivot] = aug[pivot], aug[row]
        pivot_value = aug[row][col]
        aug[row] = [value / pivot_value for value in aug[row]]
        for r in range(n):
            if r == row or aug[r][col] == _ZERO:
                continue
            factor = aug[r][col]
            aug[r] = [
                value - factor * pivot_row_value
                for value, pivot_row_value in zip(aug[r], aug[row])
            ]
        row += 1
        if row == n:
            break
    return [aug[r][n] for r in range(n)]


def solve_lp(
    variables: Sequence[str],
    constraints: Iterable[_Constraint],
    objective: Sequence[Fraction],
    minimize: bool,
) -> dict[str, Fraction]:
    """Solve an LP whose feasible polytope is a bounded simplex-ish polytope.

    Every vertex of a non-empty polyhedron is the unique solution of a square
    system formed by selecting ``n`` independent active constraint hyperplanes,
    so enumerating all ``n``-subsets of constraints and filtering for feasible
    solutions visits every vertex.  The optimum of a linear program over a
    bounded non-empty polytope is attained at a vertex.  All arithmetic is exact.
    """
    names = list(variables)
    n = len(names)
    rows = [list(c.row) for c in constraints]
    rhs = [c.rhs for c in constraints]
    if len(rows) < n:
        raise EntryPolicyError("linear program is under-constrained")
    feasible: list[tuple[Fraction, ...]] = []
    for combo in itertools.combinations(range(len(rows)), n):
        basis_rows = [rows[i] for i in combo]
        basis_rhs = [rhs[i] for i in combo]
        candidate = _solve_basis(basis_rows, basis_rhs)
        if candidate is None:
            continue
        point = tuple(candidate)
        if all(
            sum(rows[k][j] * point[j] for j in range(n)) >= rhs[k]
            for k in range(len(rows))
        ):
            feasible.append(point)
    if not feasible:
        raise EntryPolicyError("linear program has no feasible vertex")
    def score(point: tuple[Fraction, ...]) -> Fraction:
        return sum(objective[j] * point[j] for j in range(n))

    best_point = feasible[0]
    best_score = score(best_point)
    for point in feasible[1:]:
        point_score = score(point)
        if minimize:
            better = point_score < best_score
        else:
            better = point_score > best_score
        # Deterministic tie-break: lexicographically smallest vertex, so the
        # solution depends only on the inputs, never on enumeration order.
        if better or (point_score == best_score and point < best_point):
            best_point, best_score = point, point_score
    return dict(zip(names, best_point))


# ---------------------------------------------------------------------------
# Fixture validation.
# ---------------------------------------------------------------------------

def _fraction(value: Any, label: str) -> Fraction:
    """Parse a fixture-provided numeric string; bare numbers are schema errors."""
    if isinstance(value, bool) or not isinstance(value, str) or not value.strip():
        raise EntryPolicyError(f"{label} must be a non-empty numeric string")
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise EntryPolicyError(f"{label} is not a valid rational number: {value!r}") from exc


def _cert_fraction(value: Any, label: str) -> Fraction:
    """Parse values embedded in a certificate (Fraction in-process, string after JSON)."""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return Fraction(value)
    return _fraction(value, label)


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EntryPolicyError(f"{label} must be a non-empty string")
    return value


def validate_intake(data: Any) -> dict[str, Any]:
    """Validate the fixture shape and return a normalized model of exact values."""
    if not isinstance(data, dict):
        raise EntryPolicyError("intake fixture must be a JSON object")
    event_id = _require_text(data.get("event_id"), "event_id")
    raw_risks = data.get("risks")
    raw_checks = data.get("checks")
    if not isinstance(raw_risks, list) or not raw_risks:
        raise EntryPolicyError("risks must be a non-empty list")
    if not isinstance(raw_checks, list) or not raw_checks:
        raise EntryPolicyError("checks must be a non-empty list")
    if len(raw_risks) > MAX_RISKS:
        raise EntryPolicyError(f"at most {MAX_RISKS} risks are supported")
    if len(raw_checks) > MAX_CHECKS:
        raise EntryPolicyError(f"at most {MAX_CHECKS} checks are supported")

    risk_ids: list[str] = []
    risks: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_risks):
        if not isinstance(raw, dict):
            raise EntryPolicyError(f"risk at index {index} must be an object")
        risk_id = _require_text(raw.get("risk_id"), f"risks[{index}].risk_id")
        if risk_id in risk_ids:
            raise EntryPolicyError(f"duplicate risk_id: {risk_id!r}")
        loss = _fraction(raw.get("loss"), f"risk {risk_id!r} loss")
        if loss < _ZERO:
            raise EntryPolicyError(f"risk {risk_id!r} loss must be non-negative")
        prior = _fraction(raw.get("prior"), f"risk {risk_id!r} prior")
        if prior < _ZERO:
            raise EntryPolicyError(f"risk {risk_id!r} prior must be non-negative")
        risk_ids.append(risk_id)
        risks.append({"risk_id": risk_id, "loss": loss, "prior": prior})
    if sum(item["prior"] for item in risks) != _ONE:
        raise EntryPolicyError("risk priors must be non-negative and sum to 1")

    check_ids: list[str] = []
    checks: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_checks):
        if not isinstance(raw, dict):
            raise EntryPolicyError(f"check at index {index} must be an object")
        check_id = _require_text(raw.get("check_id"), f"checks[{index}].check_id")
        if check_id in check_ids:
            raise EntryPolicyError(f"duplicate check_id: {check_id!r}")
        detection_raw = raw.get("detection")
        if not isinstance(detection_raw, dict):
            raise EntryPolicyError(f"check {check_id!r} detection must be an object")
        if set(detection_raw) != set(risk_ids):
            raise EntryPolicyError(
                f"check {check_id!r} must give a detection rate for every risk"
            )
        detection: dict[str, Fraction] = {}
        for risk_id in risk_ids:
            rate = _fraction(
                detection_raw[risk_id],
                f"check {check_id!r} detection[{risk_id}]",
            )
            if not (_ZERO <= rate <= _ONE):
                raise EntryPolicyError(
                    f"check {check_id!r} detection[{risk_id}] must lie in [0, 1]"
                )
            detection[risk_id] = rate
        check_ids.append(check_id)
        checks.append({"check_id": check_id, "detection": detection})

    contracts_raw = data.get("contracts")
    if not isinstance(contracts_raw, dict):
        raise EntryPolicyError("contracts must be an object")
    thresholds: dict[str, Fraction] = {}
    for contract_name, key in (
        ("deterrence_cover", "max_worst_loss"),
        ("throughput_yield", "min_expected_avoided"),
    ):
        raw_contract = contracts_raw.get(contract_name)
        if not isinstance(raw_contract, dict) or key not in raw_contract:
            raise EntryPolicyError(f"contract {contract_name!r} requires {key!r}")
        threshold = _fraction(raw_contract[key], f"contract {contract_name!r}.{key}")
        if threshold < _ZERO:
            raise EntryPolicyError(f"contract {contract_name!r} threshold must be non-negative")
        thresholds[contract_name] = threshold

    return {
        "event_id": event_id,
        # Canonical ordering makes solutions and fingerprints independent of
        # the order risks/checks happen to appear in the source JSON.
        "risks": sorted(risks, key=lambda item: item["risk_id"]),
        "checks": sorted(checks, key=lambda item: item["check_id"]),
        "thresholds": thresholds,
    }


def require_verified_event(event_id: str, entities: Sequence[Any]) -> None:
    """Reject the policy unless exactly one harvested entity is this Event."""
    matches = [
        entity
        for entity in entities
        if getattr(entity, "identifier", None) == event_id
        and "Event" in getattr(entity, "types", ())
    ]
    if len(matches) != 1:
        raise EntryPolicyError(
            f"entry policy requires exactly one harvested Event matching {event_id!r}; "
            f"found {len(matches)}"
        )


# ---------------------------------------------------------------------------
# Fingerprinting: binds every certificate to the Event and the exact matrix.
# ---------------------------------------------------------------------------

def model_fingerprint(model: dict[str, Any]) -> str:
    """Stable hash of event identity, risks, checks, and contract thresholds."""
    payload = {
        "event_id": model["event_id"],
        "risks": [
            {
                "risk_id": risk["risk_id"],
                "loss": str(risk["loss"]),
                "prior": str(risk["prior"]),
            }
            for risk in model["risks"]
        ],
        "checks": [
            {
                "check_id": check["check_id"],
                "detection": {
                    risk_id: str(rate) for risk_id, rate in check["detection"].items()
                },
            }
            for check in model["checks"]
        ],
        "thresholds": {name: str(value) for name, value in model["thresholds"].items()},
    }
    return sha256_hex(canonical_json(payload))


def _residual_matrix(model: dict[str, Any]) -> dict[tuple[str, str], Fraction]:
    """Residual undetected loss L[a, r] = loss_r * (1 - detection[a, r])."""
    return {
        (check["check_id"], risk["risk_id"]):
            risk["loss"] * (_ONE - check["detection"][risk["risk_id"]])
        for check in model["checks"]
        for risk in model["risks"]
    }


# ---------------------------------------------------------------------------
# Certificates (each is a plain JSON-able dict, independently re-checkable).
# ---------------------------------------------------------------------------

def minimax_primal_dual(model: dict[str, Any]) -> dict[str, Any]:
    """Solve the deterrence LP and its dual with exact vertex searches.

    Primal (policy p over checks, slack t = worst residual loss)::

        minimize t
        s.t. sum_a p[a] L[a, r] <= t          for every risk r
             sum_a p[a] = 1, p[a] >= 0

    Dual (adversary q over risks, slack v)::

        maximize v
        s.t. sum_r q[r] L[a, r] >= v          for every check a
             sum_r q[r] = 1, q[r] >= 0

    Equal objective values certify global optimality: the primal is a feasible
    upper bound on the worst loss, the adversary mixture a feasible lower bound
    on what every pure check suffers, and strong duality closes the gap.
    """
    check_ids = [check["check_id"] for check in model["checks"]]
    risk_ids = [risk["risk_id"] for risk in model["risks"]]
    residual = _residual_matrix(model)

    primal_vars = check_ids + ["__t__"]
    primal_constraints: list[_Constraint] = [
        _Constraint(
            tuple(
                [-residual[(check_id, risk_id)] for check_id in check_ids] + [_ONE]
            ),
            _ZERO,
            f"worst_loss:{risk_id}",
        )
        for risk_id in risk_ids
    ]
    primal_constraints.append(
        _Constraint(tuple([_ONE] * len(check_ids) + [_ZERO]), _ONE, "simplex_eq")
    )
    primal_constraints.append(
        _Constraint(tuple([-_ONE] * len(check_ids) + [_ZERO]), -_ONE, "simplex_eq")
    )
    for check_id in check_ids:
        row = [_ZERO] * len(primal_vars)
        row[primal_vars.index(check_id)] = _ONE
        primal_constraints.append(_Constraint(tuple(row), _ZERO, f"nonneg:{check_id}"))
    primal_objective = [_ZERO] * len(check_ids) + [_ONE]
    primal_solution = solve_lp(primal_vars, primal_constraints, primal_objective, True)
    policy = {check_id: primal_solution[check_id] for check_id in check_ids}
    worst_loss = primal_solution["__t__"]

    dual_vars = risk_ids + ["__v__"]
    dual_constraints: list[_Constraint] = [
        _Constraint(
            tuple([residual[(check_id, risk_id)] for risk_id in risk_ids] + [-_ONE]),
            _ZERO,
            f"pure_check_floor:{check_id}",
        )
        for check_id in check_ids
    ]
    dual_constraints.append(
        _Constraint(tuple([_ONE] * len(risk_ids) + [_ZERO]), _ONE, "adversary_eq")
    )
    dual_constraints.append(
        _Constraint(tuple([-_ONE] * len(risk_ids) + [_ZERO]), -_ONE, "adversary_eq")
    )
    for risk_id in risk_ids:
        row = [_ZERO] * len(dual_vars)
        row[dual_vars.index(risk_id)] = _ONE
        dual_constraints.append(_Constraint(tuple(row), _ZERO, f"nonneg:{risk_id}"))
    dual_objective = [_ZERO] * len(risk_ids) + [_ONE]
    dual_solution = solve_lp(dual_vars, dual_constraints, dual_objective, False)
    adversary = {risk_id: dual_solution[risk_id] for risk_id in risk_ids}
    adversary_value = dual_solution["__v__"]

    return {
        "kind": "minimax_primal_dual",
        "policy": policy,
        "worst_loss": worst_loss,
        "adversary_mixture": adversary,
        "adversary_value": adversary_value,
    }


def verify_minimax_certificate(model: dict[str, Any], certificate: dict[str, Any]) -> None:
    """Re-derive every certificate claim from raw fixture values."""
    if certificate.get("kind") != "minimax_primal_dual":
        raise EntryPolicyError("wrong certificate kind for deterrence_cover")
    check_ids = [check["check_id"] for check in model["checks"]]
    risk_ids = [risk["risk_id"] for risk in model["risks"]]
    residual = _residual_matrix(model)

    policy = certificate.get("policy")
    adversary = certificate.get("adversary_mixture")
    if not isinstance(policy, dict) or set(policy) != set(check_ids):
        raise EntryPolicyError("certificate policy must name every check exactly once")
    if not isinstance(adversary, dict) or set(adversary) != set(risk_ids):
        raise EntryPolicyError("certificate adversary must name every risk exactly once")
    probabilities = {name: _cert_fraction(value, f"policy[{name}]") for name, value in policy.items()}
    mixture = {name: _cert_fraction(value, f"adversary[{name}]") for name, value in adversary.items()}
    if any(value < _ZERO for value in probabilities.values()):
        raise EntryPolicyError("certificate policy has a negative probability")
    if any(value < _ZERO for value in mixture.values()):
        raise EntryPolicyError("certificate adversary mixture has a negative probability")
    if sum(probabilities.values()) != _ONE:
        raise EntryPolicyError("certificate policy probabilities must sum to 1")
    if sum(mixture.values()) != _ONE:
        raise EntryPolicyError("certificate adversary mixture must sum to 1")

    risk_residual = {
        risk_id: sum(
            probabilities[check_id] * residual[(check_id, risk_id)]
            for check_id in check_ids
        )
        for risk_id in risk_ids
    }
    worst_loss = _cert_fraction(certificate.get("worst_loss"), "certificate worst_loss")
    if max(risk_residual.values()) != worst_loss:
        raise EntryPolicyError(
            "certificate worst_loss does not match recomputed per-risk residuals"
        )
    for risk_id, value in risk_residual.items():
        if value > worst_loss:
            raise EntryPolicyError(f"primal feasibility violated for risk {risk_id!r}")

    check_floor = {
        check_id: sum(
            mixture[risk_id] * residual[(check_id, risk_id)] for risk_id in risk_ids
        )
        for check_id in check_ids
    }
    adversary_value = _cert_fraction(
        certificate.get("adversary_value"), "certificate adversary_value"
    )
    if min(check_floor.values()) != adversary_value:
        raise EntryPolicyError(
            "certificate adversary_value does not match recomputed pure-check floors"
        )
    for check_id, value in check_floor.items():
        if value < adversary_value:
            raise EntryPolicyError(f"dual feasibility violated for check {check_id!r}")
    if worst_loss != adversary_value:
        raise EntryPolicyError(
            "primal and dual bounds differ; global optimality is not certified"
        )


def throughput_optimum(model: dict[str, Any]) -> dict[str, Any]:
    """Maximize prior-weighted expected avoided loss over the check simplex.

    The objective is linear in the policy and the feasible region is the
    probability simplex, so an exact vertex search returns a pure check (or the
    lexicographically first tied check).  The certificate records every pure
    check's expected value, proving no other vertex can beat the chosen one.
    """
    check_ids = [check["check_id"] for check in model["checks"]]
    variables = check_ids
    constraints = [
        _Constraint(tuple([_ONE] * len(check_ids)), _ONE, "simplex_eq"),
        _Constraint(tuple([-_ONE] * len(check_ids)), -_ONE, "simplex_eq"),
    ]
    for index, check_id in enumerate(check_ids):
        row = [_ZERO] * len(check_ids)
        row[index] = _ONE
        constraints.append(_Constraint(tuple(row), _ZERO, f"nonneg:{check_id}"))

    def expected(check_id: str) -> Fraction:
        check = next(item for item in model["checks"] if item["check_id"] == check_id)
        return sum(
            risk["prior"] * risk["loss"] * check["detection"][risk["risk_id"]]
            for risk in model["risks"]
        )

    objective = [expected(check_id) for check_id in check_ids]
    solution = solve_lp(variables, constraints, objective, False)
    policy = {check_id: solution[check_id] for check_id in check_ids}
    chosen = next(check_id for check_id in check_ids if policy[check_id] == _ONE)
    pure_values = {check_id: expected(check_id) for check_id in check_ids}
    expected_avoided = pure_values[chosen]
    return {
        "kind": "simplex_extreme_point",
        "policy": policy,
        "chosen_check": chosen,
        "expected_avoided": expected_avoided,
        "pure_check_values": pure_values,
    }


def verify_throughput_certificate(model: dict[str, Any], certificate: dict[str, Any]) -> None:
    """Re-check simplex feasibility and the all-vertices dominance claim."""
    if certificate.get("kind") != "simplex_extreme_point":
        raise EntryPolicyError("wrong certificate kind for throughput_yield")
    check_ids = [check["check_id"] for check in model["checks"]]
    policy = certificate.get("policy")
    if not isinstance(policy, dict) or set(policy) != set(check_ids):
        raise EntryPolicyError("certificate policy must name every check exactly once")
    probabilities = {name: _cert_fraction(value, f"policy[{name}]") for name, value in policy.items()}
    if any(value < _ZERO for value in probabilities.values()):
        raise EntryPolicyError("certificate policy has a negative probability")
    if sum(probabilities.values()) != _ONE:
        raise EntryPolicyError("certificate policy probabilities must sum to 1")

    def expected(check_id: str) -> Fraction:
        check = next(item for item in model["checks"] if item["check_id"] == check_id)
        return sum(
            risk["prior"] * risk["loss"] * check["detection"][risk["risk_id"]]
            for risk in model["risks"]
        )

    pure_values = {
        name: _cert_fraction(value, f"pure_check_values[{name}]")
        for name, value in certificate.get("pure_check_values", {}).items()
    }
    if set(pure_values) != set(check_ids):
        raise EntryPolicyError("certificate must score every pure check")
    for check_id in check_ids:
        if pure_values[check_id] != expected(check_id):
            raise EntryPolicyError(
                f"certificate pure-check value for {check_id!r} does not recompute"
            )
    chosen = certificate.get("chosen_check")
    if chosen not in probabilities or probabilities[chosen] != _ONE:
        raise EntryPolicyError("throughput optimum must be an extreme-point pure check")
    expected_avoided = _cert_fraction(
        certificate.get("expected_avoided"), "certificate expected_avoided"
    )
    if expected_avoided != pure_values[chosen]:
        raise EntryPolicyError("certificate expected_avoided does not match chosen check")
    if max(pure_values.values()) != expected_avoided:
        raise EntryPolicyError("another pure check beats the certified policy")


# ---------------------------------------------------------------------------
# Per-policy analytics and full issuance.
# ---------------------------------------------------------------------------

def _policy_breakdown(model: dict[str, Any], policy: dict[str, Fraction]) -> dict[str, Any]:
    check_ids = [check["check_id"] for check in model["checks"]]
    residual = _residual_matrix(model)
    per_risk: dict[str, dict[str, Any]] = {}
    for risk in model["risks"]:
        risk_id = risk["risk_id"]
        coverage = sum(
            policy[check["check_id"]] * check["detection"][risk_id]
            for check in model["checks"]
        )
        remaining = risk["loss"] * (_ONE - coverage)
        direct = sum(
            policy[check_id] * residual[(check_id, risk_id)] for check_id in check_ids
        )
        assert direct == remaining
        per_risk[risk_id] = {
            "prior": risk["prior"],
            "loss": risk["loss"],
            "detection_coverage": coverage,
            "residual_loss": remaining,
        }
    expected_avoided = sum(
        item["prior"] * (item["loss"] - item["residual_loss"])
        for item in per_risk.values()
    )
    return {
        "per_risk": per_risk,
        "expected_avoided_loss": expected_avoided,
        "worst_residual_loss": max(
            item["residual_loss"] for item in per_risk.values()
        ),
    }


def _fraction_map(values: dict[str, Fraction]) -> dict[str, str]:
    return {name: str(value) for name, value in values.items()}


def _certificate_to_jsonable(certificate: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"kind": certificate["kind"]}
    for key, value in certificate.items():
        if key == "kind":
            continue
        if isinstance(value, dict):
            result[key] = {name: str(fraction) for name, fraction in value.items()}
        elif isinstance(value, Fraction):
            result[key] = str(value)
        else:
            result[key] = value
    return result


def issue_policies(data: dict[str, Any], entities: Sequence[Any]) -> dict[str, Any]:
    """Validate the fixture, verify the Event, and issue or deny both contracts."""
    model = validate_intake(data)
    require_verified_event(model["event_id"], entities)
    fingerprint = model_fingerprint(model)
    residual = _residual_matrix(model)

    minimax = minimax_primal_dual(model)
    verify_minimax_certificate(model, minimax)
    deterrence_breakdown = _policy_breakdown(model, minimax["policy"])
    deterrence_threshold = model["thresholds"]["deterrence_cover"]
    deterrence_ok = minimax["worst_loss"] <= deterrence_threshold

    throughput = throughput_optimum(model)
    verify_throughput_certificate(model, throughput)
    throughput_breakdown = _policy_breakdown(model, throughput["policy"])
    throughput_threshold = model["thresholds"]["throughput_yield"]
    throughput_ok = throughput["expected_avoided"] >= throughput_threshold

    def contract_block(
        contract_name: str,
        certificate: dict[str, Any],
        breakdown: dict[str, Any],
        threshold: Fraction,
        threshold_field: str,
        issued: bool,
        comparison: str,
    ) -> dict[str, Any]:
        if issued:
            reason = (
                f"issued: certified optimum satisfies {contract_name}.{threshold_field} "
                f"({comparison})"
            )
        else:
            reason = (
                f"denied: certified optimum fails {contract_name}.{threshold_field} "
                f"({comparison}); threshold was not relaxed"
            )
        return {
            "status": "issued" if issued else "denied",
            "reason": reason,
            "action_probabilities": _fraction_map(certificate["policy"]),
            "per_risk": {
                risk_id: {
                    key: str(value) if isinstance(value, Fraction) else value
                    for key, value in details.items()
                }
                for risk_id, details in breakdown["per_risk"].items()
            },
            "expected_avoided_loss": str(breakdown["expected_avoided_loss"]),
            "worst_residual_loss": str(breakdown["worst_residual_loss"]),
            "threshold": str(threshold),
            "certificate": _certificate_to_jsonable(certificate),
        }

    return {
        "event_id": model["event_id"],
        "model_fingerprint": fingerprint,
        "model": json.loads(canonical_json({
            "risks": [
                {
                    "risk_id": risk["risk_id"],
                    "loss": str(risk["loss"]),
                    "prior": str(risk["prior"]),
                }
                for risk in model["risks"]
            ],
            "checks": [
                {
                    "check_id": check["check_id"],
                    "detection": {
                        risk_id: str(rate)
                        for risk_id, rate in check["detection"].items()
                    },
                }
                for check in model["checks"]
            ],
            "residual_loss": {
                f"{check_id}|{risk_id}": str(value)
                for (check_id, risk_id), value in sorted(residual.items())
            },
        })),
        "contracts": {
            "deterrence_cover": contract_block(
                "deterrence_cover",
                minimax,
                deterrence_breakdown,
                deterrence_threshold,
                "max_worst_loss",
                deterrence_ok,
                f"{minimax['worst_loss']} <= {deterrence_threshold}",
            ),
            "throughput_yield": contract_block(
                "throughput_yield",
                throughput,
                throughput_breakdown,
                throughput_threshold,
                "min_expected_avoided",
                throughput_ok,
                f"{throughput['expected_avoided']} >= {throughput_threshold}",
            ),
        },
    }


def verify_issued_report(data: dict[str, Any], entities: Sequence[Any], report: dict[str, Any]) -> None:
    """Offline re-verification: re-solve from raw inputs and bind the fingerprint."""
    model = validate_intake(data)
    require_verified_event(model["event_id"], entities)
    if report.get("model_fingerprint") != model_fingerprint(model):
        raise EntryPolicyError(
            "stale certificate: Event or risk/check matrix no longer matches the report"
        )
    contracts = report.get("contracts")
    if not isinstance(contracts, dict):
        raise EntryPolicyError("report must contain both contracts")
    verify_minimax_certificate(
        model, contracts["deterrence_cover"]["certificate"]
    )
    verify_throughput_certificate(
        model, contracts["throughput_yield"]["certificate"]
    )
