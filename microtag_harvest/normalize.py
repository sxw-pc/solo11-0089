"""Deterministic normalization for text, URLs, dates, and identifiers."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from .utils import collapse_whitespace, normalize_type_name

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}
_MONTH_DAY_YEAR = re.compile(r"^([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})$")
_DAY_MONTH_YEAR = re.compile(r"^(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})$")
_URL_FIELDS = {
    "@id", "id", "itemid", "url", "sameas", "logo", "image", "contenturl",
    "thumbnailurl", "target", "mainentityofpage", "author", "publisher",
}
_DATE_FIELDS = {
    "startdate", "enddate", "datepublished", "datemodified", "datecreated",
    "uploaddate", "birthdate",
}


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return collapse_whitespace(str(value))


def normalize_url(value: Any, base_url: str = "") -> str:
    """Resolve a URL and remove its fragment. Invalid values remain visible."""
    text = normalize_text(value)
    if not text:
        return ""
    try:
        joined = urljoin(base_url, text) if base_url else text
        parts = urlsplit(joined)
        if not parts.scheme:
            return text
        scheme = parts.scheme.lower()
        hostname = (parts.hostname or "").lower()
        port = parts.port
        if port and not ((scheme == "http" and port == 80)
                         or (scheme == "https" and port == 443)):
            netloc = f"{hostname}:{port}"
        else:
            netloc = hostname
        if parts.username:
            userinfo = parts.username
            if parts.password:
                userinfo += ":" + parts.password
            netloc = f"{userinfo}@{netloc}"
        return urlunsplit((scheme, netloc, parts.path or "", parts.query, ""))
    except (ValueError, TypeError):
        return text.split("#", 1)[0]


def normalize_identifier(value: Any, base_url: str = "") -> str:
    """Normalize an @id while preserving its fragment (the fragment is semantic)."""
    text = normalize_text(value)
    if not text:
        return ""
    if text.startswith(("#", "/", "./", "../")):
        try:
            return urljoin(base_url, text)
        except (ValueError, TypeError):
            return text
    try:
        parts = urlsplit(text)
        if parts.scheme and parts.hostname:
            return text
    except ValueError:
        pass
    return text


def normalize_date(value: Any) -> str:
    """Convert common synthetic-fixture date forms to ISO-8601."""
    text = normalize_text(value)
    if not text:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            return text
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc)
            return parsed.isoformat().replace("+00:00", "Z")
        return parsed.isoformat()
    except ValueError:
        pass
    for pattern in (_MONTH_DAY_YEAR, _DAY_MONTH_YEAR):
        match = pattern.match(text)
        if not match:
            continue
        groups = match.groups()
        if pattern is _MONTH_DAY_YEAR:
            month_name, day, year = groups
        else:
            day, month_name, year = groups
        month = _MONTHS.get(month_name.lower())
        if month:
            try:
                return date(int(year), month, int(day)).isoformat()
            except ValueError:
                return text
    return text


def is_iso_date(value: str) -> bool:
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        datetime.fromisoformat(candidate)
        return True
    except ValueError:
        try:
            date.fromisoformat(candidate)
            return True
        except ValueError:
            return False


def _is_url_field(name: str, field_format: str | None) -> bool:
    return field_format in {"uri", "url"} or name.lower() in _URL_FIELDS or name.lower().endswith("url")


def _is_date_field(name: str, field_format: str | None) -> bool:
    return field_format in {"iso8601", "date"} or name.lower() in _DATE_FIELDS


def normalize_value(
    value: Any,
    field_name: str = "",
    base_url: str = "",
    field_format: str | None = None,
) -> Any:
    if isinstance(value, list):
        return [normalize_value(item, field_name, base_url, field_format) for item in value]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        explicit_type = value.get("@type")
        for key in sorted(value):
            if key == "@type":
                types = explicit_type if isinstance(explicit_type, list) else [explicit_type]
                result[key] = [normalize_type_name(item) for item in types if item]
            elif key == "@id":
                result[key] = normalize_identifier(value[key], base_url)
            else:
                result[key] = normalize_value(value[key], key, base_url)
        return result
    if not isinstance(value, (str, int, float, bool)) and value is not None:
        return value
    if field_name == "@id" or field_name.lower() == "itemid":
        return normalize_identifier(value, base_url)
    if _is_url_field(field_name, field_format):
        return normalize_url(value, base_url)
    if _is_date_field(field_name, field_format):
        return normalize_date(value)
    return normalize_text(value) if isinstance(value, str) else value


def normalized_name(types: tuple[str, ...], fields: dict[str, list[Any]]) -> str:
    for key in ("name", "headline", "og:title"):
        values = fields.get(key, [])
        for value in values:
            if isinstance(value, str) and value.strip():
                return normalize_text(value)
    return ""
