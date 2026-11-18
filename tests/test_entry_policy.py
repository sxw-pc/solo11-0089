from __future__ import annotations

import copy
import json
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

from microtag_harvest.config import RunConfig
from microtag_harvest.entry_policy import (
    EntryPolicyError,
    issue_policies,
    minimax_primal_dual,
    model_fingerprint,
    require_verified_event,
    throughput_optimum,
    validate_intake,
    verify_issued_report,
    verify_minimax_certificate,
    verify_throughput_certificate,
)
from microtag_harvest.entry_screening_intake import preview, main as intake_main
from microtag_harvest.pipeline import build_result

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures/entry_screening/intake.json"
WITNESS_PATH = ROOT / "fixtures/entry_screening/witness.json"
SITE = ROOT / "fixtures/site"
CONTRACT = ROOT / "fixtures/contracts/site.yaml"
FIXED_NOW = "2026-01-01T00:00:00Z"


def load_data() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def build_entities(tmp_path: Path):
    result = build_result(RunConfig.create(
        input_dir=SITE, contract_path=CONTRACT,
        out_dir=tmp_path / "out", now=FIXED_NOW,
    ))
    return result.entities


def event_entities(event_id: str, count: int = 1):
    """Synthetic stand-ins for harvested MergedEntity objects."""
    entities = [
        SimpleNamespace(identifier=event_id, types=("Event",))
        for _ in range(count)
    ]
    entities.append(SimpleNamespace(identifier=event_id, types=("Person",)))
    entities.append(SimpleNamespace(identifier="https://example.invalid/other", types=("Event",)))
    return entities


# ---------------------------------------------------------------------------
# Legacy prior preview (backward-compatible behavior).
# ---------------------------------------------------------------------------

def test_prior_preview_lacks_expiry_cover(tmp_path: Path):
    data = load_data()
    entities = build_entities(tmp_path)
    assert preview(data, entities) == {
        "event_id": data["event_id"], "check_id": "dual-quick", "expected_avoided_loss": 6.225,
    }
    assert Fraction(data["risks"][2]["loss"]) * (
        1 - Fraction(data["checks"][3]["detection"]["expired-pass"])
    ) == 8


# ---------------------------------------------------------------------------
# 1. Fixed-fraction witness: primal upper bound == adversary lower bound.
# ---------------------------------------------------------------------------

def test_fixture_witness_policy_and_adversary_match_exact_fractions(tmp_path: Path):
    data = load_data()
    witness = json.loads(WITNESS_PATH.read_text(encoding="utf-8"))
    expected_policy = {k: Fraction(v) for k, v in witness["deterrence_policy"].items()}
    expected_adversary = {k: Fraction(v) for k, v in witness["adversary_policy"].items()}
    expected_value = Fraction(witness["optimal_worst_loss"])

    model = validate_intake(data)
    minimax = minimax_primal_dual(model)
    verify_minimax_certificate(model, minimax)

    # Fractions compare by value, so 9/29 (solver's reduced form) equals 27/87.
    assert minimax["policy"] == expected_policy
    assert minimax["adversary_mixture"] == expected_adversary
    assert minimax["worst_loss"] == minimax["adversary_value"] == expected_value

    # The issued report carries the witness policy in exact fractions; the
    # 27/87 witness spelling remains value-identical to the solver's 9/29.
    report = issue_policies(data, event_entities(data["event_id"]))
    issued_policy = {
        name: Fraction(value)
        for name, value in report["contracts"]["deterrence_cover"]
        ["action_probabilities"].items()
    }
    for check_id, witness_probability in expected_policy.items():
        assert issued_policy[check_id] == witness_probability
    assert Fraction(
        report["contracts"]["deterrence_cover"]["certificate"]["policy"]["expiry-review"]
    ) == Fraction(witness["deterrence_policy"]["expiry-review"])

    residual = {
        (check["check_id"], risk["risk_id"]):
            Fraction(risk["loss"]) * (1 - Fraction(check["detection"][risk["risk_id"]]))
        for check in data["checks"] for risk in data["risks"]
    }
    # Upper bound: adversary picks the worst risk against the published policy.
    upper = max(
        sum(expected_policy[a] * residual[a, r] for a in expected_policy)
        for r in expected_adversary
    )
    # Lower bound: every pure check suffers at least the same against the mixture.
    lower = min(
        sum(expected_adversary[r] * residual[a, r] for r in expected_adversary)
        for a in expected_policy
    )
    assert upper == lower == expected_value
    assert expected_value < Fraction(6) < Fraction(8)

    report = issue_policies(data, event_entities(data["event_id"]))
    block = report["contracts"]["deterrence_cover"]
    assert block["status"] == "issued"
    assert block["worst_residual_loss"] == "160/29"
    assert Fraction(block["threshold"]) == 6


# ---------------------------------------------------------------------------
# 2. Pure dual-quick misses expired-pass: throughput contract documents it.
# ---------------------------------------------------------------------------

def test_pure_dual_quick_misses_expired_pass_and_throughput_contract_shows_it(tmp_path: Path):
    data = load_data()
    model = validate_intake(data)
    throughput = throughput_optimum(model)
    verify_throughput_certificate(model, throughput)

    assert throughput["chosen_check"] == "dual-quick"
    assert throughput["policy"]["dual-quick"] == 1
    assert throughput["expected_avoided"] == Fraction("6.225") == Fraction(249, 40)

    report = issue_policies(data, event_entities(data["event_id"]))
    block = report["contracts"]["throughput_yield"]
    assert block["status"] == "issued"
    assert block["action_probabilities"]["dual-quick"] == "1"
    expired = block["per_risk"]["expired-pass"]
    assert expired["detection_coverage"] == "0"
    assert expired["residual_loss"] == "8"  # full loss survives
    assert block["worst_residual_loss"] == "8"
    assert block["expected_avoided_loss"] == "249/40"
    assert Fraction(block["expected_avoided_loss"]) >= Fraction(block["threshold"]) == 6


# ---------------------------------------------------------------------------
# 3. Adversary sees the policy and chooses the worst risk.
# ---------------------------------------------------------------------------

def test_adversary_selects_worst_risk_after_observing_policy():
    data = load_data()
    model = validate_intake(data)
    minimax = minimax_primal_dual(model)
    policy = minimax["policy"]
    residual = {
        (check["check_id"], risk["risk_id"]):
            risk["loss"] * (1 - check["detection"][risk["risk_id"]])
        for check in model["checks"] for risk in model["risks"]
    }
    per_risk = {
        risk["risk_id"]: sum(
            policy[check_id] * residual[check_id, risk["risk_id"]]
            for check_id in policy
        )
        for risk in model["risks"]
    }
    worst_risk = max(per_risk, key=lambda name: (per_risk[name], name))
    assert per_risk[worst_risk] == minimax["worst_loss"]
    # At the optimum every risk is equally painful: no adversary free lunch.
    assert set(per_risk.values()) == {Fraction(160, 29)}

    # A naive policy (pure dual-quick) lets the adversary pick expired-pass for 8.
    naive = {check_id: Fraction(1 if check_id == "dual-quick" else 0)
             for check_id in policy}
    naive_per_risk = {
        risk_id: sum(naive[a] * residual[a, risk_id] for a in naive)
        for risk_id in per_risk
    }
    assert max(naive_per_risk.values()) == 8
    assert naive_per_risk["expired-pass"] == 8


# ---------------------------------------------------------------------------
# 4. Matrix perturbation: changed matrix changes policy, certificate, fingerprint.
# ---------------------------------------------------------------------------

def test_matrix_perturbation_changes_policy_certificate_and_fingerprint():
    data = load_data()
    base_model = validate_intake(data)
    base = minimax_primal_dual(base_model)
    base_fingerprint = model_fingerprint(base_model)

    perturbed = copy.deepcopy(data)
    # dual-quick now catches 1/4 of expired passes; the whole LP must re-solve.
    perturbed["checks"][3]["detection"]["expired-pass"] = "1/4"
    perturbed_model = validate_intake(perturbed)
    assert model_fingerprint(perturbed_model) != base_fingerprint

    perturbed_solution = minimax_primal_dual(perturbed_model)
    verify_minimax_certificate(perturbed_model, perturbed_solution)
    assert perturbed_solution["policy"] != base["policy"]
    assert perturbed_solution["worst_loss"] < base["worst_loss"]

    # A stale report bound to the old fingerprint must be rejected, not re-trusted.
    old_report = issue_policies(data, event_entities(data["event_id"]))
    with pytest.raises(EntryPolicyError, match="stale certificate"):
        verify_issued_report(perturbed, event_entities(data["event_id"]), old_report)

    # Re-solving on the perturbed input yields a fresh, verifiable report.
    fresh_report = issue_policies(perturbed, event_entities(data["event_id"]))
    verify_issued_report(
        perturbed, event_entities(data["event_id"]), fresh_report
    )


def test_loss_perturbation_recomputes_witness_rather_than_hardcoding_fixture():
    data = load_data()
    perturbed = copy.deepcopy(data)
    perturbed["risks"][2]["loss"] = "20"  # expired pass becomes much costlier
    model = validate_intake(perturbed)
    solution = minimax_primal_dual(model)
    verify_minimax_certificate(model, solution)
    # The hard-coded fixture witness 160/29 can no longer be the answer.
    assert solution["worst_loss"] != Fraction(160, 29)
    assert solution["policy"]["expiry-review"] > Fraction(27, 87)


# ---------------------------------------------------------------------------
# 5. Probability conservation and [0, 1] detection coverage.
# ---------------------------------------------------------------------------

def test_probabilities_are_non_negative_and_sum_to_one():
    data = load_data()
    model = validate_intake(data)
    for solution in (minimax_primal_dual(model), throughput_optimum(model)):
        values = list(solution["policy"].values())
        assert all(value >= 0 for value in values)
        assert sum(values) == 1
    minimax = minimax_primal_dual(model)
    adversary = list(minimax["adversary_mixture"].values())
    assert all(value >= 0 for value in adversary)
    assert sum(adversary) == 1

    report = issue_policies(data, event_entities(data["event_id"]))
    for block in report["contracts"].values():
        probabilities = [Fraction(value) for value in block["action_probabilities"].values()]
        assert all(value >= 0 for value in probabilities)
        assert sum(probabilities) == 1
        for details in block["per_risk"].values():
            assert Fraction(0) <= Fraction(details["detection_coverage"]) <= Fraction(1)


# ---------------------------------------------------------------------------
# 6. Threshold that even the optimum cannot pass => denial, no threshold change.
# ---------------------------------------------------------------------------

def test_unachievable_threshold_denies_contract_without_relaxing_it():
    data = load_data()
    strict = copy.deepcopy(data)
    strict["contracts"]["deterrence_cover"]["max_worst_loss"] = "5"  # optimum is 160/29 ~= 5.517
    report = issue_policies(strict, event_entities(data["event_id"]))
    block = report["contracts"]["deterrence_cover"]
    assert block["status"] == "denied"
    assert block["threshold"] == "5"
    assert Fraction(block["worst_residual_loss"]) == Fraction(160, 29) > 5
    assert "not relaxed" in block["reason"]

    weak = copy.deepcopy(data)
    weak["contracts"]["throughput_yield"]["min_expected_avoided"] = "7"  # optimum is 6.225
    report_weak = issue_policies(weak, event_entities(data["event_id"]))
    block_weak = report_weak["contracts"]["throughput_yield"]
    assert block_weak["status"] == "denied"
    assert block_weak["threshold"] == "7"
    assert Fraction(block_weak["expected_avoided_loss"]) == Fraction(249, 40) < 7


# ---------------------------------------------------------------------------
# 7. Unknown / duplicated Event identity invalidates the whole issuance.
# ---------------------------------------------------------------------------

def test_unknown_or_duplicate_event_is_rejected(tmp_path: Path):
    data = load_data()
    with pytest.raises(EntryPolicyError, match="exactly one harvested Event"):
        require_verified_event(data["event_id"], event_entities("https://example.invalid/nope"))
    with pytest.raises(EntryPolicyError, match="exactly one harvested Event"):
        require_verified_event(data["event_id"], event_entities(data["event_id"], count=2))

    real_entities = build_entities(tmp_path)
    tampered = copy.deepcopy(data)
    tampered["event_id"] = "https://example.invalid/events/spring#impostor"
    with pytest.raises(EntryPolicyError, match="exactly one harvested Event"):
        issue_policies(tampered, real_entities)

    # The genuine chain issues, and the report fingerprint carries the identity.
    report = issue_policies(data, real_entities)
    verify_issued_report(data, real_entities, report)


# ---------------------------------------------------------------------------
# 8. Duplicate risk/check IDs.
# ---------------------------------------------------------------------------

def test_duplicate_ids_are_rejected():
    data = load_data()
    dup_risk = copy.deepcopy(data)
    dup_risk["risks"][1]["risk_id"] = dup_risk["risks"][0]["risk_id"]
    with pytest.raises(EntryPolicyError, match="duplicate risk_id"):
        validate_intake(dup_risk)

    dup_check = copy.deepcopy(data)
    dup_check["checks"][2]["check_id"] = dup_check["checks"][0]["check_id"]
    with pytest.raises(EntryPolicyError, match="duplicate check_id"):
        validate_intake(dup_check)


# ---------------------------------------------------------------------------
# 9. Invalid priors, losses, and detection rates.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d["risks"][0].__setitem__("prior", "0.30"), "priors must"),
        (lambda d: d["risks"][0].__setitem__("prior", "-0.1"), "non-negative"),
        (lambda d: d["risks"][1].__setitem__("loss", "-2"), "non-negative"),
        (lambda d: d["checks"][0]["detection"].__setitem__("name-swap", "1.2"), r"\[0, 1\]"),
        (lambda d: d["checks"][0]["detection"].__setitem__("cloned-qr", "-0.1"), r"\[0, 1\]"),
        (lambda d: d["checks"][1]["detection"].pop("expired-pass"), "every risk"),
        (lambda d: d["checks"][1]["detection"].__setitem__("ghost", "0.5"), "every risk"),
        (lambda d: d["risks"][0].__setitem__("prior", "nonsense"), "not a valid rational"),
        (lambda d: d["risks"][0].__setitem__("prior", 0.4), "numeric string"),
        (lambda d: d["checks"][0]["detection"].__setitem__("name-swap", True), "numeric string"),
    ],
)
def test_invalid_priors_losses_and_detection_rates(mutate, message):
    data = load_data()
    mutate(data)
    with pytest.raises(EntryPolicyError, match=message):
        validate_intake(data)


def test_empty_or_oversized_lists_are_rejected():
    data = load_data()
    empty_risks = copy.deepcopy(data)
    empty_risks["risks"] = []
    with pytest.raises(EntryPolicyError):
        validate_intake(empty_risks)

    too_many = copy.deepcopy(load_data())
    base_check = too_many["checks"][0]
    for index in range(5):
        extra = copy.deepcopy(base_check)
        extra["check_id"] = f"extra-{index}"
        too_many["checks"].append(extra)
    assert len(too_many["checks"]) == 9
    with pytest.raises(EntryPolicyError, match="at most 5 checks"):
        validate_intake(too_many)

    too_many_risks = copy.deepcopy(load_data())
    base_risk = too_many_risks["risks"][0]
    for index in range(4):
        extra = copy.deepcopy(base_risk)
        extra["risk_id"] = f"extra-risk-{index}"
        extra["prior"] = "0"
        too_many_risks["risks"].append(extra)
    for check in too_many_risks["checks"]:
        for index in range(4):
            check["detection"][f"extra-risk-{index}"] = "0"
    assert len(too_many_risks["risks"]) == 7
    with pytest.raises(EntryPolicyError, match="at most 4 risks"):
        validate_intake(too_many_risks)


# ---------------------------------------------------------------------------
# 10. Input reordering: same inputs => identical policy and fingerprint.
# ---------------------------------------------------------------------------

def test_reordering_inputs_does_not_change_policy_or_fingerprint():
    data = load_data()
    reordered = copy.deepcopy(data)
    reordered["risks"] = list(reversed(reordered["risks"]))
    reordered["checks"] = list(reversed(reordered["checks"]))
    for check in reordered["checks"]:
        check["detection"] = {
            key: check["detection"][key]
            for key in reversed(list(check["detection"]))
        }

    base_model = validate_intake(data)
    reordered_model = validate_intake(reordered)
    assert model_fingerprint(base_model) == model_fingerprint(reordered_model)

    base_solution = minimax_primal_dual(base_model)
    reordered_solution = minimax_primal_dual(reordered_model)
    assert reordered_solution["policy"] == base_solution["policy"]
    assert reordered_solution["adversary_mixture"] == base_solution["adversary_mixture"]
    assert reordered_solution["worst_loss"] == base_solution["worst_loss"]

    base_report = issue_policies(data, event_entities(data["event_id"]))
    reordered_report = issue_policies(reordered, event_entities(data["event_id"]))
    for contract_name in base_report["contracts"]:
        assert (
            base_report["contracts"][contract_name]["action_probabilities"]
            == reordered_report["contracts"][contract_name]["action_probabilities"]
        )


# ---------------------------------------------------------------------------
# 11. The two contracts impose different real obligations.
# ---------------------------------------------------------------------------

def test_two_contracts_issue_different_action_obligations():
    data = load_data()
    report = issue_policies(data, event_entities(data["event_id"]))
    deterrence = report["contracts"]["deterrence_cover"]
    throughput = report["contracts"]["throughput_yield"]

    assert deterrence["action_probabilities"] != throughput["action_probabilities"]
    # Deterrence spreads weight over three checks including the expiry review;
    # throughput puts the whole round on dual-quick.
    assert Fraction(deterrence["action_probabilities"]["expiry-review"]) > 0
    assert Fraction(deterrence["action_probabilities"]["dual-quick"]) < 1
    assert throughput["action_probabilities"]["dual-quick"] == "1"
    assert throughput["action_probabilities"]["expiry-review"] == "0"

    # Deterrence buys a worst-case promise (5.517...); throughput cannot (8).
    assert Fraction(deterrence["worst_residual_loss"]) == Fraction(160, 29) < 6
    assert Fraction(throughput["worst_residual_loss"]) == 8
    # Throughput buys the best prior-average yield; deterrence sacrifices some.
    assert Fraction(throughput["expected_avoided_loss"]) > Fraction(
        deterrence["expected_avoided_loss"]
    )


# ---------------------------------------------------------------------------
# 12. Tampered certificates fail independent re-verification.
# ---------------------------------------------------------------------------

def test_tampered_certificates_are_rejected_by_verifiers():
    data = load_data()
    model = validate_intake(data)
    minimax = minimax_primal_dual(model)
    tampered = copy.deepcopy(minimax)
    tampered["worst_loss"] = Fraction(1)  # too good to be true
    with pytest.raises(EntryPolicyError, match="worst_loss does not match"):
        verify_minimax_certificate(model, tampered)

    broken_dual = copy.deepcopy(minimax)
    broken_dual["adversary_mixture"]["expired-pass"] = Fraction(
        broken_dual["adversary_mixture"]["expired-pass"]
    ) + Fraction(1, 29)
    with pytest.raises(EntryPolicyError, match="mixture must sum to 1"):
        verify_minimax_certificate(model, broken_dual)

    throughput = throughput_optimum(model)
    broken_throughput = copy.deepcopy(throughput)
    broken_throughput["pure_check_values"]["dual-quick"] = Fraction(99)
    with pytest.raises(EntryPolicyError, match="does not recompute"):
        verify_throughput_certificate(model, broken_throughput)

    report = issue_policies(data, event_entities(data["event_id"]))
    json_roundtrip = json.loads(json.dumps(report, sort_keys=True))
    verify_issued_report(data, event_entities(data["event_id"]), json_roundtrip)
    json_roundtrip["contracts"]["deterrence_cover"]["certificate"]["worst_loss"] = "1"
    with pytest.raises(EntryPolicyError):
        verify_issued_report(
            data, event_entities(data["event_id"]), json_roundtrip
        )


# ---------------------------------------------------------------------------
# 13. Full end-to-end through the real harvested entities.
# ---------------------------------------------------------------------------

def test_tied_objectives_break_deterministically():
    data = load_data()
    tied = copy.deepcopy(data)
    # Make qr-verify score identically to dual-quick but keep distinct risks.
    tied["checks"][1]["detection"] = {
        "name-swap": "0.75", "cloned-qr": "0.75", "expired-pass": "0",
    }
    model = validate_intake(tied)
    first = throughput_optimum(model)
    verify_throughput_certificate(model, first)
    assert first["pure_check_values"]["qr-verify"] == first["pure_check_values"]["dual-quick"]
    # Repeated solves with identical inputs return the identical vertex.
    again = throughput_optimum(validate_intake(copy.deepcopy(tied)))
    assert again["chosen_check"] == first["chosen_check"]
    assert again["policy"] == first["policy"]

    # Reversed input order must produce the same winner after canonical sorting.
    reversed_data = copy.deepcopy(tied)
    reversed_data["checks"] = list(reversed(reversed_data["checks"]))
    second = throughput_optimum(validate_intake(reversed_data))
    assert second["chosen_check"] == first["chosen_check"]


def test_end_to_end_issuance_against_harvested_event(tmp_path: Path):
    data = load_data()
    entities = build_entities(tmp_path)
    report = issue_policies(data, entities)
    verify_issued_report(data, entities, report)
    assert report["event_id"] == data["event_id"]
    assert len(report["model_fingerprint"]) == 64
    assert {block["status"] for block in report["contracts"].values()} == {"issued"}
    for block in report["contracts"].values():
        assert block["reason"].startswith("issued")


# ---------------------------------------------------------------------------
# 14. CLI surface: exit codes for the valid fixture and an invalid one.
# ---------------------------------------------------------------------------

def test_cli_issues_report_for_fixture(capsys: pytest.CaptureFixture[str]):
    code = intake_main(["--fixture", str(FIXTURE)])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["event_id"] == load_data()["event_id"]
    verify_issued_report(
        load_data(),
        event_entities(load_data()["event_id"]),
        report,
    )


def test_cli_returns_error_code_for_invalid_fixture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    bad = load_data()
    bad["risks"][0]["prior"] = "0.30"  # priors no longer sum to 1
    bad_path = tmp_path / "bad_intake.json"
    bad_path.write_text(json.dumps(bad), encoding="utf-8")
    code = intake_main(["--fixture", str(bad_path)])
    assert code == 2
    assert "priors must" in capsys.readouterr().err


def test_cli_returns_error_code_for_missing_fixture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    code = intake_main(["--fixture", str(tmp_path / "missing.json")])
    assert code == 2
    assert capsys.readouterr().err.startswith("error:")
