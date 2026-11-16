"""YAML contract loading, schema parsing, and contract summaries."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .utils import sha256_hex

FIELD_TYPES = {
    "string", "url", "date", "number", "integer", "boolean",
    "array", "object", "identifier",
}
FORMATS = {None, "uri", "url", "iso8601", "email", "nonempty"}
POLICIES = {"error", "warn", "ignore"}
ENTITY_VERIFICATION_MODES = {"joint", "independent"}
DEFAULT_ENTITY_VERIFICATION_MODE = "joint"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ContractError(Exception):
    def __init__(self, message: str, path: str, line: int = 1):
        super().__init__(message)
        self.message = message
        self.path = path
        self.line = line

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: str
    format: str | None = None
    enum: tuple[Any, ...] = ()
    required: bool = False


@dataclass(frozen=True)
class TypeSpec:
    name: str
    required: tuple[str, ...] = ()
    recommended: tuple[str, ...] = ()
    fields: dict[str, FieldSpec] = field(default_factory=dict)
    unknown_fields: str | None = None


@dataclass(frozen=True)
class EntityVerificationSpec:
    """Entity-level contract verification policy.

    joint: complementary sources (across syntaxes or documents) may combine
      their fields so that the merged entity satisfies required fields.
    independent: every eligible source must itself carry every required field;
      the entity can never pass on another source's complementary values.
    Conflicting scalar values block a pass under both modes.
    """

    mode: str = DEFAULT_ENTITY_VERIFICATION_MODE

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode}


@dataclass(frozen=True)
class Contract:
    name: str
    version: int
    unknown_type: str
    unknown_field: str
    types: dict[str, TypeSpec]
    source_path: str
    source_text: str
    sha256: str
    entity_verification: EntityVerificationSpec = field(
        default_factory=lambda: EntityVerificationSpec()
    )

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "sha256": self.sha256,
            "unknown_type": self.unknown_type,
            "unknown_field": self.unknown_field,
            "entity_verification": self.entity_verification.to_dict(),
            "types": {
                name: {
                    "required": list(spec.required),
                    "recommended": list(spec.recommended),
                    "field_count": len(spec.fields),
                    "unknown_fields": spec.unknown_fields or self.unknown_field,
                }
                for name, spec in sorted(self.types.items())
            },
        }


def _line_for(source: str, key: str) -> int:
    pattern = re.compile(r"^\s*" + re.escape(str(key)) + r"\s*:", re.MULTILINE)
    match = pattern.search(source)
    return source[:match.start()].count("\n") + 1 if match else 1


def _require(condition: bool, message: str, path: str, line: int) -> None:
    if not condition:
        raise ContractError(message, path, line)


def _string_list(value: Any, path: str, source: str, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    _require(isinstance(value, list) and all(isinstance(i, str) for i in value),
             f"{key} must be a list of field names", path, _line_for(source, key))
    return tuple(dict.fromkeys(value))


def _parse_field(name: str, raw: Any, path: str, source: str) -> FieldSpec:
    line = _line_for(source, name)
    if raw is None:
        return FieldSpec(name=name, type="string")
    _require(isinstance(raw, dict), f"field {name!r} must be a mapping", path, line)
    field_type = raw.get("type", "string")
    _require(field_type in FIELD_TYPES,
             f"field {name!r} has unsupported type {field_type!r}", path, line)
    field_format = raw.get("format")
    _require(field_format in FORMATS,
             f"field {name!r} has unsupported format {field_format!r}", path, line)
    enum = raw.get("enum", [])
    _require(isinstance(enum, list), f"field {name!r} enum must be a list", path, line)
    required = raw.get("required", False)
    _require(isinstance(required, bool), f"field {name!r} required must be boolean", path, line)
    return FieldSpec(name, field_type, field_format, tuple(enum), required)


def _parse_type(name: str, raw: Any, path: str, source: str) -> TypeSpec:
    line = _line_for(source, name)
    _require(isinstance(raw, dict), f"type {name!r} must be a mapping", path, line)
    required = list(_string_list(raw.get("required"), path, source, "required"))
    recommended = list(_string_list(raw.get("recommended"), path, source, "recommended"))
    raw_fields = raw.get("fields", {})
    _require(isinstance(raw_fields, dict), f"type {name!r} fields must be a mapping", path, line)
    fields = {
        str(field_name): _parse_field(str(field_name), spec, path, source)
        for field_name, spec in sorted(raw_fields.items())
    }
    for field_name, spec in fields.items():
        if spec.required and field_name not in required:
            required.append(field_name)
    unknown = raw.get("unknown_fields")
    _require(unknown is None or unknown in POLICIES,
             f"type {name!r} unknown_fields must be one of {sorted(POLICIES)}", path, line)
    return TypeSpec(
        name=name,
        required=tuple(required),
        recommended=tuple(recommended),
        fields=fields,
        unknown_fields=unknown,
    )


def _parse_entity_verification(raw: Any, path: str, source: str) -> EntityVerificationSpec:
    if raw is None:
        return EntityVerificationSpec()
    line = _line_for(source, "entity_verification")
    _require(isinstance(raw, dict),
             "entity_verification must be a mapping", path, line)
    mode = raw.get("mode", DEFAULT_ENTITY_VERIFICATION_MODE)
    _require(isinstance(mode, str) and mode in ENTITY_VERIFICATION_MODES,
             f"entity_verification.mode must be one of {sorted(ENTITY_VERIFICATION_MODES)}",
             path, line)
    return EntityVerificationSpec(mode=mode)


def load_contract(path: str | Path) -> Contract:
    contract_path = Path(path)
    source = contract_path.read_text(encoding="utf-8")
    display_path = str(path).replace("\\", "/")
    try:
        raw = yaml.safe_load(source)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "context_mark", None) or getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark else 1
        problem = getattr(exc, "problem", None) or "invalid YAML"
        raise ContractError(problem, display_path, line) from exc
    _require(isinstance(raw, dict), "contract root must be a mapping", display_path, 1)
    name = raw.get("name", contract_path.stem)
    _require(isinstance(name, str) and name.strip(),
             "contract name must be a non-empty string", display_path, _line_for(source, "name"))
    version = raw.get("version", 1)
    _require(isinstance(version, int) and not isinstance(version, bool),
             "version must be an integer", display_path, _line_for(source, "version"))
    unknown_type = raw.get("unknown_type", "error")
    unknown_field = raw.get("unknown_field", "warn")
    _require(unknown_type in POLICIES,
             f"unknown_type must be one of {sorted(POLICIES)}", display_path,
             _line_for(source, "unknown_type"))
    _require(unknown_field in POLICIES,
             f"unknown_field must be one of {sorted(POLICIES)}", display_path,
             _line_for(source, "unknown_field"))
    raw_types = raw.get("types")
    _require(isinstance(raw_types, dict) and bool(raw_types),
             "types must be a non-empty mapping", display_path, _line_for(source, "types"))
    types = {
        str(type_name): _parse_type(str(type_name), value, display_path, source)
        for type_name, value in sorted(raw_types.items())
    }
    entity_verification = _parse_entity_verification(
        raw.get("entity_verification"), display_path, source
    )
    return Contract(
        name=name.strip(),
        version=version,
        unknown_type=unknown_type,
        unknown_field=unknown_field,
        types=types,
        source_path=display_path,
        source_text=source,
        sha256=sha256_hex(source.encode("utf-8")),
        entity_verification=entity_verification,
    )
