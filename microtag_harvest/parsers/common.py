"""Shared candidate-record structures and extraction helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from lxml import etree

from ..issues import RawIssue
from ..utils import cap_text, canonical_json, collapse_whitespace, sha1_short, sha256_hex


@dataclass
class FieldValue:
    value: Any
    locator: str
    attr: str = ""


@dataclass
class Annotation:
    doc_path: str
    doc_sha256: str
    syntax: str
    locator: str
    fragment: str
    fragment_sha256: str
    types: tuple[str, ...]
    identifier: str
    fields: dict[str, list[FieldValue]]
    references: tuple[str, ...]
    base_url: str

    @property
    def annotation_id(self) -> str:
        seed = "|".join(
            [self.doc_sha256, self.syntax, self.locator, self.fragment_sha256]
        )
        return sha1_short(seed, 16)

    def field_values(self, name: str) -> list[Any]:
        return [item.value for item in self.fields.get(name, [])]

    def to_dict(self) -> dict[str, Any]:
        return {
            "annotation_id": self.annotation_id,
            "document": self.doc_path,
            "document_sha256": self.doc_sha256,
            "syntax": self.syntax,
            "locator": self.locator,
            "fragment": self.fragment,
            "fragment_sha256": self.fragment_sha256,
            "types": list(self.types),
            "identifier": self.identifier,
            "base_url": self.base_url,
            "references": list(self.references),
            "fields": {
                name: [
                    {"value": item.value, "locator": item.locator, "attr": item.attr}
                    for item in values
                ]
                for name, values in sorted(self.fields.items())
            },
        }


@dataclass
class ParseResult:
    annotations: list[Annotation] = field(default_factory=list)
    issues: list[RawIssue] = field(default_factory=list)
    base_url: str = ""


def base_url_for(tree: etree._Element, doc_path: str) -> str:
    """Use <base href> when present; local archives otherwise get a reserved origin."""
    nodes = tree.xpath("//base[@href]")
    if nodes:
        raw = collapse_whitespace(nodes[0].get("href") or "")
        if raw:
            return raw
    return f"https://example.invalid/{doc_path.replace(chr(92), '/')}"


def find_annotation(
    *,
    doc_path: str,
    doc_sha256: str,
    syntax: str,
    locator: str,
    raw_fragment: str,
    types: Iterable[Any],
    identifier: str = "",
    fields: dict[str, list[FieldValue]] | None = None,
    references: Iterable[str] = (),
    base_url: str = "",
) -> Annotation:
    from ..utils import normalize_type_name

    clean_types: list[str] = []
    for item in types:
        name = normalize_type_name(item)
        if name and name not in clean_types:
            clean_types.append(name)
    return Annotation(
        doc_path=doc_path,
        doc_sha256=doc_sha256,
        syntax=syntax,
        locator=locator,
        fragment=cap_text(raw_fragment),
        fragment_sha256=sha256_hex(raw_fragment),
        types=tuple(clean_types),
        identifier=collapse_whitespace(identifier),
        fields=fields or {},
        references=tuple(item for item in references if item),
        base_url=base_url,
    )


def serialize_element(element: etree._Element) -> str:
    return etree.tostring(element, encoding="unicode", with_tail=False)


def collect_object_references(value: Any) -> list[str]:
    """Collect JSON-LD-style @id links, not arbitrary URL-valued fields."""
    refs: list[str] = []
    if isinstance(value, dict):
        identifier = value.get("@id")
        if identifier and (len(value) == 1 or set(value).issubset({"@id", "@type"})):
            refs.append(str(identifier))
        for key, nested in value.items():
            if key != "@id":
                refs.extend(collect_object_references(nested))
    elif isinstance(value, list):
        for item in value:
            refs.extend(collect_object_references(item))
    return refs


def attribute_value(element: etree._Element) -> tuple[Any, str]:
    """Choose the property value using Microdata/RDFa precedence, deterministically."""
    for name in ("content", "resource", "href", "src", "datetime"):
        if name in element.attrib:
            return element.get(name) or "", name
    text = collapse_whitespace(" ".join(element.itertext()))
    return text, "text"


def dedupe_strings(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)


def evidence_key(annotation: Annotation, field_name: str, locator: str = "") -> str:
    return canonical_json([annotation.annotation_id, field_name, locator])


def field_locator(annotation: Annotation, field_name: str) -> str:
    for item in annotation.fields.get(field_name, []):
        if item.locator:
            return item.locator
    return annotation.locator
