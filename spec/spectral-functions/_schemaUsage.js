"use strict";

const { HTTP_METHODS, resolveLocalRef } = require("./_shared");

const SCHEMA_MAPS = ["properties", "patternProperties", "dependentSchemas", "$defs"];
const SCHEMA_ARRAYS = ["allOf", "anyOf", "oneOf", "prefixItems"];
const SCHEMA_SINGLES = ["items", "additionalProperties", "unevaluatedProperties", "unevaluatedItems",
  "contains", "propertyNames", "not", "if", "then", "else", "contentSchema"];
const SCHEMA_VALUES = new Set(["$ref", "$schema", "type", "enum", "const", "format", "pattern",
  "minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
  "required", "dependentRequired", "minProperties", "maxProperties", "minItems", "maxItems", "uniqueItems",
  "minContains", "maxContains", "title", "description", "default", "example", "examples", "deprecated",
  "readOnly", "writeOnly", "externalDocs", "xml", "$comment", "discriminator",
  ...SCHEMA_MAPS, ...SCHEMA_ARRAYS, ...SCHEMA_SINGLES]);
const DIALECTS = new Set(["https://spec.openapis.org/oas/3.1/dialect/base",
  "https://json-schema.org/draft/2020-12/schema"]);
const REFERENCE_CONTAINERS = {
  parameter: "parameters", header: "headers", requestBody: "requestBodies",
  response: "responses", callback: "callbacks", pathItem: "pathItems",
};
const CONTAINER_KEYS = {
  pathItem: new Set(["$ref", "summary", "description", "servers", "parameters", ...HTTP_METHODS]),
  requestBody: new Set(["$ref", "summary", "description", "required", "content"]),
  response: new Set(["$ref", "summary", "description", "headers", "content", "links"]),
  parameter: new Set(["$ref", "summary", "name", "in", "description", "required", "deprecated",
    "allowEmptyValue", "style", "explode", "allowReserved", "schema", "example", "examples", "content"]),
  header: new Set(["$ref", "summary", "description", "required", "deprecated", "allowEmptyValue",
    "style", "explode", "allowReserved", "schema", "example", "examples", "content"]),
};
const OPERATION_KEYS = new Set(["tags", "summary", "description", "externalDocs", "operationId",
  "parameters", "requestBody", "responses", "callbacks", "deprecated", "security", "servers"]);
const MAX_NODES = 16384;
const MAX_DEPTH = 64;

class SchemaUsageError extends Error {
  constructor(reason, path) {
    super(reason);
    this.path = path;
  }
}

// Schema-map keys are names, while annotation and literal payloads are data, not schema edges.
function schemaChildren(value, path) {
  const result = [];
  for (const key of SCHEMA_MAPS) {
    for (const [name, child] of Object.entries(value[key] || {})) {
      result.push({ value: child, path: [...path, key, name], declaration: key === "$defs" });
    }
  }
  for (const key of SCHEMA_ARRAYS) {
    if (Array.isArray(value[key])) {
      value[key].forEach((child, index) => result.push({ value: child, path: [...path, key, String(index)] }));
    }
  }
  for (const key of SCHEMA_SINGLES) {
    if (Object.hasOwn(value, key)) result.push({ value: value[key], path: [...path, key] });
  }
  return result;
}

function collectSchemaUsage(document, { visitSchema = () => {}, scope = () => "", allowSchemaCycles = false } = {}) {
  const names = { request: new Set(), response: new Set() };
  const work = { request: 0, response: 0 };
  const completed = new Map();
  const fail = (reason, path) => { throw new SchemaUsageError(reason, path); };
  function step(path, use, depth) {
    if (++work[use] > MAX_NODES || depth > MAX_DEPTH) fail("schema usage traversal bound", path);
  }
  function object(value, path) {
    if (!value || typeof value !== "object" || Array.isArray(value)) fail("malformed schema usage container", path);
    return value;
  }
  function tokens(reference, kind, path) {
    if (typeof reference !== "string" || !reference.startsWith("#/") ||
        /%|~(?![01])/.test(reference)) fail("unsupported schema usage reference", [...path, "$ref"]);
    const parts = reference.slice(2).split("/").map((token) => token.replace(/~1/g, "/").replace(/~0/g, "~"));
    if (kind === "schema") {
      if (parts.length < 3 || parts[0] !== "components" || parts[1] !== "schemas" || !parts[2]) {
        fail("schema usage reference must target a schema context", [...path, "$ref"]);
      }
      for (let index = 3; index < parts.length; index++) {
        const key = parts[index];
        if (SCHEMA_MAPS.includes(key)) {
          if (++index >= parts.length) fail("incomplete schema-map reference", [...path, "$ref"]);
        } else if (SCHEMA_ARRAYS.includes(key)) {
          if (++index >= parts.length || !/^(0|[1-9][0-9]*)$/.test(parts[index])) {
            fail("ambiguous schema-array reference", [...path, "$ref"]);
          }
        } else if (!SCHEMA_SINGLES.includes(key)) {
          fail("schema usage reference targets data, not a schema", [...path, "$ref"]);
        }
      }
    } else if (!(parts.length === 3 && parts[0] === "components" && parts[1] === REFERENCE_CONTAINERS[kind] && parts[2]) &&
        !(kind === "pathItem" && parts.length === 2 && ["paths", "webhooks"].includes(parts[0]) && parts[1])) {
      fail("schema usage reference has the wrong OpenAPI context", [...path, "$ref"]);
    }
    return parts;
  }
  function reference(value, kind, path, context, active, depth) {
    if (!Object.hasOwn(value, "$ref")) return 0;
    const parts = tokens(value.$ref, kind, path);
    let declared = document;
    for (const part of parts) {
      if (!declared || typeof declared !== "object" || !Object.hasOwn(declared, part)) {
        fail("unresolved schema usage reference", [...path, "$ref"]);
      }
      declared = declared[part];
    }
    const target = resolveLocalRef(value.$ref, document);
    if (target === undefined) fail("unresolved schema usage reference", [...path, "$ref"]);
    if (kind === "schema") names[context.use].add(parts[2]);
    const key = JSON.stringify([context.use, context.scope, kind, parts]);
    if (active.has(key)) {
      if (kind === "schema" && allowSchemaCycles) return 0;
      fail("cyclic schema usage reference", [...path, "$ref"]);
    }
    const cached = completed.get(key);
    if (cached !== undefined) {
      step(path, context.use, depth);
      if (depth + 1 + cached > MAX_DEPTH) fail("cached schema usage depth bound", path);
      return cached + 1;
    }
    const height = walk(target, kind, parts, context, new Set(active).add(key), depth + 1);
    completed.set(key, height);
    return height + 1;
  }
  function mapping(value, path, each, extensions = false) {
    if (value === undefined) return;
    object(value, path);
    for (const [name, child] of Object.entries(value)) {
      if (!extensions || !name.startsWith("x-")) each(child, [...path, name]);
    }
  }
  function schema(value, path, context, active, depth) {
    step(path, context.use, depth);
    if (typeof value === "boolean") return 0;
    object(value, path);
    for (const key of Object.keys(value)) {
      if (!SCHEMA_VALUES.has(key) && !key.startsWith("x-")) fail("unsupported schema usage keyword", [...path, key]);
    }
    if (value.$schema !== undefined && !DIALECTS.has(value.$schema)) fail("unsupported schema usage dialect", path);
    for (const key of ["readOnly", "writeOnly"]) {
      if (value[key] !== undefined && typeof value[key] !== "boolean") fail("malformed schema usage annotation", [...path, key]);
    }
    for (const key of SCHEMA_MAPS) {
      if (value[key] !== undefined) object(value[key], [...path, key]);
    }
    for (const key of SCHEMA_ARRAYS) {
      if (value[key] !== undefined && (!Array.isArray(value[key]) || !value[key].length)) {
        fail("malformed schema usage composition", [...path, key]);
      }
    }
    visitSchema(value, path, context);
    let height = reference(value, "schema", path, context, active, depth);
    for (const child of schemaChildren(value, path)) {
      if (!child.declaration) height = Math.max(height,
        1 + schema(child.value, child.path, context, active, depth + 1));
    }
    if (value.discriminator !== undefined) {
      const discriminator = object(value.discriminator, [...path, "discriminator"]);
      if (typeof discriminator.propertyName !== "string" ||
          Object.keys(discriminator).some((key) => !["propertyName", "mapping"].includes(key))) {
        fail("unsupported schema usage discriminator", [...path, "discriminator"]);
      }
      mapping(discriminator.mapping, [...path, "discriminator", "mapping"], (target, at) => {
        height = Math.max(height, reference({ $ref: target }, "schema", at, context, active, depth));
      });
    }
    return height;
  }
  function walk(value, kind, path, context, active = new Set(), depth = 0) {
    if (value === undefined) return 0;
    if (kind === "schema") return schema(value, path, context, active, depth);
    step(path, context.use, depth);
    object(value, path);
    if (CONTAINER_KEYS[kind] && Object.keys(value).some((key) =>
      !CONTAINER_KEYS[kind].has(key) && !key.startsWith("x-"))) {
      fail("unsupported OpenAPI usage container field", path);
    }
    if (value.$ref !== undefined && Object.keys(value).some((key) =>
      key !== "$ref" && key !== "summary" && key !== "description" && !key.startsWith("x-"))) {
      fail("ambiguous OpenAPI usage reference siblings", path);
    }
    let height = reference(value, kind, path, context, active, depth);
    const child = (member, childKind, at) => {
      if (member !== undefined) height = Math.max(height,
        1 + walk(member, childKind, at, context, active, depth + 1));
    };
    if (kind === "callback") {
      if (!Object.hasOwn(value, "$ref")) mapping(value, path, (item, at) => child(item, "pathItem", at), true);
    } else if (kind === "pathItem") {
      if (value.parameters !== undefined && !Array.isArray(value.parameters)) fail("malformed Path Item parameters", path);
      for (const method of HTTP_METHODS) {
        const operation = value[method];
        if (!operation) continue;
        object(operation, [...path, method]);
        if (Object.keys(operation).some((key) => !OPERATION_KEYS.has(key) && !key.startsWith("x-"))) {
          fail("unsupported operation usage field", [...path, method]);
        }
        const next = { ...context, operation, scope: scope(operation), path: [...path, method] };
        const operationAt = [...path, method];
        function used(member, useKind, at) {
          if (member === undefined) return;
          height = Math.max(height, 1 + walk(member, useKind, at, next, active, depth + 1));
        }
        if (context.use === "request") {
          used(operation.requestBody, "requestBody", [...operationAt, "requestBody"]);
          if (operation.parameters !== undefined && !Array.isArray(operation.parameters)) {
            fail("malformed operation parameters", operationAt);
          }
          for (const [parameters, at] of [[value.parameters, [...path, "parameters"]],
            [operation.parameters, [...operationAt, "parameters"]]]) {
            (parameters || []).forEach((parameter, index) => used(parameter, "parameter", [...at, index]));
          }
        } else {
          mapping(operation.responses, [...operationAt, "responses"], (response, at) => used(response, "response", at), true);
        }
        mapping(operation.callbacks, [...operationAt, "callbacks"], (callback, at) => used(callback, "callback", at));
      }
    } else {
      child(value.schema, "schema", [...path, "schema"]);
      mapping(value.content, [...path, "content"], (media, mediaAt) => {
        object(media, mediaAt);
        child(media.schema, "schema", [...mediaAt, "schema"]);
        mapping(media.encoding, [...mediaAt, "encoding"], (encoding, at) => {
          object(encoding, at);
          mapping(encoding.headers, [...at, "headers"], (header, headerAt) => child(header, "header", headerAt));
        });
      });
      if (kind === "response") mapping(value.headers, [...path, "headers"], (header, at) => child(header, "header", at));
    }
    return height;
  }
  if (!document || document.openapi !== "3.1.0" ||
      document.jsonSchemaDialect !== undefined && !DIALECTS.has(document.jsonSchemaDialect)) {
    fail("unsupported OpenAPI schema usage dialect", []);
  }
  for (const use of ["request", "response"]) {
    const context = { use, scope: "", operation: undefined };
    mapping(document.paths, ["paths"], (item, at) => walk(item, "pathItem", at, context), true);
    mapping(document.webhooks, ["webhooks"], (item, at) => walk(item, "pathItem", at, context), true);
  }
  return names;
}

module.exports = { schemaChildren, collectSchemaUsage, SchemaUsageError };
