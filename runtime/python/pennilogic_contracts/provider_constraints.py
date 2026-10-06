"""Declared constraints for marked providers, compiled from the owning specification."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any, Mapping
from urllib.parse import urlsplit

from pennilogic_contracts.models.instant import Instant
from pennilogic_contracts.models.local_date import LocalDate
from pennilogic_contracts.provider_constraint_data import SCHEMAS
from pennilogic_contracts.uuid_wire import uuid_from_wire


class ProviderConstraintError(ValueError):
    def __init__(self) -> None:
        super().__init__("provider value rejected")

@lru_cache(maxsize=256)
def _pattern(source: str) -> re.Pattern[str]:
    parts: list[str] = []
    in_class = False
    escaped = False
    for character in source:
        if escaped:
            if character == "s" and in_class:
                parts[-1] = r"\u0009-\u000d\u0020\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
            else:
                parts.append(character)
            escaped = False
        elif character == "\\":
            parts.append(character)
            escaped = True
        elif character == "[":
            in_class = True
            parts.append(character)
        elif character == "]":
            in_class = False
            parts.append(character)
        elif character == "." and not in_class:
            # ECMAScript dot excludes these four terminators, but includes NEL.
            parts.append(r"[^\n\r\u2028\u2029]")
        else:
            parts.append(character)
    try:
        return re.compile("".join(parts), re.ASCII)
    except re.PatternError:
        raise ProviderConstraintError() from None


def pattern_matches(source: str, value: str) -> bool:
    return _pattern(source).fullmatch(value) is not None


def validate_provider(name: str, value: Mapping[str, Any]) -> None:
    if name not in SCHEMAS:
        raise ValueError("provider constraint binding missing")
    if not _matches(value, SCHEMAS[name]):
        raise ProviderConstraintError()


def _equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_equal(item, right[key]) for key, item in left.items())
    if isinstance(left, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return bool(left == right)


def _format(value: str, name: str) -> bool:
    try:
        if name == "date-time":
            Instant.from_wire(value)
        elif name == "date":
            LocalDate.from_wire(value)
        elif name == "uri-reference":
            if not value.isascii() or re.search(r"[\x00-\x20\x7f]|%(?![0-9a-fA-F]{2})", value):
                return False
            urlsplit(value)
        elif name == "uuid":
            uuid_from_wire(value)
        else:
            raise ValueError("provider constraint binding unsupported")
    except (ValueError, TypeError):
        return False
    return True


def _matches(value: Any, schema: Mapping[str, Any]) -> bool:
    reference = schema.get("ref")
    if reference is not None:
        if reference not in SCHEMAS:
            raise ValueError("provider constraint reference missing")
        if not _matches(value, SCHEMAS[reference]):
            return False
    kind = schema.get("type")
    kinds = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool}
    if kind is not None and (kind not in kinds or type(value) is not kinds[kind]):
        return False
    if value is None:
        return False
    if "const" in schema and not _equal(value, schema["const"]):
        return False
    if "enum" in schema and not any(_equal(value, allowed) for allowed in schema["enum"]):
        return False
    if type(value) is int:
        if schema.get("format") == "int32" and not -(2**31) <= value <= 2**31 - 1:
            return False
        if schema.get("format") == "int64" and not -(2**63) <= value <= 2**63 - 1:
            return False
        if ("minimum" in schema and value < schema["minimum"]) or ("maximum" in schema and value > schema["maximum"]):
            return False
        if ("exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]) or (
                "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]):
            return False
    if isinstance(value, str):
        if ("minLength" in schema and len(value) < schema["minLength"]) or (
                "maxLength" in schema and len(value) > schema["maxLength"]):
            return False
        if "pattern" in schema and not pattern_matches(schema["pattern"], value):
            return False
        if "format" in schema and not _format(value, schema["format"]):
            return False
    if isinstance(value, list):
        if ("minItems" in schema and len(value) < schema["minItems"]) or (
                "maxItems" in schema and len(value) > schema["maxItems"]):
            return False
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, ensure_ascii=True) for item in value]
            if len(set(encoded)) != len(value):
                return False
        if "items" in schema and any(not _matches(item, schema["items"]) for item in value):
            return False
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if any(key not in value for key in schema.get("required", [])):
            return False
        if schema.get("additionalProperties") is False and not value.keys() <= properties.keys():
            return False
        if any(key in value and not _matches(value[key], child) for key, child in properties.items()):
            return False
    if any(not _matches(value, child) for child in schema.get("allOf", [])):
        return False
    if "anyOf" in schema and not any(_matches(value, child) for child in schema["anyOf"]):
        return False
    if "oneOf" in schema and sum(_matches(value, child) for child in schema["oneOf"]) != 1:
        return False
    if "not" in schema and _matches(value, schema["not"]):
        return False
    if "if" in schema:
        branch = "then" if _matches(value, schema["if"]) else "else"
        if branch in schema and not _matches(value, schema[branch]):
            return False
    return True
