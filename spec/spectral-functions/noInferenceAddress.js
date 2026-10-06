"use strict";
const { HTTP_METHODS } = require("./_shared");
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
    function remember(value, at, origin, seen) {
      if (!value || typeof value !== "object" || Array.isArray(value)) return;
      enrollmentAddresses.set(JSON.stringify(at.map(String)), field);
      // A constrained reference is not an alias: its shared base can still be ordinary text.
      if (Object.keys(value).every((key) => ["$ref", "title", "summary", "description", "example", "examples", "default", "deprecated"].includes(key))) {
        reference(value, at, origin, seen, remember);
      }
    }
    remember(document.components?.schemas?.CustomDestinationRegistrationRequest?.properties?.[field],
      ["components", "schemas", "CustomDestinationRegistrationRequest", "properties", field],
      `registration field ${field}`, new Set());
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
