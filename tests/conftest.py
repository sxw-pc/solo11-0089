from __future__ import annotations

import sys
from pathlib import Path

import pytest
from lxml import html

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from microtag_harvest.config import RunConfig  # noqa: E402
from microtag_harvest.parsers.common import FieldValue, find_annotation  # noqa: E402
from microtag_harvest.pipeline import execute  # noqa: E402
from microtag_harvest.utils import sha256_hex  # noqa: E402

SITE = ROOT / "fixtures" / "site"
CONTRACT = ROOT / "fixtures" / "contracts" / "site.yaml"
EXPECTED = ROOT / "fixtures" / "expected" / "report_summary.json"
FIXED_NOW = "2026-01-01T00:00:00Z"


def parse_html(markup: str):
    return html.fromstring(markup)


def make_annotation(
    *,
    doc_path: str = "page.html",
    syntax: str = "json-ld",
    locator: str = "entity[0]",
    types: tuple[str, ...] = ("Person",),
    identifier: str = "",
    values: dict[str, object] | None = None,
    references: tuple[str, ...] = (),
    base_url: str = "https://example.invalid/",
    raw: str = "",
):
    fields = {
        name: [FieldValue(value, f"{locator}#{name}")] for name, value in (values or {}).items()
    }
    return find_annotation(
        doc_path=doc_path,
        doc_sha256=sha256_hex(doc_path + locator),
        syntax=syntax,
        locator=locator,
        raw_fragment=raw or f"{doc_path}|{syntax}|{locator}|{identifier}|{values}",
        types=types,
        identifier=identifier,
        fields=fields,
        references=references,
        base_url=base_url,
    )


@pytest.fixture
def site_dir() -> Path:
    return SITE


@pytest.fixture
def contract_path() -> Path:
    return CONTRACT


@pytest.fixture
def expected_path() -> Path:
    return EXPECTED


@pytest.fixture
def annotation_factory():
    return make_annotation


@pytest.fixture
def executed(tmp_path: Path):
    config = RunConfig.create(
        input_dir=SITE, contract_path=CONTRACT, out_dir=tmp_path / "out", now=FIXED_NOW
    )
    result, artifacts = execute(config)
    return config, result, artifacts
