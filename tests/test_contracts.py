from __future__ import annotations

from pathlib import Path

import pytest

from microtag_harvest.contracts import ContractError, load_contract
from microtag_harvest.validation import validate_annotations

CONTRACT_TEXT = """name: test-contract
version: 1
unknown_type: warn
unknown_field: error
types:
  Thing:
    required: [name, when]
    recommended: [url]
    fields:
      name: {type: string, format: nonempty}
      when: {type: date, format: iso8601}
      url: {type: url, format: uri}
      mode: {type: string, enum: [a, b]}
      tags: {type: array}
"""


def _contract(tmp_path: Path, text: str = CONTRACT_TEXT):
    path = tmp_path / "contract.yaml"
    path.write_text(text, encoding="utf-8")
    return load_contract(path)


def test_sample_contract_summary(contract_path: Path):
    contract = load_contract(contract_path)
    assert contract.name == "sample-site"
    assert contract.version == 1
    assert set(contract.types) >= {"Organization", "Product", "Metadata"}
    assert contract.types["Product"].required == ("name", "sku", "offers")
    assert contract.types["Metadata"].unknown_fields == "ignore"
    assert len(contract.sha256) == 64
    assert contract.summary()["types"]["Product"]["field_count"] == 8


def test_required_recommended_type_format_enum_and_unknown_field(tmp_path: Path, annotation_factory):
    contract = _contract(tmp_path)
    annotation = annotation_factory(
        types=("Thing",),
        values={
            "name": "Sample",
            "when": "March 3, 2025",
            "url": "ftp://example.invalid/file",
            "mode": "c",
            "tags": "not-a-list",
            "extra": "unexpected",
        },
    )
    issues = validate_annotations([annotation], contract)
    codes = [issue.code for issue in issues]
    assert "CONTRACT_TYPE_MISMATCH" in codes  # tags
    assert "CONTRACT_FORMAT_INVALID" in codes  # url
    assert "CONTRACT_ENUM_INVALID" in codes
    assert "CONTRACT_UNKNOWN_FIELD" in codes
    unknown = next(issue for issue in issues if issue.code == "CONTRACT_UNKNOWN_FIELD")
    assert unknown.severity == "error"
    assert unknown.details["policy"] == "error"
    assert all(len(issue.evidence_sha256) == 64 for issue in issues)


def test_missing_required_recommended_and_unknown_type(tmp_path: Path, annotation_factory):
    contract = _contract(tmp_path)
    missing = annotation_factory(types=("Thing",), values={"name": "Sample"})
    unknown = annotation_factory(types=("Mystery",), values={"name": "Unknown"})
    issues = validate_annotations([missing, unknown], contract)
    codes = [issue.code for issue in issues]
    assert codes.count("CONTRACT_MISSING_REQUIRED") == 1
    assert codes.count("CONTRACT_RECOMMENDED_MISSING") == 1
    assert codes.count("CONTRACT_UNKNOWN_TYPE") == 1
    recommended = next(i for i in issues if i.code == "CONTRACT_RECOMMENDED_MISSING")
    assert recommended.severity == "warn"
    unknown_type = next(i for i in issues if i.code == "CONTRACT_UNKNOWN_TYPE")
    assert unknown_type.severity == "warn"


def test_per_type_unknown_fields_ignore(tmp_path: Path, annotation_factory):
    text = CONTRACT_TEXT.replace(
        "  Thing:\n", "  Thing:\n    unknown_fields: ignore\n"
    )
    contract = _contract(tmp_path, text)
    annotation = annotation_factory(types=("Thing",), values={
        "name": "Sample", "when": "2025-01-01", "url": "https://example.invalid/",
        "extra": "ignored",
    })
    issues = validate_annotations([annotation], contract)
    assert "CONTRACT_UNKNOWN_FIELD" not in [issue.code for issue in issues]


def test_contract_yaml_syntax_error_reports_line(tmp_path: Path):
    path = tmp_path / "broken.yaml"
    path.write_text("name: broken\nversion: [1\n", encoding="utf-8")
    with pytest.raises(ContractError) as caught:
        load_contract(path)
    assert caught.value.line == 2
    assert "broken.yaml" in str(caught.value)


def test_contract_semantic_error_reports_line(tmp_path: Path):
    path = tmp_path / "semantic.yaml"
    path.write_text(
        "name: bad\nversion: 1\nunknown_type: sometimes\ntypes:\n  Thing:\n    fields: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(ContractError) as caught:
        load_contract(path)
    assert caught.value.line == 3
    assert "unknown_type" in caught.value.message
