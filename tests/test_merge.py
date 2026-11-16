from __future__ import annotations

from microtag_harvest.contracts import load_contract
from microtag_harvest.merge import merge_annotations


def _people(annotation_factory, **kwargs):
    kwargs.setdefault("types", ("Person",))
    return annotation_factory(**kwargs)


def test_same_document_name_merges_but_cross_document_name_does_not(contract_path, annotation_factory):
    contract = load_contract(contract_path)
    first = _people(annotation_factory, doc_path="a.html", syntax="json-ld",
                    locator="json[0]", values={"name": "Avery Example"})
    second = _people(annotation_factory, doc_path="a.html", syntax="rdfa",
                     locator="rdfa[0]", values={"name": " Avery   Example "})
    third = _people(annotation_factory, doc_path="b.html", syntax="json-ld",
                    locator="json[0]", values={"name": "Avery Example"})
    entities, _issues = merge_annotations([first, second, third], contract)
    assert len(entities) == 2
    merged = next(entity for entity in entities if len(entity.sources) == 2)
    assert len(merged.sources) == 2
    assert {source.syntax for source in merged.sources} == {"json-ld", "rdfa"}
    assert all(len(entity.entity_id) == 12 for entity in entities)


def test_cross_document_merge_requires_normalized_id(contract_path, annotation_factory):
    contract = load_contract(contract_path)
    first = _people(annotation_factory, doc_path="a.html", syntax="json-ld",
                    locator="json[0]", identifier="#avery",
                    base_url="https://example.invalid/people/",
                    values={"name": "Avery Example"})
    second = _people(annotation_factory, doc_path="b.html", syntax="microdata",
                     locator="/html/body/div[1]",
                     identifier="https://example.invalid/people/#avery",
                     values={"name": "Avery Example"})
    entities, issues = merge_annotations([first, second], contract)
    assert len(entities) == 1
    assert len(entities[0].sources) == 2
    assert entities[0].identifier == "https://example.invalid/people/#avery"
    assert not [issue for issue in issues if issue.code == "DANGLING_REFERENCE"]


def test_conflict_keeps_all_sources(contract_path, annotation_factory):
    contract = load_contract(contract_path)
    first = _people(annotation_factory, doc_path="a.html", syntax="json-ld",
                    locator="json[0]", identifier="https://example.invalid/#p",
                    values={"name": "Avery Example", "jobTitle": "Data Steward"})
    second = _people(annotation_factory, doc_path="a.html", syntax="rdfa",
                     locator="rdfa[0]", identifier="https://example.invalid/#p",
                     values={"name": "Avery Example", "jobTitle": "Lead Steward"})
    entities, issues = merge_annotations([first, second], contract)
    assert len(entities) == 1
    entity = entities[0]
    assert entity.conflicts == ("jobTitle",)
    assert [value.value for value in entity.fields["jobTitle"]] == [
        "Data Steward", "Lead Steward"
    ]
    codes = {issue.code for issue in issues}
    assert "FIELD_VALUE_CONFLICT" in codes
    assert "MULTI_SYNTAX_CONFLICT" in codes
    conflict = next(issue for issue in issues if issue.code == "FIELD_VALUE_CONFLICT")
    assert len(conflict.details["values"]) == 2


def test_array_fields_do_not_conflict(contract_path, annotation_factory):
    contract = load_contract(contract_path)
    first = _people(annotation_factory, doc_path="a.html", syntax="json-ld",
                    locator="json[0]", identifier="https://example.invalid/#p",
                    values={"name": "Avery", "sameAs": ["https://example.invalid/a"]})
    second = _people(annotation_factory, doc_path="a.html", syntax="rdfa",
                     locator="rdfa[0]", identifier="https://example.invalid/#p",
                     values={"name": "Avery", "sameAs": ["https://example.invalid/b"]})
    entities, issues = merge_annotations([first, second], contract)
    assert entities[0].conflicts == ()
    assert "FIELD_VALUE_CONFLICT" not in {issue.code for issue in issues}


def test_duplicate_identifier_same_document_and_syntax(contract_path, annotation_factory):
    contract = load_contract(contract_path)
    one = _people(annotation_factory, locator="json[0]",
                  identifier="https://example.invalid/#a", values={"name": "Avery"})
    two = _people(annotation_factory, locator="json[1]",
                  identifier="https://example.invalid/#a", values={"name": "Avery"})
    entities, issues = merge_annotations([one, two], contract)
    assert len(entities) == 1
    assert [issue.code for issue in issues].count("ENTITY_DUPLICATE_ID") == 1


def test_dangling_reference_and_cycle(contract_path, annotation_factory):
    contract = load_contract(contract_path)
    a = _people(annotation_factory, locator="a", identifier="https://example.invalid/#a",
                values={"name": "A"}, references=("https://example.invalid/#b",))
    b = _people(annotation_factory, locator="b", identifier="https://example.invalid/#b",
                values={"name": "B"}, references=("https://example.invalid/#a",))
    c = _people(annotation_factory, locator="c", identifier="https://example.invalid/#c",
                values={"name": "C"}, references=("https://example.invalid/#missing",))
    entities, issues = merge_annotations([a, b, c], contract)
    assert len(entities) == 3
    codes = [issue.code for issue in issues]
    assert codes.count("REFERENCE_CYCLE") == 1
    assert codes.count("DANGLING_REFERENCE") == 1
    dangling = next(issue for issue in issues if issue.code == "DANGLING_REFERENCE")
    assert dangling.details["reference"] == "https://example.invalid/#missing"


def test_entity_ids_are_deterministic(contract_path, annotation_factory):
    contract = load_contract(contract_path)
    annotation = _people(annotation_factory, identifier="https://example.invalid/#stable",
                         values={"name": "Stable"})
    first, _ = merge_annotations([annotation], contract)
    second, _ = merge_annotations([annotation], contract)
    assert first[0].entity_id == second[0].entity_id
    assert first[0].to_dict() == second[0].to_dict()
