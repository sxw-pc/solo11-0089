"""Entity-level contract verification: complementary, conflicting and missing
sources must produce distinct, evidence-bound business conclusions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from microtag_harvest.config import RunConfig
from microtag_harvest.contracts import ContractError, load_contract
from microtag_harvest.merge import merge_annotations
from microtag_harvest.pipeline import build_result, execute
from microtag_harvest.queries import get_entity, list_entities, list_field_evidence
from microtag_harvest.report import golden_summary
from microtag_harvest.store import connect
from microtag_harvest.validation import (
    ENTITY_FAIL_CONFLICT,
    ENTITY_FAIL_MISSING,
    ENTITY_INELIGIBLE,
    ENTITY_PASS,
    FIELD_COMPLEMENTARY,
    FIELD_CONFLICT,
    FIELD_MISSING,
    FIELD_PRESENT,
    validate_annotations,
    verify_entities,
)

JOINT_CONTRACT = Path(__file__).resolve().parents[1] / "fixtures" / "contracts" / "site.yaml"
INDEPENDENT_CONTRACT = (
    Path(__file__).resolve().parents[1] / "fixtures" / "contracts" / "site_independent.yaml"
)
ORG_ID = "https://example.invalid/orgs#acme"
FIXED_NOW = "2026-01-01T00:00:00Z"


def _org(annotation_factory, *, syntax, locator, values, doc_path="page.html",
         identifier=ORG_ID):
    return annotation_factory(
        types=("Organization",),
        syntax=syntax,
        locator=locator,
        doc_path=doc_path,
        identifier=identifier,
        base_url="https://example.invalid/",
        values=values,
    )


def _verification(annotations, contract):
    # Per-source annotation validation runs first, exactly like the pipeline.
    source_issues = validate_annotations(annotations, contract)
    entities, merge_issues = merge_annotations(annotations, contract)
    verifications, entity_issues = verify_entities(entities, contract)
    assert len(verifications) == 1
    return entities[0], verifications[0], [*source_issues, *merge_issues, *entity_issues]


def _field_map(verification):
    result = verification.type_results[0]
    return {item.field: item for item in result.field_evidence}


# ---------------------------------------------------------------------------
# Mode semantics on the identical complementary input
# ---------------------------------------------------------------------------

def test_complementary_sources_pass_in_joint_mode(contract_path, annotation_factory):
    contract = load_contract(contract_path)
    assert contract.entity_verification.mode == "joint"
    jsonld = _org(annotation_factory, syntax="json-ld", locator="entity[0]",
                  values={"name": "Acme Cooperative"})
    microdata = _org(annotation_factory, syntax="microdata", locator="/html/body/div[1]",
                     values={"url": "https://example.invalid/acme"})
    entity, verification, issues = _verification([jsonld, microdata], contract)

    assert verification.verdict == ENTITY_PASS
    fields = _field_map(verification)
    # The two states must not be conflated with plain presence or with missing.
    assert fields["name"].status == FIELD_COMPLEMENTARY
    assert fields["url"].status == FIELD_COMPLEMENTARY
    assert fields["name"].annotation_ids == (jsonld.annotation_id,)
    assert fields["url"].annotation_ids == (microdata.annotation_id,)
    # Per-source audit is retained even though the entity passes.
    per_source = [issue for issue in issues if issue.code == "CONTRACT_MISSING_REQUIRED"]
    assert {issue.field_name for issue in per_source} == {"name", "url"}
    assert not [issue for issue in issues
                if issue.code in {"ENTITY_REQUIREMENT_MISSING", "ENTITY_REQUIREMENT_CONFLICT"}]
    # The entity dict carries the same verification payload.
    assert entity.to_dict()["verification"]["verdict"] == ENTITY_PASS


def test_same_complementary_input_fails_in_independent_mode(annotation_factory):
    contract = load_contract(INDEPENDENT_CONTRACT)
    assert contract.entity_verification.mode == "independent"
    jsonld = _org(annotation_factory, syntax="json-ld", locator="entity[0]",
                  values={"name": "Acme Cooperative"})
    microdata = _org(annotation_factory, syntax="microdata", locator="/html/body/div[1]",
                     values={"url": "https://example.invalid/acme"})
    _entity, verification, issues = _verification([jsonld, microdata], contract)

    assert verification.verdict == ENTITY_FAIL_MISSING
    fields = _field_map(verification)
    assert fields["name"].status == FIELD_COMPLEMENTARY
    assert fields["url"].status == FIELD_COMPLEMENTARY
    entity_issue = next(issue for issue in issues if issue.code == "ENTITY_REQUIREMENT_MISSING")
    # The same two annotation ids bind the entity-level conclusion.
    assert set(entity_issue.details["annotation_ids"]) == {
        jsonld.annotation_id, microdata.annotation_id
    }
    # Per-source problems are still recorded in both modes.
    assert len([i for i in issues if i.code == "CONTRACT_MISSING_REQUIRED"]) == 2


def test_complete_sources_pass_under_both_modes(annotation_factory):
    complete = {"name": "Acme Cooperative", "url": "https://example.invalid/acme"}
    for contract_path in (JOINT_CONTRACT, INDEPENDENT_CONTRACT):
        contract = load_contract(contract_path)
        jsonld = _org(annotation_factory, syntax="json-ld", locator="entity[0]",
                      values=dict(complete))
        microdata = _org(annotation_factory, syntax="microdata",
                         locator="/html/body/div[1]", values=dict(complete))
        _entity, verification, _issues = _verification([jsonld, microdata], contract)
        assert verification.verdict == ENTITY_PASS
        assert all(item.status == FIELD_PRESENT
                   for item in verification.type_results[0].field_evidence)


# ---------------------------------------------------------------------------
# Conflicts block both modes and never complete an entity
# ---------------------------------------------------------------------------

def test_conflicting_name_blocks_pass_even_when_fields_exist(annotation_factory):
    for contract_path in (JOINT_CONTRACT, INDEPENDENT_CONTRACT):
        contract = load_contract(contract_path)
        jsonld = _org(annotation_factory, syntax="json-ld", locator="entity[0]",
                      values={"name": "Acme North", "url": "https://example.invalid/acme"})
        microdata = _org(annotation_factory, syntax="microdata",
                         locator="/html/body/div[1]",
                         values={"name": "Acme South", "url": "https://example.invalid/acme"})
        entity, verification, issues = _verification([jsonld, microdata], contract)

        assert verification.verdict == ENTITY_FAIL_CONFLICT
        fields = _field_map(verification)
        assert fields["name"].status == FIELD_CONFLICT
        assert fields["url"].status == FIELD_PRESENT
        # Conflict evidence keeps every contributing annotation/locator/hash.
        name_evidence = fields["name"]
        assert set(name_evidence.annotation_ids) == {
            jsonld.annotation_id, microdata.annotation_id
        }
        assert set(name_evidence.locators) == {
            jsonld.locator + "#name", microdata.locator + "#name"
        }
        assert set(name_evidence.fragment_sha256) == {
            jsonld.fragment_sha256, microdata.fragment_sha256
        }
        conflict_issue = next(
            issue for issue in issues if issue.code == "ENTITY_REQUIREMENT_CONFLICT"
        )
        assert conflict_issue.details["entity_id"] == entity.entity_id
        assert conflict_issue.field_name == "name"
        assert {value for value in conflict_issue.details["values"]} == {
            "Acme North", "Acme South"
        }


def test_complement_and_conflict_are_distinct_on_one_entity(annotation_factory):
    """name conflicts while url is complementary: conflict must dominate and
    the complementary field must not be silently treated as complete."""
    contract = load_contract(JOINT_CONTRACT)
    jsonld = _org(annotation_factory, syntax="json-ld", locator="entity[0]",
                  values={"name": "Acme North"})
    microdata = _org(annotation_factory, syntax="microdata", locator="/html/body/div[1]",
                     values={"name": "Acme South", "url": "https://example.invalid/acme"})
    _entity, verification, _issues = _verification([jsonld, microdata], contract)
    fields = _field_map(verification)
    assert verification.verdict == ENTITY_FAIL_CONFLICT
    assert fields["name"].status == FIELD_CONFLICT
    assert fields["url"].status == FIELD_COMPLEMENTARY


# ---------------------------------------------------------------------------
# Missing vs unknown/ineligible must stay distinct
# ---------------------------------------------------------------------------

def test_missing_field_is_distinct_from_unknown_type(annotation_factory):
    contract = load_contract(JOINT_CONTRACT)
    known = _org(annotation_factory, syntax="json-ld", locator="entity[0]",
                 values={"name": "Acme Cooperative"})  # url missing
    unknown = annotation_factory(
        types=("Mystery",), syntax="microdata", locator="/html/body/div[1]",
        doc_path="page.html", identifier=ORG_ID,
        base_url="https://example.invalid/",
        values={"url": "https://example.invalid/acme"},
    )
    entities, _merge_issues = merge_annotations([known, unknown], contract)
    assert len(entities) == 1  # same @id stays grouped; types are never mixed away
    source_issues = validate_annotations([known, unknown], contract)
    verifications, issues = verify_entities(entities, contract)
    issues = [*source_issues, *issues]
    verification = verifications[0]

    fields = _field_map(verification)
    assert fields["name"].status == FIELD_PRESENT
    # The unknown-type annotation's url must NOT satisfy the Organization field.
    assert fields["url"].status == FIELD_MISSING
    assert verification.verdict == ENTITY_FAIL_MISSING
    assert unknown.annotation_id in verification.ineligible_annotation_ids
    assert known.annotation_id in verification.eligible_annotation_ids
    assert any(issue.code == "CONTRACT_UNKNOWN_TYPE" for issue in issues)
    assert any(issue.code == "ENTITY_REQUIREMENT_MISSING" for issue in issues)


def test_entity_with_only_unknown_type_is_ineligible_not_complete(annotation_factory):
    contract = load_contract(JOINT_CONTRACT)
    unknown = annotation_factory(
        types=("Mystery",), syntax="json-ld", locator="entity[0]",
        doc_path="page.html", identifier=ORG_ID,
        base_url="https://example.invalid/",
        values={"name": "Acme Cooperative", "url": "https://example.invalid/acme"},
    )
    entities, _ = merge_annotations([unknown], contract)
    verifications, issues = verify_entities(entities, contract)
    verification = verifications[0]
    assert verification.verdict == ENTITY_INELIGIBLE
    assert "unknown_type" in verification.reasons
    assert verification.type_results == ()
    ineligible_issue = next(
        issue for issue in issues if issue.code == "ENTITY_VERIFICATION_INELIGIBLE"
    )
    assert ineligible_issue.details["entity_id"] == verification.entity_id
    # No field evidence rows can exist without a declared type: never fake completeness.
    assert ineligible_issue.details["ineligible_annotation_ids"] == [unknown.annotation_id]


def test_same_page_multi_syntax_duplicate_passes_without_conflict(annotation_factory):
    contract = load_contract(JOINT_CONTRACT)
    values = {"name": "Acme Cooperative", "url": "https://example.invalid/acme"}
    jsonld = _org(annotation_factory, syntax="json-ld", locator="entity[0]",
                  identifier="https://example.invalid/#o", values=values)
    microdata = _org(annotation_factory, syntax="microdata",
                     locator="/html/body/div[1]",
                     identifier="https://example.invalid/#o", values=values)
    entities, _ = merge_annotations([jsonld, microdata], contract)
    assert len(entities) == 1
    verifications, _ = verify_entities(entities, contract)
    verification = verifications[0]
    assert entities[0].conflicts == ()
    assert verification.verdict == ENTITY_PASS
    fields = _field_map(verification)
    # Same normalized value on every eligible source is plain presence.
    assert fields["name"].status == FIELD_PRESENT
    assert fields["url"].status == FIELD_PRESENT
    assert set(fields["name"].annotation_ids) == {
        jsonld.annotation_id, microdata.annotation_id
    }


def test_cross_document_same_id_merges_and_keeps_evidence_per_source(annotation_factory):
    contract = load_contract(JOINT_CONTRACT)
    first = _org(annotation_factory, syntax="json-ld", locator="entity[0]",
                 doc_path="a.html", values={"name": "Acme Cooperative"})
    second = _org(annotation_factory, syntax="microdata",
                  locator="/html/body/div[1]", doc_path="b.html",
                  values={"url": "https://example.invalid/acme"})
    entities, _ = merge_annotations([first, second], contract)
    assert len(entities) == 1
    verifications, _ = verify_entities(entities, contract)
    verification = verifications[0]
    assert verification.verdict == ENTITY_PASS
    fields = _field_map(verification)
    # Each document contributes one complementary field; evidence stays bound
    # to the annotation that actually provided it.
    assert fields["name"].annotation_ids == (first.annotation_id,)
    assert fields["url"].annotation_ids == (second.annotation_id,)


def test_distinct_ids_never_merge_even_with_same_name(annotation_factory):
    contract = load_contract(JOINT_CONTRACT)
    first = _org(annotation_factory, syntax="json-ld", locator="entity[0]",
                 identifier="https://example.invalid/#one",
                 values={"name": "Same Name", "url": "https://example.invalid/a"})
    second = _org(annotation_factory, syntax="microdata", locator="/html/body/div[1]",
                  identifier="https://example.invalid/#two",
                  values={"name": "Same Name", "url": "https://example.invalid/b"})
    entities, _ = merge_annotations([first, second], contract)
    assert len(entities) == 2
    verifications, _ = verify_entities(entities, contract)
    assert {item.verdict for item in verifications} == {ENTITY_PASS}


# ---------------------------------------------------------------------------
# End-to-end on isolated local HTML, including parser-failure eligibility
# ---------------------------------------------------------------------------

COMPLEMENTARY_HTML = """<!doctype html>
<html><head><base href="https://example.invalid/"></head><body>
<script type="application/ld+json">
{{"@context": "https://example.invalid/vocab", "@type": "Organization",
  "@id": "{org_id}", "name": "{name}"}}
</script>
<div itemscope itemtype="https://example.invalid/vocab/Organization"
     itemid="{org_id}">
  <a itemprop="url" href="{url}">Site</a>
</div>
</body></html>"""


def _write_site(tmp_path: Path, *, name="Acme Cooperative",
                url="https://example.invalid/acme") -> Path:
    site = tmp_path / "site"
    site.mkdir()
    (site / "org.html").write_text(
        COMPLEMENTARY_HTML.format(org_id=ORG_ID, name=name, url=url),
        encoding="utf-8",
    )
    return site


def test_end_to_end_joint_vs_independent_verdicts(tmp_path: Path):
    site = _write_site(tmp_path)
    outcomes = {}
    for label, contract_path in (("joint", JOINT_CONTRACT),
                                 ("independent", INDEPENDENT_CONTRACT)):
        config = RunConfig.create(
            input_dir=site, contract_path=contract_path,
            out_dir=tmp_path / f"out-{label}", now=FIXED_NOW,
        )
        result = build_result(config)
        entity = next(entity for entity in result.entities
                      if entity.identifier == ORG_ID)
        outcomes[label] = (entity, result)
        # Both per-source missing issues survive regardless of mode.
        per_source = [i for i in result.issues
                      if i.code == "CONTRACT_MISSING_REQUIRED"]
        assert len(per_source) == 2

    joint_entity, joint_result = outcomes["joint"]
    independent_entity, independent_result = outcomes["independent"]
    # Same merge grouping => same entity id across modes.
    assert joint_entity.entity_id == independent_entity.entity_id
    assert joint_entity.verification.verdict == ENTITY_PASS
    assert independent_entity.verification.verdict == ENTITY_FAIL_MISSING
    fields = {
        item.field: item
        for item in joint_entity.verification.type_results[0].field_evidence
    }
    assert fields["name"].status == FIELD_COMPLEMENTARY
    assert fields["url"].status == FIELD_COMPLEMENTARY
    # Evidence binds the exact annotation ids / locators / fragment hashes.
    source_ids = {source.annotation_id for source in joint_entity.sources}
    bound = {aid for item in fields.values() for aid in item.annotation_ids}
    assert bound <= source_ids and len(bound) == 2
    for item in fields.values():
        assert all(len(digest) == 64 for digest in item.fragment_sha256)
    # Business conclusion differs on identical input.
    joint_codes = {i.code for i in joint_result.issues}
    independent_codes = {i.code for i in independent_result.issues}
    assert "ENTITY_REQUIREMENT_MISSING" not in joint_codes
    assert "ENTITY_REQUIREMENT_MISSING" in independent_codes


def test_end_to_end_conflict_blocks_both_modes(tmp_path: Path):
    site = _write_site(tmp_path, name="Acme South")
    # JSON-LD says Acme North while microdata contributes only the url; add a
    # second microdata name via a fresh page to force a name scalar conflict.
    (site / "org2.html").write_text(
        COMPLEMENTARY_HTML.format(org_id=ORG_ID, name="Acme North",
                                  url="https://example.invalid/acme"),
        encoding="utf-8",
    )
    for contract_path in (JOINT_CONTRACT, INDEPENDENT_CONTRACT):
        config = RunConfig.create(
            input_dir=site, contract_path=contract_path,
            out_dir=tmp_path / f"out-conflict-{contract_path.stem}", now=FIXED_NOW,
        )
        result = build_result(config)
        entity = next(e for e in result.entities if e.identifier == ORG_ID)
        assert entity.verification.verdict == ENTITY_FAIL_CONFLICT
        assert any(i.code == "ENTITY_REQUIREMENT_CONFLICT" for i in result.issues)
        assert "name" in entity.conflicts


def test_parser_failure_keeps_entity_ineligible_not_complete(tmp_path: Path, monkeypatch):
    site = _write_site(tmp_path)
    config = RunConfig.create(
        input_dir=site, contract_path=JOINT_CONTRACT,
        out_dir=tmp_path / "out-parsefail", now=FIXED_NOW,
    )
    from microtag_harvest import pipeline

    def boom(_tree, _path, _sha):
        raise RuntimeError("synthetic extractor failure")

    # The pipeline derives the syntax label from the parser's module name.
    boom.__module__ = "microtag_harvest.parsers.microdata"

    # Fail the microdata extractor: only the JSON-LD (name-only) source survives.
    original = pipeline.PARSERS
    monkeypatch.setattr(pipeline, "PARSERS", (original[0], boom, *original[2:]))
    result = build_result(config)
    assert any(i.code == "PARSER_FAILURE" for i in result.issues)
    entity = next(e for e in result.entities if e.identifier == ORG_ID)
    verification = entity.verification
    assert verification.verdict == ENTITY_INELIGIBLE
    assert "extraction_incomplete" in verification.reasons
    assert verification.failed_extractions
    slot = verification.failed_extractions[0]
    assert slot["document"] == "org.html"
    assert slot["syntax"] == "microdata"
    ineligible_issue = next(
        i.to_dict() for i in result.issues if i.code == "ENTITY_VERIFICATION_INELIGIBLE"
    )
    assert ineligible_issue["details"]["failed_extractions"][0]["syntax"] == "microdata"


# ---------------------------------------------------------------------------
# Determinism and report <-> SQLite read-back consistency
# ---------------------------------------------------------------------------

def test_repeated_runs_have_stable_verdict_and_evidence(tmp_path: Path):
    site = _write_site(tmp_path)
    config = RunConfig.create(
        input_dir=site, contract_path=INDEPENDENT_CONTRACT,
        out_dir=tmp_path / "out-stable", now=FIXED_NOW,
    )
    first = build_result(config)
    second = build_result(config)
    first_payload = [e.to_dict()["verification"] for e in first.entities]
    second_payload = [e.to_dict()["verification"] for e in second.entities]
    assert first_payload == second_payload
    assert first.run["run_id"] == second.run["run_id"]
    issue_ids_first = [i.issue_id for i in first.issues]
    assert issue_ids_first == [i.issue_id for i in second.issues]


def test_report_and_sqlite_readback_share_evidence_identity(tmp_path: Path):
    site = _write_site(tmp_path)
    config = RunConfig.create(
        input_dir=site, contract_path=INDEPENDENT_CONTRACT,
        out_dir=tmp_path / "out-ledger", now=FIXED_NOW,
    )
    result, artifacts = execute(config)
    report = json.loads(artifacts["report.json"].read_text(encoding="utf-8"))
    report_entity = next(e for e in report["entities"] if e["identifier"] == ORG_ID)
    entity = next(e for e in result.entities if e.identifier == ORG_ID)

    connection = connect(config.db_path)
    try:
        stored = get_entity(connection, result.run["run_id"], entity.entity_id)
        assert stored is not None
        assert stored["verification"]["verdict"] == (
            report_entity["verification"]["verdict"]
        )
        assert stored["verification"]["mode"] == "independent"
        # Field evidence rows mirror the report, annotation id for annotation id.
        rows = list_field_evidence(connection, result.run["run_id"], entity.entity_id)
        report_evidence = [
            fe
            for tr in report_entity["verification"]["type_results"]
            for fe in tr["field_evidence"]
        ]
        assert len(rows) == len(report_evidence)
        for row, fe in zip(rows, report_evidence):
            assert row["field_name"] == fe["field"]
            assert row["status"] == fe["status"]
            assert row["annotation_ids"] == fe["annotation_ids"]
            assert row["locators"] == fe["locators"]
            assert row["fragment_sha256"] == fe["fragment_sha256"]
        # Every evidence annotation_id resolves to an entity_sources row and
        # points at the same locator/fragment pair as the report.
        report_sources = {s["annotation_id"]: s for s in report_entity["sources"]}
        for row in rows:
            for annotation_id in row["annotation_ids"]:
                assert annotation_id in report_sources
        linked = connection.execute(
            """SELECT a.annotation_id, a.locator, a.fragment_sha256
               FROM entity_field_evidence fe
               JOIN entity_sources es ON es.run_id=fe.run_id AND es.entity_id=fe.entity_id
               JOIN annotations a ON a.annotation_id=es.annotation_id
               WHERE fe.run_id=? AND fe.entity_id=?""",
            (result.run["run_id"], entity.entity_id),
        ).fetchall()
        for db_id, locator, digest in linked:
            source = report_sources[db_id]
            assert source["locator"] == locator
            assert source["fragment_sha256"] == digest
        # list_entities surfaces the verdict without loading field payloads.
        listed = list_entities(connection, result.run["run_id"])
        entry = next(item for item in listed if item["entity_id"] == entity.entity_id)
        assert entry["verification"]["verdict"] == ENTITY_FAIL_MISSING
    finally:
        connection.close()


def test_golden_summary_carries_field_verdict_statuses(tmp_path: Path):
    site = _write_site(tmp_path)
    config = RunConfig.create(
        input_dir=site, contract_path=JOINT_CONTRACT,
        out_dir=tmp_path / "out-golden", now=FIXED_NOW,
    )
    result, artifacts = execute(config)
    report = json.loads(artifacts["report.json"].read_text(encoding="utf-8"))
    summary = golden_summary(report)
    entity_summary = next(
        item for item in summary["entities"] if item["entity_id"] ==
        next(e.entity_id for e in result.entities if e.identifier == ORG_ID)
    )
    statuses = {item["field"]: item["status"]
                for item in entity_summary["verification"]["field_evidence"]}
    assert statuses == {"name": FIELD_COMPLEMENTARY, "url": FIELD_COMPLEMENTARY}
    assert entity_summary["verification"]["verdict"] == ENTITY_PASS


# ---------------------------------------------------------------------------
# Contract parsing guards
# ---------------------------------------------------------------------------

def test_invalid_verification_mode_is_contract_error(tmp_path: Path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        "name: bad\nversion: 1\n"
        "entity_verification:\n  mode: sometimes\n"
        "types:\n  Person:\n    required: [name]\n    fields:\n      name: {type: string}\n",
        encoding="utf-8",
    )
    with pytest.raises(ContractError) as caught:
        load_contract(path)
    assert "entity_verification.mode" in caught.value.message


def test_default_mode_is_joint_when_section_absent(tmp_path: Path):
    path = tmp_path / "plain.yaml"
    path.write_text(
        "name: plain\nversion: 1\ntypes:\n  Person:\n    required: [name]\n"
        "    fields:\n      name: {type: string}\n",
        encoding="utf-8",
    )
    assert load_contract(path).entity_verification.mode == "joint"
