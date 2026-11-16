"""Page-embedded semantic annotation parsers."""

from . import jsonld, meta, microdata, rdfa

PARSERS = (jsonld.parse, microdata.parse, rdfa.parse, meta.parse)

__all__ = ["jsonld", "meta", "microdata", "rdfa", "PARSERS"]
