"use strict";
const { HTTP_METHODS, SCHEMA_ANNOTATIONS } = require("./_shared");
const { loadSource } = require("./_customDestinationSource");

const ENROLLMENT = new Map([
  ["/ai/custom-destinations", { get: "listCustomDestinations", post: "registerCustomDestination" }],
  ["/ai/custom-destinations/{destinationId}", { delete: "revokeCustomDestination" }],
  ...["validate", "activate", "suspend"].map((action) => [
    `/ai/custom-destinations/{destinationId}/${action}`, { post: `${action}CustomDestination` },
  ]),
]);
const ADDRESS_WORDS = new Set(["url", "uri", "host", "hostname", "endpoint", "address", "port", "headers", "proxy"]);
const ADDRESS_FORMATS = new Set(["uri", "uri-reference", "uri-template", "iri", "iri-reference",
  "url", "hostname", "idn-hostname", "ipv4", "ipv6"]);
const REQUEST_NAME = /(?:inference|assistant|completion|chat|tool|^ai).*?(?:request|input|argument|param)|(?:request|input|argument).*?(?:inference|assistant|completion|chat|tool)/i;
const OPERATION_NAME = /(?:^|[^a-z])(?:ai|inference|assistant|completion|chat|tools?)(?:[^a-z]|$)/i;

function words(name) {
  return String(name).replace(/([A-Z]+)([A-Z][a-z])/g, "$1_$2")
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2").toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
}

function scalarExclusion(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const scalar = (item) => item === null || ["string", "boolean"].includes(typeof item) ||
    typeof item === "number" && Number.isFinite(item);
  return Object.entries(value).every(([key, child]) => {
    if (SCHEMA_ANNOTATIONS.has(key) || key === "summary") return true;
    if (key === "type") return ["string", "integer", "boolean", "null"].includes(child);
    if (key === "const") return scalar(child);
    if (key === "enum") return Array.isArray(child) && child.length > 0 && child.every(scalar);
    if (key === "pattern") return typeof child === "string";
    return ["minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"].includes(key) &&
      typeof child === "number" && Number.isFinite(child);
  });
}

function enrollmentBody(operation, pathName, method) {
  const id = ENROLLMENT.get(pathName)?.[method];
  if (!id || operation.operationId !== id || operation.tags?.length !== 1 || operation.tags[0] !== "CustomDestinations") return false;
  const body = operation.requestBody;
  if (method === "get") return body === undefined;
  if (id !== "registerCustomDestination") {
    return body?.$ref === "#/components/requestBodies/CustomDestinationLifecycle" && Object.keys(body).length === 1;
  }
  const shape = body?.content?.["application/json"]?.schema;
  return body?.required === true && Object.keys(body.content || {}).length === 1 &&
    shape?.$ref === "#/components/schemas/CustomDestinationRegistrationRequest" && Object.keys(shape).length === 1;
}

module.exports = function noInferenceAddress(document) {
  const { wire } = loadSource().consequence;
  const forbidden = new Set(wire.forbidden_inference_fields.map((name) => words(name).join("")));
  const results = [];
  const reported = new Set();
  function report(message, at, origin) {
    const key = JSON.stringify([at, message]);
    if (reported.has(key)) return;
    reported.add(key);
    results.push({
      message: `${origin}: ${message}; use owner-bound destinationId/custom_model_id and the CustomDestinations enrollment group, never a per-request address`,
      path: at,
    });
  }
  function address(name) {
    const parts = words(name);
    return forbidden.has(parts.join("")) || parts.some((part) => ADDRESS_WORDS.has(part)) ||
      /^(?:base|api|endpoint)(?:url|uri|address|host)$/.test(parts.join(""));
  }
  function reference(value, at, origin, seen, visitor) {
    if (typeof value.$ref !== "string") return;
    const ref = value.$ref;
    if (!ref.startsWith("#/")) {
      report("request schemas must use inspectable local $refs (external/dynamic request shapes cannot prove closure)", [...at, "$ref"], origin);
      return;
    }
    if (seen.has(ref)) return;
    seen.add(ref);
    let parts;
    try {
      parts = decodeURIComponent(ref.slice(2)).split("/").map((part) => part.replace(/~1/g, "/").replace(/~0/g, "~"));
    } catch (error) {
      if (!(error instanceof URIError)) throw error;
      report("malformed request $ref", [...at, "$ref"], origin);
      return;
    }
    const target = parts.reduce((node, part) => node && typeof node === "object" ? node[part] : undefined, document);
    if (target === undefined) report("unresolved request $ref cannot prove closure", [...at, "$ref"], origin);
    else visitor(target, parts, origin, seen);
  }
  const enrollmentAddresses = new Map();
  for (const field of wire.registration_fields.filter(address)) {
    const origin = `registration field ${field}`;
    const rootAt = ["components", "schemas", "CustomDestinationRegistrationRequest", "properties", field];
    const root = document.components?.schemas?.CustomDestinationRegistrationRequest?.properties?.[field];
    if (!root || typeof root !== "object" || Array.isArray(root)) continue;
    const key = (at) => JSON.stringify(at.map(String));
    enrollmentAddresses.set(key(rootAt), field);
    const candidates = new Map();
    let work = 0;
    function unproved(reason, at) {
      report(`registration address provenance is unproved: ${reason}`, at, origin);
      return undefined;
    }
    function conjuncts(value, at, active = new Set(), depth = 0) {
      if (++work > 16384 || depth > 64) return unproved("local reference/conjunction traversal bound", at);
      if (!value || typeof value !== "object" || Array.isArray(value) || active.has(key(at))) {
        return unproved("malformed or cyclic scalar alias", at);
      }
      const next = new Set(active).add(key(at));
      const facts = { type: undefined, min: 0, max: Infinity, patterns: new Map(), formats: new Set(), finite: undefined };
      const intersect = (values) => {
        facts.finite = facts.finite === undefined ? values : facts.finite.filter((item) => values.includes(item));
      };
      const merge = (child) => {
        if (child.type) facts.type = child.type;
        facts.min = Math.max(facts.min, child.min);
        facts.max = Math.min(facts.max, child.max);
        for (const [pattern, expression] of child.patterns) facts.patterns.set(pattern, expression);
        for (const format of child.formats) facts.formats.add(format);
        if (child.finite !== undefined) intersect(child.finite);
      };
      for (const [name, child] of Object.entries(value)) {
        if (SCHEMA_ANNOTATIONS.has(name) || name === "summary" || name === "$ref" || name === "allOf") continue;
        if (name === "type" && child === "string") facts.type = child;
        else if (["minLength", "maxLength"].includes(name) && Number.isSafeInteger(child) && child >= 0 && child <= 2147483647) {
          facts[name === "minLength" ? "min" : "max"] = child;
        } else if (name === "pattern" && typeof child === "string" && child.length <= 4096) {
          try { facts.patterns.set(child, new RegExp(child, "u")); }
          catch (error) {
            if (!(error instanceof SyntaxError)) throw error;
            return unproved("malformed scalar pattern", [...at, name]);
          }
        } else if (name === "format" && typeof child === "string" && child.length > 0) facts.formats.add(child);
        else if (name === "const" && typeof child === "string") intersect([child]);
        else if (name === "enum" && Array.isArray(child) && child.length > 0 && child.length <= 256 &&
            child.every((item) => typeof item === "string") && new Set(child).size === child.length) intersect(child);
        else return unproved(`unsupported scalar alias keyword or value ${JSON.stringify(name)}`, [...at, name]);
      }
      if (Object.hasOwn(value, "$ref")) {
        let target;
        reference(value, at, origin, new Set(), (resolved, targetAt) => {
          target = conjuncts(resolved, targetAt, next, depth + 1);
          if (target) candidates.set(key(targetAt), { facts: target, at: targetAt });
        });
        if (!target) return unproved("reference cannot establish a scalar alias", [...at, "$ref"]);
        merge(target);
      }
      if (Object.hasOwn(value, "allOf")) {
        if (!Array.isArray(value.allOf) || !value.allOf.length || value.allOf.length > 256) {
          return unproved("malformed or unbounded scalar conjunction", [...at, "allOf"]);
        }
        for (const [index, branch] of value.allOf.entries()) {
          const child = conjuncts(branch, [...at, "allOf", index], next, depth + 1);
          if (!child) return undefined;
          merge(child);
        }
      }
      if (facts.min > facts.max || facts.finite?.length === 0) return unproved("contradictory scalar constraints", at);
      return facts;
    }
    function accepts(facts, text) {
      return facts.type === "string" && [...text].length >= facts.min && [...text].length <= facts.max &&
        (facts.finite === undefined || facts.finite.includes(text)) &&
        [...facts.patterns.values()].every((pattern) => pattern.test(text));
    }
    function implies(target, role) {
      if (target.type !== "string" || role.type !== "string" ||
          [...role.formats].some((format) => !target.formats.has(format))) return false;
      return (role.finite === undefined || target.finite !== undefined && target.finite.every((text) => role.finite.includes(text))) &&
        target.min >= role.min && target.max <= role.max &&
        [...role.patterns.keys()].every((pattern) => target.patterns.has(pattern));
    }
    if (document.openapi !== "3.1.0" || ![
      undefined, "https://spec.openapis.org/oas/3.1/dialect/base", "https://json-schema.org/draft/2020-12/schema",
    ].includes(document.jsonSchemaDialect)) {
      unproved("unsupported OpenAPI schema dialect", rootAt);
      continue;
    }
    const role = conjuncts(root, rootAt);
    if (!role) continue;
    for (const [targetKey, { facts: target, at }] of candidates) {
      if (implies(target, role)) {
        enrollmentAddresses.set(targetKey, field);
        continue;
      }
      // A concrete counterexample proves proper narrowing, not regex-language equivalence.
      // Bound the witness input to empty/single-character strings; an unproved case stays red.
      const witnesses = target.finite ?? ["", ...Array.from({ length: 128 }, (_, code) => String.fromCharCode(code))];
      const narrowed = !target.formats.size && !role.formats.size && witnesses.some((text) =>
        text.length <= 1 && accepts(target, text) && !accepts(role, text));
      if (!narrowed) unproved("neither an equivalent alias nor a proved narrower use of its shared base", at);
    }
  }
  function schema(value, at, origin, seen, structuredArguments = false, usageAt = at) {
    if (value === false) return;
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      report("request schema must be explicitly typed and closed", at, origin);
      return;
    }
    const enrollmentField = enrollmentAddresses.get(JSON.stringify(at.map(String)));
    if (enrollmentField) {
      report(`registration-only ${enrollmentField} address schema is forbidden in an inference/tool request`, usageAt, origin);
    }
    reference(value, at, origin, seen, (target, targetAt, targetOrigin, targetSeen) =>
      schema(target, targetAt, targetOrigin, targetSeen, structuredArguments, [...at, "$ref"]));
    if (ADDRESS_FORMATS.has(value.format)) {
      report(`address-bearing format ${JSON.stringify(value.format)} is forbidden in an inference/tool request`, [...at, "format"], origin);
    }
    if (value.$dynamicRef || value.$recursiveRef) {
      report("dynamic request references cannot prove a closed request shape", at, origin);
    }
    if (Object.hasOwn(value, "not") && !scalarExclusion(value.not)) {
      report("negative (not) request schema composition beyond plain scalar exclusions is unsupported: address-free fields cannot be proved", [...at, "not"], origin);
    }
    const object = value.type === "object" || (Array.isArray(value.type) && value.type.includes("object")) || value.properties !== undefined ||
      value.additionalProperties !== undefined || value.patternProperties !== undefined;
    if (object && (value.additionalProperties !== false || value.patternProperties !== undefined ||
        value.unevaluatedProperties === true || typeof value.unevaluatedProperties === "object")) {
      report("every request object, including nested tool arguments, requires additionalProperties: false and no dynamic property map", at, origin);
    }
    if (!value.type && !value.$ref && !["allOf", "anyOf", "oneOf", "const", "enum"].some((key) => key in value)) {
      report("untyped request schema permits arbitrary address-bearing objects", at, origin);
    }
    if (structuredArguments && (value.type === "string" || (Array.isArray(value.type) && value.type.includes("string")))) {
      report("tool arguments must be typed closed fields, not a string carrying encoded request JSON", at, origin);
    }
    if ((value.type === "array" || (Array.isArray(value.type) && value.type.includes("array"))) &&
        value.items !== false && (!value.items || typeof value.items !== "object")) {
      report("request arrays require typed items (or items: false after a closed tuple)", at, origin);
    }
    for (const key of ["const", "enum"]) {
      const literals = key === "enum" && Array.isArray(value.enum) ? value.enum : [value[key]];
      if (literals.some((literal) => literal !== null && typeof literal === "object")) {
        report("object/array-valued const or enum cannot replace typed closed request fields", [...at, key], origin);
      }
    }
    if (value.contentEncoding || value.contentMediaType) {
      report("encoded/embedded request payloads cannot hide tool fields; publish typed closed fields instead", at, origin);
    }
    for (const [name, child] of Object.entries(value.properties || {})) {
      if (address(name) || /%[0-9a-f]{2}/i.test(name)) {
        report(`destination address, header, credential or encoded field ${JSON.stringify(name).slice(0, 100)} is forbidden in an inference/tool request`, [...at, "properties", name], origin);
      }
      schema(child, [...at, "properties", name], origin, new Set(seen),
        ["arguments", "toolarguments", "toolargs"].includes(words(name).join("")));
    }
    for (const key of ["items", "contains", "additionalProperties", "unevaluatedProperties", "if", "then", "else"]) {
      if (value[key] && typeof value[key] === "object") schema(value[key], [...at, key], origin, new Set(seen));
    }
    for (const key of ["allOf", "anyOf", "oneOf", "prefixItems"]) {
      if (Array.isArray(value[key])) value[key].forEach((child, index) =>
        schema(child, [...at, key, index], origin, new Set(seen), structuredArguments));
    }
    for (const key of ["dependentSchemas", "patternProperties"]) {
      for (const [name, child] of Object.entries(value[key] || {})) schema(child, [...at, key, name], origin, new Set(seen));
    }
  }
  function request(value, at, origin, seen) {
    if (!value || typeof value !== "object") return;
    reference(value, at, origin, seen, request);
    if (typeof value.name === "string" && (address(value.name) || /%[0-9a-f]{2}/i.test(value.name))) {
      report("destination addresses and custom headers are forbidden in request parameters", [...at, "name"], origin);
    }
    if (value.schema !== undefined) schema(value.schema, [...at, "schema"], origin, seen);
    for (const [media, body] of Object.entries(value.content || {})) {
      if (!body || typeof body !== "object" || body.schema === undefined) {
        report("every inference/tool request media type requires a typed closed schema", [...at, "content", media], origin);
        continue;
      }
      schema(body.schema, [...at, "content", media, "schema"], origin, new Set(seen));
      for (const [name, encoding] of Object.entries(body.encoding || {})) {
        if (encoding.headers && Object.keys(encoding.headers).length) {
          report("custom encoding headers are forbidden in inference/tool requests", [...at, "content", media, "encoding", name], origin);
        }
      }
    }
  }
  function pathItem(item, at, pathName, seen = new Set()) {
    if (!item || typeof item !== "object") return;
    reference(item, at, pathName, seen, (resolved, resolvedAt) => pathItem(resolved, resolvedAt, pathName, seen));
    for (const method of HTTP_METHODS) {
      const operation = item[method];
      if (!operation) continue;
      const names = `${pathName} ${words(operation.operationId || "").join(" ")} ${(operation.tags || []).join(" ")}`;
      if (!OPERATION_NAME.test(names) && operation["x-ai-request"] !== true) continue;
      const origin = `${method.toUpperCase()} ${pathName}`;
      if (!enrollmentBody(operation, pathName, method)) {
        request(operation.requestBody, [...at, method, "requestBody"], origin, new Set());
      }
      for (const [parameters, parametersAt] of [
        [item.parameters, [...at, "parameters"]],
        [operation.parameters, [...at, method, "parameters"]],
      ]) {
        (parameters || []).forEach((parameter, index) => request(parameter, [...parametersAt, index], origin, new Set()));
      }
    }
  }
  for (const [name, item] of Object.entries(document.paths || {})) pathItem(item, ["paths", name], name);
  for (const [name, value] of Object.entries(document.components?.schemas || {})) {
    if (REQUEST_NAME.test(name)) schema(value, ["components", "schemas", name], name, new Set());
  }
  for (const [name, value] of Object.entries(document.components?.requestBodies || {})) {
    if (REQUEST_NAME.test(name)) request(value, ["components", "requestBodies", name], name, new Set());
  }
  return results;
};
