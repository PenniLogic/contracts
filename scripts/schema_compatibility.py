"""Bounded source proofs for additive tagged constraints; an unproved case stays a finding."""

from __future__ import annotations

import json
import math
import re

MAX_DEPTH = 64
MAX_NODES = 16384
MAX_MEMBERS = 256
DIALECTS = {
    "https://spec.openapis.org/oas/3.1/dialect/base",
    "https://json-schema.org/draft/2020-12/schema",
}
ANNOTATIONS = {
    "title", "description", "default", "example", "examples", "deprecated", "readOnly", "writeOnly",
    "externalDocs", "xml", "x-pennilogic-strict-provider", "x-pennilogic-provider-validator",
    "x-not-money", "x-state-denials",
}
SCHEMA_KEYS = ANNOTATIONS | {
    "$ref", "type", "enum", "const", "format", "pattern", "minLength", "maxLength",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "properties", "required", "additionalProperties", "items", "minItems", "maxItems",
    "minProperties", "maxProperties", "uniqueItems", "allOf", "anyOf", "oneOf", "not", "if", "then", "else",
}
NEGATIVE_POSITIONS = {"if", "then", "else", "not", "oneOf", "anyOf"}


class Unproved(Exception):
    """The source or record is outside the deliberately finite proof."""


def identity(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def scalar_key(value: object) -> tuple[str, object]:
    if type(value) not in (str, int, float, bool, type(None)) or \
            (type(value) is float and not math.isfinite(value)):
        raise Unproved("non-scalar value")
    return ("number" if type(value) in (int, float) else type(value).__name__, value)


def finite(values: object) -> dict[tuple[str, object], object]:
    if not isinstance(values, list) or not 0 < len(values) <= MAX_MEMBERS:
        raise Unproved("unbounded finite set")
    result = {scalar_key(value): value for value in values}
    if len(result) != len(values):
        raise Unproved("duplicate finite values")
    return result


class SourceProof:
    def __init__(self, base: dict, revision: dict) -> None:
        self.base, self.revision = base, revision
        self.work = 0
        self.closed: dict[tuple[int, int], tuple[object, object, int]] = {}
        self.tag_domains: dict[tuple[int, int, str, int], set[str] | None] = {}
        self.tag_frames: dict[tuple[int, int, str, str, int], dict | None] = {}
        self.relations: dict[tuple[int, int, bool], tuple[dict, dict, bool]] = {}
        self.diffs: dict[tuple[int, int], tuple[dict, dict, dict]] = {}

    def clear(self) -> None:
        self.closed.clear()
        self.tag_domains.clear()
        self.tag_frames.clear()
        self.relations.clear()
        self.diffs.clear()

    def step(self, depth: int) -> None:
        self.work += 1
        if depth > MAX_DEPTH or self.work > MAX_NODES:
            raise Unproved("source traversal bound")

    def documents(self) -> None:
        for document in (self.base, self.revision):
            if not isinstance(document, dict) or document.get("openapi") != "3.1.0" or \
                    not isinstance(document.get("info"), dict) or not isinstance(document.get("paths"), dict) or \
                    any(not isinstance(document["info"].get(key), str) or not document["info"][key]
                        for key in ("title", "version")) or \
                    not isinstance(document.get("components"), dict) or \
                    not isinstance(document["components"].get("schemas"), dict) or \
                    any(key in document for key in ("$schema", "$id", "$vocabulary", "$dynamicAnchor")):
                raise Unproved("unsupported OpenAPI document")
        dialects = [document.get("jsonSchemaDialect", "https://spec.openapis.org/oas/3.1/dialect/base")
                    for document in (self.base, self.revision)]
        if dialects[0] != dialects[1] or any(not isinstance(dialect, str) or dialect not in DIALECTS for dialect in dialects):
            raise Unproved("unsupported dialect")

    def reference(self, document: dict, reference: object) -> dict:
        if not isinstance(reference, str) or not reference.startswith("#/"):
            raise Unproved("non-local reference")
        value: object = document
        for token in reference[2:].split("/"):
            if re.search(r"~(?![01])", token) or "%" in token:
                raise Unproved("ambiguous pointer")
            token = token.replace("~1", "/").replace("~0", "~")
            if isinstance(value, dict) and token in value:
                value = value[token]
            elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", token) and int(token) < len(value):
                value = value[int(token)]
            else:
                raise Unproved("unresolved pointer")
        if not isinstance(value, dict):
            raise Unproved("non-object reference")
        return value

    def audit(self, node: object, document: dict, active: frozenset[str] = frozenset(), depth: int = 0) -> None:
        self.step(depth)
        if not isinstance(node, dict) or set(node) - SCHEMA_KEYS:
            raise Unproved("unsupported schema keyword")
        if "$ref" in node:
            ref = node["$ref"]
            target = self.reference(document, ref)
            if ref in active:
                raise Unproved("cyclic reference")
            self.audit(target, document, active | {ref}, depth + 1)
        if "type" in node:
            declared = node["type"]
            nullable = isinstance(declared, list) and len(declared) == 2 and \
                all(isinstance(kind, str) for kind in declared) and len(set(declared)) == 2 and \
                "null" in declared and all(kind in ("string", "integer", "boolean", "null") for kind in declared)
            if not nullable and declared not in ("object", "array", "string", "integer", "number", "boolean", "null"):
                raise Unproved("unsupported type")
        if "enum" in node:
            finite(node["enum"])
        if "const" in node:
            scalar_key(node["const"])
        for key in ("format", "pattern", "description", "title"):
            if key in node and not isinstance(node[key], str):
                raise Unproved("malformed scalar annotation")
        for key in ("required",):
            if key in node and (not isinstance(node[key], list) or len(node[key]) > MAX_MEMBERS or
                                any(not isinstance(name, str) or not name for name in node[key]) or
                                len(set(node[key])) != len(node[key])):
                raise Unproved("malformed required members")
        for key in ("minLength", "maxLength", "minItems", "maxItems", "minProperties", "maxProperties"):
            if key in node and (type(node[key]) is not int or node[key] < 0):
                raise Unproved("malformed bound")
        for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"):
            if key in node and (type(node[key]) not in (int, float) or not math.isfinite(node[key])):
                raise Unproved("malformed numeric constraint")
        for key in ("additionalProperties", "uniqueItems"):
            if key in node and type(node[key]) is not bool:
                raise Unproved("unsupported object or array evaluation")
        if "properties" in node:
            if not isinstance(node["properties"], dict) or len(node["properties"]) > MAX_MEMBERS:
                raise Unproved("malformed properties")
            for child in node["properties"].values():
                self.audit(child, document, active, depth + 1)
        for key in ("items", "not", "if", "then", "else"):
            if key in node:
                self.audit(node[key], document, active, depth + 1)
        if ("then" in node or "else" in node) and "if" not in node:
            raise Unproved("unbound conditional")
        for key in ("allOf", "anyOf", "oneOf"):
            if key not in node:
                continue
            members = node[key]
            if not isinstance(members, list) or not 0 < len(members) <= MAX_MEMBERS or \
                    len({identity(member) for member in members}) != len(members):
                raise Unproved("ambiguous composition")
            for child in members:
                self.audit(child, document, active, depth + 1)

    def closure(self, value: object, document: dict, active: frozenset[str] = frozenset(), depth: int = 0) -> object:
        return self.closed_value(value, document, active, depth)[0]

    def closed_value(self, value: object, document: dict, active: frozenset[str], depth: int) -> tuple[object, int]:
        self.step(depth)
        key = id(document), id(value)
        cached = self.closed.get(key)
        if cached is not None:
            if depth + cached[2] > MAX_DEPTH:
                raise Unproved("cached closure depth bound")
            return cached[1], cached[2]
        height = 0
        if isinstance(value, dict):
            members = []
            for name, child in sorted(value.items()):
                if name != "$ref":
                    closed, child_height = self.closed_value(child, document, active, depth + 1)
                    members.append((name, closed))
                    height = max(height, child_height + 1)
            if "$ref" in value:
                ref = value["$ref"]
                target = self.reference(document, ref)
                if ref in active:
                    raise Unproved("cyclic metadata reference")
                # Tuples retain reference identity without a forgeable marker in the source object.
                closed, child_height = self.closed_value(target, document, active | {ref}, depth + 1)
                height = max(height, child_height + 1)
                result = ("reference", ref, closed, members)
            else:
                result = ("object", members)
        elif isinstance(value, list):
            members = []
            for child in value:
                closed, child_height = self.closed_value(child, document, active, depth + 1)
                members.append(closed)
                height = max(height, child_height + 1)
            result = ("array", members)
        else:
            result = ("scalar", scalar_key(value))
        self.closed[key] = value, result, height
        return result, height

    def same(self, before: object, after: object) -> bool:
        return self.closure(before, self.base) == self.closure(after, self.revision)

    def conjuncts(self, node: dict, document: dict, depth: int = 0) -> list[dict]:
        self.step(depth)
        result = [node]
        if "$ref" in node:
            result += self.conjuncts(self.reference(document, node["$ref"]), document, depth + 1)
        for member in node.get("allOf", []):
            result += self.conjuncts(member, document, depth + 1)
        return result

    def scalar_view(self, nodes: list[dict], document: dict) -> tuple[str, set | None]:
        parts = [part for node in nodes for part in self.conjuncts(node, document)]
        if any(isinstance(part.get("type"), list) for part in parts):
            raise Unproved("nullable discriminator or scalar projection")
        types = {part["type"] for part in parts if "type" in part}
        if len(types) != 1 or next(iter(types)) not in ("string", "integer", "number", "boolean", "null") or \
                any(set(part) & ({"if", "then", "else", "properties", "items"}) for part in parts):
            raise Unproved("non-scalar intersection")
        domain: set | None = None
        for part in parts:
            for values in ([part["enum"]] if "enum" in part else []) + ([[part["const"]]] if "const" in part else []):
                members = set(finite(values))
                domain = members if domain is None else domain & members
            for keyword in ("anyOf", "oneOf"):
                if keyword in part:
                    members: set = set()
                    for child in part[keyword]:
                        if set(child) not in ({"const"}, {"enum"}):
                            raise Unproved("unsupported scalar union")
                        values = finite([child["const"]] if "const" in child else child["enum"])
                        if keyword == "oneOf" and members & set(values):
                            raise Unproved("overlapping scalar union")
                        members.update(values)
                    domain = members if domain is None else domain & members
            if "not" in part:
                child = part["not"]
                if domain is None or set(child) not in ({"const"}, {"enum"}):
                    raise Unproved("unsupported scalar exclusion")
                domain -= set(finite([child["const"]] if "const" in child else child["enum"]))
        if domain == set():
            raise Unproved("empty scalar upper bound")
        return next(iter(types)), domain

    def scalar_structure(self, node: dict, document: dict, depth: int = 0) -> object:
        self.step(depth)
        result = {key: value for key, value in node.items() if key not in ("type", "enum", "description", "$ref", "allOf")}
        refs = None if "$ref" not in node else (
            node["$ref"], self.scalar_structure(self.reference(document, node["$ref"]), document, depth + 1))
        return (identity(result), refs,
                [self.scalar_structure(member, document, depth + 1) for member in node.get("allOf", [])])

    def domain(self, node: dict, document: dict, field: str) -> set[str]:
        parts = self.conjuncts(node, document)
        types = {part["type"] for part in parts if "type" in part}
        required = {name for part in parts for name in part.get("required", [])}
        if types != {"object"} or field not in required:
            raise Unproved("discriminator is not unconditionally required on an object")
        fields = [part["properties"][field] for part in parts if field in part.get("properties", {})]
        kind, domain = self.scalar_view(fields, document)
        if kind != "string" or domain is None or any(tag != "str" or not value for tag, value in domain):
            raise Unproved("discriminator lacks a nonempty finite string upper bound")
        return {value for _, value in domain}

    def new_guard(self, member: dict, before: dict, after: dict) -> bool:
        if set(member) != {"if", "then"}:
            return False
        guard, consequence = member["if"], member["then"]
        if set(guard) != {"properties", "required"} or len(guard["properties"]) != 1:
            return False
        field, predicate = next(iter(guard["properties"].items()))
        if guard["required"] != [field] or set(predicate) not in ({"const"}, {"enum"}):
            return False
        tags = finite([predicate["const"]] if "const" in predicate else predicate["enum"])
        if any(kind != "str" or not value for kind, value in tags):
            return False
        old = self.domain(before, self.base, field)
        new = self.domain(after, self.revision, field)
        if not {value for _, value in tags} <= new - old:
            return False
        if set(consequence) != {"properties"} or not consequence["properties"]:
            return False
        for fixed in consequence["properties"].values():
            if set(fixed) not in ({"const"}, {"enum"}):
                return False
            finite([fixed["const"]] if "const" in fixed else fixed["enum"])
        return True

    def tag_values(self, node: dict, document: dict, field: str, depth: int = 0) -> set[str] | None:
        self.step(depth)
        key = id(document), id(node), field, depth
        if key in self.tag_domains:
            return self.tag_domains[key]
        parts = self.conjuncts(node, document, depth)
        fields = [part["properties"][field] for part in parts if field in part.get("properties", {})]
        values: set[str] | None = None
        if fields:
            augmented = [{"type": "string"}, *fields]
            kind, domain = self.scalar_view(augmented, document)
            if kind != "string" or domain is None or any(kind != "str" or not value for kind, value in domain):
                raise Unproved("unbounded or non-string tag")
            values = {value for _, value in domain}
        for part in parts:
            for keyword in ("oneOf", "anyOf"):
                if keyword not in part:
                    continue
                children = [self.tag_values(child, document, field, depth + 1) for child in part[keyword]]
                if any(child is None for child in children):
                    raise Unproved("unbound object union")
                union = set().union(*children)
                values = union if values is None else values & union
        self.tag_domains[key] = values
        return values

    def tag_condition(self, node: dict, field: str, tag: str) -> bool | None:
        if set(node) != {"properties", "required"} or node["required"] != [field] or \
                set(node["properties"]) != {field}:
            return None
        predicate = node["properties"][field]
        negate = set(predicate) == {"not"}
        if negate:
            predicate = predicate["not"]
        if set(predicate) not in ({"const"}, {"enum"}):
            return None
        members = finite([predicate["const"]] if "const" in predicate else predicate["enum"])
        if any(kind != "str" or not value for kind, value in members):
            raise Unproved("non-string tag predicate")
        result = ("str", tag) in members
        return not result if negate else result

    def tag_frame(self, node: dict, document: dict, field: str, tag: str, depth: int = 0) -> dict | None:
        """Retain every reachable assertion; only a known required discriminator selects a branch."""
        self.step(depth)
        key = id(document), id(node), field, tag, depth
        if key in self.tag_frames:
            return self.tag_frames[key]
        values = self.tag_values(node, document, field, depth)
        if values is not None and tag not in values:
            self.tag_frames[key] = None
            return None
        frame = {"required": set(), "forbidden": set(), "closed": None, "properties": {},
                 "opaque": [], "unions": [], "references": []}

        def collect(value: dict, level: int) -> None:
            self.step(level)
            if "$ref" in value:
                frame["references"].append(value["$ref"])
                collect(self.reference(document, value["$ref"]), level + 1)
            frame["required"].update(value.get("required", []))
            if value.get("additionalProperties") is False:
                names = set(value.get("properties", {}))
                frame["closed"] = names if frame["closed"] is None else frame["closed"] & names
            for name, child in value.get("properties", {}).items():
                frame["properties"].setdefault(name, []).append(child)
            for child in value.get("allOf", []):
                collect(child, level + 1)
            if "if" in value:
                selected = self.tag_condition(value["if"], field, tag)
                if selected is None:
                    frame["opaque"].append({key: value[key] for key in ("if", "then", "else") if key in value})
                else:
                    collect(value.get("then" if selected else "else", {}), level + 1)
            if "not" in value:
                excluded = value["not"]
                terms = excluded.get("anyOf", []) if set(excluded) == {"anyOf"} else [excluded]
                if not terms or any(set(term) != {"required"} or len(term["required"]) != 1 for term in terms):
                    raise Unproved("unsupported object exclusion")
                frame["forbidden"].update(term["required"][0] for term in terms)
            for keyword in ("oneOf", "anyOf"):
                if keyword in value:
                    frame["unions"].append((keyword, value[keyword]))
            stable = {key: item for key, item in value.items()
                      if key not in ANNOTATIONS | {"$ref", "properties", "required", "additionalProperties",
                                                  "allOf", "if", "then", "else", "not", "oneOf", "anyOf"}}
            if stable:
                frame["opaque"].append(stable)

        collect(node, depth)
        if frame["required"] & frame["forbidden"] or \
                (frame["closed"] is not None and not frame["required"] <= frame["closed"]):
            self.tag_frames[key] = None
            return None
        self.tag_frames[key] = frame
        return frame

    def frames_preserve(self, before: dict | None, after: dict | None, field: str, tag: str,
                        response: bool, depth: int, *, partial: bool = False) -> bool:
        self.step(depth)
        source, target = (after, before) if response else (before, after)
        if source is None:
            return True
        if partial and before is not None and after is not None and \
                set(before["properties"]) != set(after["properties"]):
            return False
        if target is None or before["references"] != after["references"] or \
                len(before["opaque"]) != len(after["opaque"]) or any(
                    not self.same(left, right) for left, right in zip(before["opaque"], after["opaque"])):
            return False
        if not target["required"] <= source["required"]:
            return False
        allowed_source, allowed_target = source["closed"], target["closed"]
        if partial and allowed_source is None and allowed_target is None:
            if set(source["properties"]) != set(target["properties"]):
                return False
            allowed_source = set(source["properties"])
            allowed_target = set(target["properties"])
        elif allowed_source is None or allowed_target is None:
            raise Unproved("open tagged object")
        allowed_source -= source["forbidden"]
        allowed_target -= target["forbidden"]
        if target["forbidden"] & allowed_source:
            return False
        if not allowed_source <= allowed_target:
            return False
        for name in allowed_source:
            old = before["properties"].get(name, [])
            new = after["properties"].get(name, [])
            if name == field:
                kind_old, values_old = self.scalar_view([{"type": "string"}, *old], self.base)
                kind_new, values_new = self.scalar_view([{"type": "string"}, *new], self.revision)
                if kind_old != kind_new or values_old is None or values_new is None or \
                        ("str", tag) not in values_old or ("str", tag) not in values_new or \
                        len(old) != len(new) or any(self.scalar_structure(left, self.base) !=
                                                   self.scalar_structure(right, self.revision)
                                                   for left, right in zip(old, new)):
                    return False
            else:
                if len(old) != len(new):
                    return False
                if all(self.same(left, right) for left, right in zip(old, new)):
                    continue
                try:
                    kind_old, values_old = self.scalar_view(old, self.base)
                    kind_new, values_new = self.scalar_view(new, self.revision)
                    scalar = kind_old == kind_new and all(
                        self.scalar_structure(left, self.base) == self.scalar_structure(right, self.revision)
                        for left, right in zip(old, new)) and (
                            values_old is None and values_new is None or
                            values_old is not None and values_new is not None and
                            (values_new <= values_old if response else values_old <= values_new))
                except Unproved:
                    scalar = False
                if not scalar and any(not self.preserves(left, right, depth + 1, response=response)
                                      for left, right in zip(old, new)):
                    return False
        if len(before["unions"]) != len(after["unions"]):
            return False
        for (keyword, old), (other, new) in zip(before["unions"], after["unions"]):
            if keyword != other or len(old) != len(new):
                return False
            for left, right in zip(old, new):
                if left.get("$ref") != right.get("$ref") or not self.frames_preserve(
                        self.tag_frame(left, self.base, field, tag, depth + 1),
                        self.tag_frame(right, self.revision, field, tag, depth + 1),
                        field, tag, response, depth + 1, partial=True):
                    return False
        return True

    def tagged_preserves(self, before: dict, after: dict, response: bool, depth: int) -> bool:
        old_parts, new_parts = self.conjuncts(before, self.base), self.conjuncts(after, self.revision)
        old_required = set().union(*(set(part.get("required", [])) for part in old_parts))
        new_required = set().union(*(set(part.get("required", [])) for part in new_parts))
        old_names = set().union(*(set(part.get("properties", {})) for part in old_parts))
        new_names = set().union(*(set(part.get("properties", {})) for part in new_parts))
        if new_names - old_names - (after.get("properties", {}).keys() - before.get("properties", {}).keys()) and \
                not self.has_addition(before, after):
            return False
        if {identity(part["type"]) for part in old_parts if "type" in part} != {'"object"'} or \
                {identity(part["type"]) for part in new_parts if "type" in part} != {'"object"'}:
            return False

        prefix, revised = before.get("allOf", []), after.get("allOf", [])
        if len(revised) > len(prefix):
            old_names = set(before.get("properties", {}))
            new_names = set(after.get("properties", {})) - old_names
            for branch in revised[len(prefix):]:
                if set(branch) - {"if", "then", "else"} or "if" not in branch or "then" not in branch:
                    return False
                consequence = branch["then"]
                if set(consequence) - {"properties", "required", "not"} or \
                        not set(consequence.get("required", [])) <= new_names:
                    return False
                for name, constraint in consequence.get("properties", {}).items():
                    if name in old_names and set(constraint) not in ({"const"}, {"enum"}):
                        return False
                if "else" in branch and set(branch["else"]) != {"not"}:
                    return False
                for value in (consequence.get("not"), branch.get("else", {}).get("not")):
                    if value is not None:
                        terms = value.get("anyOf", []) if set(value) == {"anyOf"} else [value]
                        if not terms or any(set(term) != {"required"} or len(term["required"]) != 1 or
                                            term["required"][0] not in new_names for term in terms):
                            return False
        for field in sorted(old_required & new_required):
            self.step(depth)
            try:
                old = self.tag_values(before, self.base, field, depth)
                new = self.tag_values(after, self.revision, field, depth)
                if not old or not new:
                    continue
                source, target = (new, old) if response else (old, new)
                if not source <= target:
                    return False
                used: set[str] = set()
                for branch in revised[len(prefix):]:
                    predicate = branch["if"]
                    if set(predicate) != {"properties", "required"} or predicate["required"] != [field] or \
                            set(predicate["properties"]) != {field}:
                        raise Unproved("unbound appended tag guard")
                    value = predicate["properties"][field]
                    if set(value) in ({"const"}, {"enum"}):
                        members = finite([value["const"]] if "const" in value else value["enum"])
                        if any(kind != "str" or not tag for kind, tag in members):
                            raise Unproved("non-string appended guard")
                        tags = {tag for _, tag in members}
                        if not tags <= new - old or used & tags:
                            return False
                        used.update(tags)
                if all(self.frames_preserve(self.tag_frame(before, self.base, field, tag, depth),
                                            self.tag_frame(after, self.revision, field, tag, depth),
                                            field, tag, response, depth + 1) for tag in sorted(source)):
                    return True
            except Unproved:
                continue
        return False

    def plain_preserves(self, before: dict, after: dict, response: bool, depth: int) -> bool:
        old = self.conjuncts(before, self.base)
        new = self.conjuncts(after, self.revision)
        if any(set(part) & NEGATIVE_POSITIONS for part in old + new) or \
                {identity(part["type"]) for part in old if "type" in part} != {'"object"'} or \
                {identity(part["type"]) for part in new if "type" in part} != {'"object"'}:
            return False
        return self.frames_preserve(self.tag_frame(before, self.base, "", "", depth),
                                    self.tag_frame(after, self.revision, "", "", depth),
                                    "", "", response, depth + 1)

    def preserves(self, before: dict, after: dict, depth: int = 0, *, response: bool = False) -> bool:
        self.step(depth)
        key = id(before), id(after), response
        cached = self.relations.get(key)
        if cached is not None:
            self.closed_value(before, self.base, frozenset(), depth)
            self.closed_value(after, self.revision, frozenset(), depth)
            return cached[2]
        result = self.preserves_uncached(before, after, depth, response=response)
        self.relations[key] = before, after, result
        return result

    def preserves_uncached(self, before: dict, after: dict, depth: int = 0, *, response: bool = False) -> bool:
        if self.same(before, after):
            return True
        if before.get("$ref") != after.get("$ref"):
            return False
        if "$ref" in before and not (set(before) | set(after)) - {"$ref", "description"}:
            return self.preserves(self.reference(self.base, before["$ref"]),
                                  self.reference(self.revision, after["$ref"]), depth + 1, response=response)
        if identity(before) == identity(after):
            try:
                if self.schema_diff(before, after, depth + 1) == {}:
                    return True
            except Unproved:
                pass
        try:
            old_type, old_domain = self.scalar_view([before], self.base)
            new_type, new_domain = self.scalar_view([after], self.revision)
        except Unproved:
            pass
        else:
            return old_type == new_type and self.scalar_structure(before, self.base) == \
                self.scalar_structure(after, self.revision) and (
                    old_domain is None and new_domain is None or
                    old_domain is not None and new_domain is not None and
                    (new_domain <= old_domain if response else old_domain <= new_domain))
        if self.tagged_preserves(before, after, response, depth + 1):
            return True
        if self.plain_preserves(before, after, response, depth + 1):
            return True
        if set(before) & NEGATIVE_POSITIONS or set(after) & NEGATIVE_POSITIONS:
            return False
        if "$ref" in before and not self.preserves(self.reference(self.base, before["$ref"]),
                                                 self.reference(self.revision, after["$ref"]), depth + 1, response=response):
            return False
        stable = set(before) | set(after)
        stable -= {"$ref", "description", "properties", "items", "allOf"}
        if any(key not in before or key not in after or not self.same(before[key], after[key]) for key in stable):
            return False
        old_properties, new_properties = before.get("properties", {}), after.get("properties", {})
        if set(old_properties) != set(new_properties) or any(
                not self.preserves(child, new_properties[name], depth + 1, response=response) for name, child in old_properties.items()):
            return False
        if ("items" in before) != ("items" in after) or \
                ("items" in before and not self.preserves(before["items"], after["items"], depth + 1, response=response)):
            return False
        prefix, revised = before.get("allOf", []), after.get("allOf", [])
        if len(revised) < len(prefix) or any(identity(member) != identity(revised[index]) or
                not self.preserves(member, revised[index], depth + 1, response=response) for index, member in enumerate(prefix)):
            return False
        used: set[str] = set()
        discriminator = None
        for member in revised[len(prefix):]:
            if response:
                return False
            if not self.new_guard(member, before, after):
                return False
            field, predicate = next(iter(member["if"]["properties"].items()))
            tags = set(predicate["enum"] if "enum" in predicate else [predicate["const"]])
            if discriminator is not None and field != discriminator or used & tags:
                return False
            discriminator = field
            used.update(tags)
        return True

    def effective(self, node: dict, document: dict, depth: int = 0) -> dict:
        self.step(depth)
        if "$ref" not in node:
            return node
        result = dict(self.effective(self.reference(document, node["$ref"]), document, depth + 1))
        for key, value in node.items():
            if key == "$ref":
                continue
            if key not in result or key in ANNOTATIONS:
                result[key] = value
            elif key == "enum":
                left, right = finite(result[key]), finite(value)
                result[key] = [member for token, member in right.items() if token in left]
                finite(result[key])
            elif identity(result[key]) != identity(value):
                raise Unproved("ambiguous reference-sibling projection")
        return result

    def member_identity(self, index: int, member: dict) -> dict:
        result = {"index": index}
        if "$ref" in member:
            match = re.fullmatch(r"#/components/schemas/([A-Za-z0-9._-]+)", member["$ref"])
            if match is None:
                raise Unproved("ambiguous diff component identity")
            result["component"] = match[1]
        return result

    def schema_diff(self, before: dict, after: dict, depth: int = 0) -> dict:
        """Reconstruct only the complete supported oasdiff record, never trust its summary."""
        self.step(depth)
        key = id(before), id(after)
        cached = self.diffs.get(key)
        if cached is not None:
            self.closed_value(before, self.base, frozenset(), depth)
            self.closed_value(after, self.revision, frozenset(), depth)
            return cached[2]
        result = self.schema_diff_uncached(before, after, depth)
        self.diffs[key] = before, after, result
        return result

    def schema_diff_uncached(self, before: dict, after: dict, depth: int = 0) -> dict:
        if before.get("$ref") != after.get("$ref"):
            raise Unproved("changed reference identity")
        before, after = self.effective(before, self.base), self.effective(after, self.revision)
        result = {}
        for key in set(before) | set(after):
            left, right = before.get(key), after.get(key)
            if key == "properties":
                if not isinstance(left, dict) or not isinstance(right, dict):
                    raise Unproved("changed property envelope")
                children = {name: self.schema_diff(left[name], right[name], depth + 1)
                            for name in left.keys() & right.keys()}
                changed = {name: value for name, value in children.items() if value}
                record = {}
                if right.keys() - left.keys():
                    record["added"] = sorted(right.keys() - left.keys())
                if left.keys() - right.keys():
                    record["deleted"] = sorted(left.keys() - right.keys())
                if changed:
                    record["modified"] = changed
                if record:
                    result[key] = record
            elif key == "items":
                if not isinstance(left, dict) or not isinstance(right, dict):
                    raise Unproved("changed item envelope")
                child = self.schema_diff(left, right, depth + 1)
                if child:
                    result[key] = child
            elif key in ("allOf", "oneOf", "anyOf"):
                left, right = left or [], right or []
                if len(right) < len(left) or (key != "allOf" and len(left) != len(right)):
                    raise Unproved("removed conjunction")
                if key != "allOf" and any(identity(member) != identity(right[index])
                                          for index, member in enumerate(left)):
                    raise Unproved("changed union member identity")
                change = {}
                modified = []
                for index, member in enumerate(left):
                    child = self.schema_diff(member, right[index], depth + 1)
                    if child:
                        modified.append({"base": self.member_identity(index, member),
                                         "revision": self.member_identity(index, right[index]), "diff": child})
                if modified:
                    change["modified"] = modified
                if len(right) > len(left):
                    change["added"] = [self.member_identity(index, right[index]) for index in range(len(left), len(right))]
                if change:
                    result[key] = change
            elif key == "enum":
                old, new = finite(left), finite(right)
                if not set(old) <= set(new):
                    raise Unproved("removed enum constraint or value")
                added = [value for token, value in new.items() if token not in old]
                if added:
                    result[key] = {"added": added}
            elif key == "description":
                if left != right:
                    result[key] = {"from": left or "", "to": right or ""}
            elif not self.same(left, right):
                raise Unproved("unsupported diff field")
        return result

    def record_equal(self, expected: object, actual: object, depth: int = 0) -> bool:
        self.step(depth)
        if isinstance(expected, dict):
            if not isinstance(actual, dict) or set(actual) != set(expected):
                return False
            if set(expected) == {"added"} and isinstance(expected["added"], list) and expected["added"] and \
                    not isinstance(expected["added"][0], dict):
                return set(finite(expected["added"])) == set(finite(actual["added"]))
            return all(self.record_equal(value, actual[key], depth + 1) for key, value in expected.items())
        if isinstance(expected, list):
            return isinstance(actual, list) and len(expected) == len(actual) and all(
                self.record_equal(left, right, depth + 1) for left, right in zip(expected, actual))
        return type(expected) is type(actual) and expected == actual

    def component_references(self, node: object, document: dict, names: set[str],
                             visited: set[str], depth: int = 0) -> None:
        self.step(depth)
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"]
                target = self.reference(document, ref)
                if ref not in visited:
                    visited.add(ref)
                    parts = ref[2:].split("/")
                    if len(parts) < 3 or parts[:2] != ["components", "schemas"]:
                        raise Unproved("reference outside the schema component closure")
                    names.add(parts[2].replace("~1", "/").replace("~0", "~"))
                    self.component_references(target, document, names, visited, depth + 1)
            for key, child in node.items():
                if key != "$ref":
                    self.component_references(child, document, names, visited, depth + 1)
        elif isinstance(node, list):
            for child in node:
                self.component_references(child, document, names, visited, depth + 1)

    def component_records(self, before: dict, after: dict, changes: dict, *,
                          audited: bool = False, use_site_proved: bool = False) -> bool:
        """Bind changed reference dependencies as well as their expanded use-site records."""
        try:
            names: set[str] = set()
            self.component_references(before, self.base, names, set())
            self.component_references(after, self.revision, names, set())
            for name in sorted(names):
                left = self.base["components"]["schemas"].get(name)
                right = self.revision["components"]["schemas"].get(name)
                if not isinstance(left, dict) or not isinstance(right, dict):
                    return False
                if self.same(left, right):
                    continue
                if not audited:
                    self.audit(left, self.base)
                    self.audit(right, self.revision)
                expected = self.schema_diff(left, right)
                if (not use_site_proved and not self.preserves(left, right)) or \
                        (expected and not self.record_equal(expected, changes.get(name))) or \
                        (not expected and name in changes and not self.record_equal(expected, changes[name])):
                    return False
            return True
        except Unproved:
            return False

    def schema(self, before: dict, after: dict, change: object, changes: dict) -> bool:
        try:
            self.work = 0
            self.clear()
            self.documents()
            self.audit(before, self.base)
            self.audit(after, self.revision)
            return self.preserves(before, after) and self.record_equal(self.schema_diff(before, after), change) and \
                self.component_records(before, after, changes, audited=True, use_site_proved=True)
        except Unproved:
            return False

    def response(self, before: dict, after: dict, change: object, changes: dict, *,
                 producer: bool = True) -> bool:
        try:
            self.work = 0
            self.clear()
            self.documents()
            if before.get("$ref") != after.get("$ref"):
                return False
            before, after = self.effective(before, self.base), self.effective(after, self.revision)
            if not self.same({key: value for key, value in before.items() if key != "content"},
                             {key: value for key, value in after.items() if key != "content"}):
                return False
            left, right = before.get("content"), after.get("content")
            if not isinstance(left, dict) or not isinstance(right, dict) or not left or set(left) != set(right):
                return False
            modified = {}
            for media, entry in left.items():
                other = right[media]
                if not isinstance(entry, dict) or not isinstance(other, dict) or \
                        not isinstance(entry.get("schema"), dict) or not isinstance(other.get("schema"), dict) or \
                        not self.same({key: value for key, value in entry.items() if key != "schema"},
                                      {key: value for key, value in other.items() if key != "schema"}):
                    return False
                self.audit(entry["schema"], self.base)
                self.audit(other["schema"], self.revision)
                if not self.preserves(entry["schema"], other["schema"], response=producer):
                    return False
                if not self.component_records(entry["schema"], other["schema"], changes,
                                              audited=True, use_site_proved=True):
                    return False
                child = self.schema_diff(entry["schema"], other["schema"])
                if child:
                    modified[media] = {"schema": child}
            expected = {"content": {"modified": modified}} if modified else {}
            return self.record_equal(expected, change)
        except Unproved:
            return False

    def has_addition(self, before: object, after: object, depth: int = 0,
                     active: frozenset[tuple[str, str]] = frozenset()) -> bool:
        """Select the new proof path even when a partial record omits its actual appended branch."""
        self.step(depth)
        if isinstance(before, dict) and isinstance(after, dict):
            left, right = before.get("allOf", []), after.get("allOf", [])
            if isinstance(left, list) and isinstance(right, list) and len(right) > len(left):
                return True
            if "$ref" in before and "$ref" in after:
                refs = before["$ref"], after["$ref"]
                if any(not isinstance(ref, str) for ref in refs) or refs in active:
                    raise Unproved("cyclic selection")
                if self.has_addition(self.reference(self.base, refs[0]), self.reference(self.revision, refs[1]),
                                     depth + 1, active | {refs}):
                    return True
            return any(self.has_addition(before[key], after[key], depth + 1, active)
                       for key in before.keys() & after.keys() if key != "$ref")
        if isinstance(before, list) and isinstance(after, list):
            return any(self.has_addition(left, right, depth + 1, active) for left, right in zip(before, after))
        return False

    def candidate(self, before: dict, after: dict) -> bool:
        try:
            self.work = 0
            self.clear()
            return self.has_addition(before, after)
        except Unproved:
            return True
