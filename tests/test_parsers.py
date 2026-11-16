from __future__ import annotations

import json

from conftest import parse_html
from microtag_harvest.parsers import jsonld, meta, microdata, rdfa
from microtag_harvest.utils import sha256_hex


def _jsonld(markup: str, doc: str = "sample.html"):
    tree = parse_html(markup)
    return jsonld.parse(tree, doc, sha256_hex(markup))


def test_jsonld_array_graph_and_nested_values():
    payload = [
        {
            "@context": "https://example.invalid/vocab",
            "@type": "Product",
            "@id": "https://example.invalid/p#p",
            "name": "Widget",
            "offers": {"@type": "Offer", "@id": "https://example.invalid/p#o", "price": "1"},
        },
        {
            "@context": "https://example.invalid/vocab",
            "@graph": [
                {"@type": "Organization", "name": "Sample Org"},
                {"@type": ["Person", "Thing"], "name": "Avery Example"},
            ],
        },
    ]
    markup = f'<html><head><base href="https://example.invalid/base/"></head><body><script type="application/ld+json">{json.dumps(payload)}</script></body></html>'
    result = _jsonld(markup)
    assert len(result.annotations) == 3
    assert result.annotations[0].types == ("Product",)
    assert result.annotations[0].identifier == "https://example.invalid/p#p"
    assert result.annotations[0].fields["offers"][0].value["price"] == "1"
    assert result.annotations[2].types == ("Person", "Thing")
    assert result.annotations[1].locator.endswith("/graph[0]")
    assert all(len(ann.fragment_sha256) == 64 for ann in result.annotations)
    assert result.base_url == "https://example.invalid/base/"


def test_jsonld_invalid_and_remote_context_are_recorded_without_raising():
    markup = """<html><head><base href="https://example.invalid/"></head><body>
    <script type="application/ld+json">{"@type": "Person",</script>
    <script type="application/ld+json">{"@context": "https://example.invalid/context.jsonld",
      "@type": "Person", "name": "Avery"}</script>
    </body></html>"""
    result = _jsonld(markup)
    codes = [issue.code for issue in result.issues]
    assert "PARSE_JSONLD_INVALID" in codes
    assert "REMOTE_CONTEXT_NOT_LOADED" in codes
    assert len(result.annotations) == 1


def test_jsonld_typeless_object_warns():
    markup = """<html><body><script type="application/ld+json">
    {"@context": "https://example.invalid/vocab", "name": "No type"}
    </script></body></html>"""
    result = _jsonld(markup)
    assert not result.annotations
    codes = [issue.code for issue in result.issues]
    assert "JSONLD_ENTITY_WITHOUT_TYPE" in codes
    assert "REMOTE_CONTEXT_NOT_LOADED" in codes


def test_microdata_nested_items_and_itemid():
    markup = """<html><body>
    <div itemscope itemtype="https://example.invalid/vocab/Event"
         itemid="https://example.invalid/e#event">
      <span itemprop="name">Meetup</span>
      <div itemprop="location" itemscope>
        <span itemprop="name">Sample Hall</span>
        <span itemprop="address">1 Example Way</span>
      </div>
    </div></body></html>"""
    result = microdata.parse(parse_html(markup), "events.html", sha256_hex(markup))
    assert len(result.annotations) == 1
    annotation = result.annotations[0]
    assert annotation.types == ("Event",)
    assert annotation.identifier.endswith("#event")
    location = annotation.fields["location"][0].value
    assert location["name"] == "Sample Hall"
    assert location["address"] == "1 Example Way"


def test_microdata_untyped_top_level_is_warned():
    markup = '<html><body><div itemscope><span itemprop="name">No type</span></div></body></html>'
    result = microdata.parse(parse_html(markup), "plain.html", sha256_hex(markup))
    assert not result.annotations
    assert result.issues[0].code == "MICRODATA_ITEM_WITHOUT_TYPE"


def test_rdfa_basic_properties():
    markup = """<html><body>
    <div vocab="https://example.invalid/vocab/" typeof="Person"
         about="https://example.invalid/#avery">
      <span property="name">Avery Example</span>
      <a property="url" href="/people/avery">Profile</a>
      <time property="birthDate" datetime="1990-01-02">January 2</time>
    </div></body></html>"""
    result = rdfa.parse(parse_html(markup), "people.html", sha256_hex(markup))
    annotation = result.annotations[0]
    assert annotation.types == ("Person",)
    assert annotation.identifier == "https://example.invalid/#avery"
    assert annotation.fields["name"][0].value == "Avery Example"
    assert annotation.fields["url"][0].value == "/people/avery"
    assert annotation.fields["birthDate"][0].attr == "rdfa:datetime"


def test_meta_opengraph_and_twitter_card_are_unified():
    markup = """<html><head>
    <meta property="og:title" content="Sample">
    <meta property="og:image" content="/image.png">
    <meta name="twitter:card" content="summary">
    <meta name="description" content="ignored">
    </head><body></body></html>"""
    result = meta.parse(parse_html(markup), "meta.html", sha256_hex(markup))
    annotation = result.annotations[0]
    assert annotation.types == ("Metadata",)
    assert sorted(annotation.fields) == ["og:image", "og:title", "twitter:card"]
    assert annotation.fields["og:title"][0].value == "Sample"
    assert annotation.fragment_sha256 == sha256_hex(annotation.fragment)
