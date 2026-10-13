"""Strict JSON reading and a closed-world draft-07 subset validator for the verification files (T-QA-02).

The verification schemas under scripts/verification/ use only this subset: type, properties, required,
additionalProperties (false only), items (one schema), minItems, maxItems, uniqueItems (only together with
maxItems, which bounds its pairwise comparison), enum, const, pattern, minLength and local
`#/definitions/<name>` references, plus the annotations $schema, $id, title, description and definitions.
A schema that uses any other keyword is refused (SchemaUnsupported) instead of being half-applied.
Patterns are written to mean the same in ECMA-262 and Python `re`: anchored with `^` and `(?![\\s\\S])`,
ASCII character classes only. Length and count bounds live in the code that owns each document. A
violation names the JSON pointer and the keyword, never the value. JSON equality is computed without
recursion, so a deeply nested value cannot exhaust the interpreter's recursion limit.
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

DRAFT_07 = "http://json-schema.org/draft-07/schema#"
KEYWORDS = frozenset({
    "type", "properties", "required", "additionalProperties", "items", "minItems", "maxItems", "uniqueItems",
    "enum", "const", "pattern", "minLength", "$ref", "title", "description",
})
ROOT_KEYWORDS = frozenset({"$schema", "$id", "definitions"})
TYPES = frozenset({"null", "boolean", "integer", "number", "string", "array", "object"})
DEFINITION_NAME = re.compile(r"[A-Za-z0-9_-]+")
NOT_PRINTABLE_ASCII = re.compile(r"[^\x20-\x7e]")


class StrictJsonError(ValueError):
    """The bytes are not strict JSON. The message is static text that never echoes the input."""


class InputUnavailable(OSError):
    """An input path is missing, not a regular file, unreadable or larger than its bound."""


class InputMissing(InputUnavailable):
    """An input path does not exist."""


class InputTooLarge(InputUnavailable):
    """An input file is larger than its bound."""


class SchemaUnsupported(ValueError):
    """A schema file uses something outside the supported subset (a defect of the schema, not of a document)."""


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    value: dict = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _non_finite(_: str) -> object:
    raise ValueError("non-finite number")


def _finite_float(text: str) -> float:
    # json.loads turns an overflowing literal such as 1e400 into infinity without calling parse_constant.
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("non-finite number")
    return value


def parse_strict_json(data: bytes) -> object:
    """Parse UTF-8 JSON without a byte order mark, duplicate keys or non-finite numbers."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise StrictJsonError("is not UTF-8") from None
    if text.startswith("\ufeff"):
        raise StrictJsonError("starts with a byte order mark")
    try:
        return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_non_finite, parse_float=_finite_float)
    except (ValueError, RecursionError):
        raise StrictJsonError("is not strict JSON (syntax, duplicate key or non-finite number)") from None


def read_bounded(path: Path, max_bytes: int) -> bytes:
    """Read a regular file of at most `max_bytes` bytes; raise InputUnavailable otherwise."""
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            raise InputUnavailable("is not a regular file")
        with open(path, "rb") as handle:
            data = handle.read(max_bytes + 1)
    except InputUnavailable:
        raise
    except FileNotFoundError:
        raise InputMissing("does not exist") from None
    except OSError as error:
        raise InputUnavailable(f"cannot be read ({type(error).__name__})") from None
    if len(data) > max_bytes:
        raise InputTooLarge(f"exceeds {max_bytes} bytes")
    return data


def dumps(value: object) -> str:
    """The repository's JSON text form: two-space indent, ASCII only, final newline."""
    return json.dumps(value, indent=2, ensure_ascii=True) + "\n"


def _escape_character(match: re.Match[str]) -> str:
    code = ord(match.group())
    return f"\\u{code:04x}" if code <= 0xFFFF else f"\\U{code:08x}"


def printable_text(value: str, limit: int) -> str:
    """Escape every character outside printable ASCII and cap the length.

    Detector text comes from the compared documents. Escaping C0 and C1 controls, bidirectional and invisible
    format characters and everything else outside U+0020-U+007E keeps such text from restyling or reordering
    a terminal, a log viewer or a report, and keeps the output encodable on any console.
    """
    text = NOT_PRINTABLE_ASCII.sub(_escape_character, value) or "-"
    return text if len(text) <= limit else text[:limit - 3] + "..."


# --- JSON values ----------------------------------------------------------------------------------------

def json_type(value: object) -> str:
    if value is None:
        return "null"
    if value is True or value is False:
        return "boolean"
    return {int: "integer", float: "number", str: "string", list: "array", dict: "object"}.get(type(value), "unknown")


def _has_type(value: object, name: str) -> bool:
    actual = json_type(value)
    return actual == name or (name == "number" and actual == "integer")


def json_equal(left: object, right: object) -> bool:
    """JSON equality: booleans are not numbers, 1 equals 1.0, arrays are ordered, objects are not.

    Iterative: the strict reader accepts nesting far deeper than the interpreter's recursion limit.
    """
    pending = [(left, right)]
    while pending:
        one, other = pending.pop()
        kinds = {json_type(one), json_type(other)}
        if kinds <= {"integer", "number"}:
            if one != other:
                return False
        elif len(kinds) != 1:
            return False
        elif isinstance(one, list) and isinstance(other, list):
            if len(one) != len(other):
                return False
            pending.extend(zip(one, other))
        elif isinstance(one, dict) and isinstance(other, dict):
            if one.keys() != other.keys():
                return False
            pending.extend((one[key], other[key]) for key in one)
        elif one != other:
            return False
    return True


# --- schemas ----------------------------------------------------------------------------------------------

def load_schema(path: Path) -> dict:
    """Read a committed schema file and refuse it unless it stays inside the supported subset."""
    try:
        schema = parse_strict_json(read_bounded(path, 1024 * 1024))
    except (InputUnavailable, StrictJsonError) as error:
        raise SchemaUnsupported(f"{path.name} {error}") from None
    check_schema(schema)
    assert isinstance(schema, dict)
    return schema


def check_schema(schema: object) -> None:
    if not isinstance(schema, dict) or schema.get("$schema") != DRAFT_07:
        raise SchemaUnsupported("the schema is not a draft-07 schema object")
    definitions = schema.get("definitions", {})
    if not isinstance(definitions, dict) or any(DEFINITION_NAME.fullmatch(name) is None for name in definitions):
        raise SchemaUnsupported("definitions must map plain names to schemas")
    _check_node(schema, definitions, "", root=True)
    for name, node in definitions.items():
        _check_node(node, definitions, f"/definitions/{name}")


def _check_node(node: object, definitions: dict, pointer: str, root: bool = False) -> None:
    where = pointer or "/"
    if not isinstance(node, dict):
        raise SchemaUnsupported(f"{where} is not a schema object")
    unknown = set(node) - (KEYWORDS | ROOT_KEYWORDS if root else KEYWORDS)
    if unknown:
        raise SchemaUnsupported(f"{where} uses keywords outside the supported subset: {sorted(map(ascii, unknown))}")
    if "$ref" in node:
        reference = node["$ref"]
        match = re.fullmatch(r"#/definitions/([A-Za-z0-9_-]+)", reference) if isinstance(reference, str) else None
        if match is None or match.group(1) not in definitions or set(node) - {"$ref", "title", "description"}:
            raise SchemaUnsupported(f"{where} $ref must name a definition and stand alone")
    if "type" in node:
        types = node["type"] if isinstance(node["type"], list) else [node["type"]]
        if not types or any(not isinstance(name, str) or name not in TYPES for name in types) or len(set(types)) != len(types):
            raise SchemaUnsupported(f"{where} type must name JSON types")
    if "properties" in node:
        if not isinstance(node["properties"], dict):
            raise SchemaUnsupported(f"{where} properties must be an object")
        for name, child in node["properties"].items():
            _check_node(child, definitions, f"{pointer}/properties/{name}")
    required = node.get("required", [])
    if not isinstance(required, list) or any(not isinstance(name, str) for name in required) or len(set(required)) != len(required):
        raise SchemaUnsupported(f"{where} required must list distinct names")
    if "additionalProperties" in node and node["additionalProperties"] is not False:
        raise SchemaUnsupported(f"{where} additionalProperties supports only false")
    if "items" in node:
        _check_node(node["items"], definitions, f"{pointer}/items")
    for keyword in ("minItems", "maxItems", "minLength"):
        if keyword in node and (type(node[keyword]) is not int or node[keyword] < 0):
            raise SchemaUnsupported(f"{where} {keyword} must be a non-negative integer")
    if "uniqueItems" in node and not isinstance(node["uniqueItems"], bool):
        raise SchemaUnsupported(f"{where} uniqueItems must be a boolean")
    if node.get("uniqueItems") is True and "maxItems" not in node:
        raise SchemaUnsupported(f"{where} uniqueItems needs maxItems, which bounds its pairwise comparison")
    if "enum" in node and (not isinstance(node["enum"], list) or not node["enum"]):
        raise SchemaUnsupported(f"{where} enum must be a non-empty list")
    if "pattern" in node:
        try:
            re.compile(node["pattern"])
        except (TypeError, re.error):
            raise SchemaUnsupported(f"{where} pattern is not a regular expression") from None


@dataclass(frozen=True)
class SchemaViolation:
    """The first place a document leaves its schema: a JSON pointer and the failing keyword."""

    pointer: str
    keyword: str

    def __str__(self) -> str:
        return f"{self.pointer or '/'} fails {self.keyword}"


def _escape(name: str) -> str:
    return name.replace("~", "~0").replace("/", "~1")


def first_violation(instance: object, schema: dict, root: dict | None = None, pointer: str = "") -> SchemaViolation | None:
    """Validate `instance` against a checked schema (check_schema); return the first violation or None."""
    root = schema if root is None else root
    if "$ref" in schema:
        return first_violation(instance, root["definitions"][schema["$ref"].rsplit("/", 1)[1]], root, pointer)
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_has_type(instance, name) for name in types):
            return SchemaViolation(pointer, "type")
    if "const" in schema and not json_equal(instance, schema["const"]):
        return SchemaViolation(pointer, "const")
    if "enum" in schema and not any(json_equal(instance, option) for option in schema["enum"]):
        return SchemaViolation(pointer, "enum")
    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            return SchemaViolation(pointer, "minLength")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            return SchemaViolation(pointer, "pattern")
    if isinstance(instance, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in instance:
                return SchemaViolation(f"{pointer}/{_escape(name)}", "required")
        if schema.get("additionalProperties") is False and not set(instance) <= set(properties):
            return SchemaViolation(pointer, "additionalProperties")
        for name, child in properties.items():
            if name in instance:
                violation = first_violation(instance[name], child, root, f"{pointer}/{_escape(name)}")
                if violation is not None:
                    return violation
    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            return SchemaViolation(pointer, "minItems")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            return SchemaViolation(pointer, "maxItems")
        if schema.get("uniqueItems") and any(json_equal(item, other) for index, item in enumerate(instance)
                                             for other in instance[index + 1:]):
            return SchemaViolation(pointer, "uniqueItems")
        if "items" in schema:
            for index, item in enumerate(instance):
                violation = first_violation(item, schema["items"], root, f"{pointer}/{index}")
                if violation is not None:
                    return violation
    return None
