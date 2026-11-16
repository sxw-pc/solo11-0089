from __future__ import annotations

from microtag_harvest.normalize import (
    is_iso_date, normalize_date, normalize_identifier, normalize_text,
    normalize_url, normalize_value,
)


def test_text_and_url_normalization_remove_fragments():
    assert normalize_text("  a\n\t b  ") == "a b"
    assert normalize_url("/catalog/item.html#top", "https://example.invalid/base/") == (
        "https://example.invalid/catalog/item.html"
    )
    assert normalize_url("HTTPS://EXAMPLE.INVALID:443/a?x=1#frag") == (
        "https://example.invalid/a?x=1"
    )
    assert normalize_url("https://LOCALHOST:5017/path#x") == (
        "https://localhost:5017/path"
    )
    assert normalize_url("not a url") == "not a url"


def test_identifier_preserves_semantic_fragment():
    assert normalize_identifier("#person", "https://example.invalid/people/avery") == (
        "https://example.invalid/people/avery#person"
    )
    assert normalize_identifier("https://example.invalid/#org") == (
        "https://example.invalid/#org"
    )


def test_dates_become_iso_8601_deterministically():
    assert normalize_date("March 3, 2025") == "2025-03-03"
    assert normalize_date("3 March 2025") == "2025-03-03"
    assert normalize_date("2025-03-04") == "2025-03-04"
    assert normalize_date("2025-03-04T10:30:00Z") == "2025-03-04T10:30:00Z"
    assert normalize_date("2025-03-04T10:30:00+02:00") == "2025-03-04T08:30:00Z"
    assert normalize_date("not a date") == "not a date"
    assert is_iso_date("2025-03-04")
    assert not is_iso_date("March 3, 2025")


def test_nested_values_and_field_formats():
    value = {
        "@id": "https://example.invalid/#x",
        "@type": "https://example.invalid/vocab/Person",
        "name": "  Avery\n Example ",
        "url": "/avery#fragment",
        "children": [{"url": "/child#x"}],
    }
    normalized = normalize_value(value, "subject", "https://example.invalid/")
    assert normalized["@type"] == ["Person"]
    assert normalized["@id"] == "https://example.invalid/#x"
    assert normalized["name"] == "Avery Example"
    assert normalized["url"] == "https://example.invalid/avery"
    assert normalized["children"][0]["url"] == "https://example.invalid/child"
    assert normalize_value("  text  ", "description") == "text"
