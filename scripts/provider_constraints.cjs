"use strict";

const fs = require("node:fs");
const { Yaml } = require("@stoplight/spectral-parsers");
const { resolveLocalRef } = require("../spec/spectral-functions/_shared.js");

const ANNOTATIONS = new Set(["title", "description", "default", "example", "examples", "deprecated",
  "x-pennilogic-strict-provider", "x-pennilogic-provider-validator", "x-not-money"]);
const KEYS = new Set(["$ref", "type", "properties", "required", "additionalProperties", "items",
  "enum", "const", "pattern", "minLength", "maxLength", "minimum", "maximum",
  "exclusiveMinimum", "exclusiveMaximum", "minItems", "maxItems", "uniqueItems",
  "format", "allOf", "anyOf", "oneOf", "not", "if", "then", "else"]);
const TYPES = new Set(["object", "array", "string", "integer", "boolean"]);

function reject(reason) { throw new Error(`provider constraint generation rejected: ${reason}`); }
function object(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) reject("malformed schema");
  return value;
}
function scalar(value) {
  return typeof value === "string" || typeof value === "boolean" || Number.isSafeInteger(value);
}

function compile(document) {
  const schemas = object(document.components?.schemas);
  const compiled = {};
  const active = new Set();
  const roots = Object.keys(schemas).filter((name) => schemas[name]["x-pennilogic-strict-provider"] === true).sort();

  function component(name) {
    if (Object.hasOwn(compiled, name)) return;
    if (active.has(name)) reject("cyclic reference");
    if (!/^[A-Za-z][A-Za-z0-9_]*$/.test(name) || !Object.hasOwn(schemas, name)) reject("unresolved reference");
    active.add(name);
    compiled[name] = node(schemas[name]);
    active.delete(name);
  }

  function node(input, valueSchema = true) {
    const source = object(input), result = {};
    for (const key of Object.keys(source)) {
      if (!KEYS.has(key) && !ANNOTATIONS.has(key)) reject("unsupported keyword");
    }
    if (source.default !== undefined && source.default !== null) reject("unsupported default");
    if (valueSchema && source.type === undefined && source.$ref === undefined) reject("untyped value");
    if (source.$ref !== undefined) {
      if (typeof source.$ref !== "string" || !/^#\/components\/schemas\/[A-Za-z][A-Za-z0-9_]*$/.test(source.$ref) ||
          !resolveLocalRef(source.$ref, document)) reject("unresolved reference");
      const name = source.$ref.slice("#/components/schemas/".length);
      component(name);
      result.ref = name;
    }
    if (source.type !== undefined) {
      if (!TYPES.has(source.type)) reject("unsupported type");
      result.type = source.type;
    }
    if (source.format !== undefined) {
      if ((source.type === "string" && !["date", "date-time", "uri-reference"].includes(source.format)) ||
          (source.type === "integer" && !["int32", "int64"].includes(source.format)) ||
          !["string", "integer"].includes(source.type)) reject("unsupported format");
      result.format = source.format;
    }
    for (const key of ["minLength", "maxLength", "minItems", "maxItems"]) {
      if (source[key] !== undefined) {
        if (!Number.isSafeInteger(source[key]) || source[key] < 0 || source[key] > 2147483647) reject("malformed bound");
        result[key] = source[key];
      }
    }
    for (const [lower, upper] of [["minLength", "maxLength"], ["minItems", "maxItems"]]) {
      if (result[lower] !== undefined && result[upper] !== undefined && result[lower] > result[upper]) reject("contradictory bounds");
    }
    for (const key of ["minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"]) {
      if (source[key] !== undefined) {
        if (!Number.isSafeInteger(source[key])) reject("unsupported numeric bound");
        result[key] = source[key];
      }
    }
    if (result.minimum !== undefined && result.maximum !== undefined && result.minimum > result.maximum) reject("contradictory bounds");
    if ((result.exclusiveMinimum !== undefined && result.maximum !== undefined && result.exclusiveMinimum >= result.maximum) ||
        (result.minimum !== undefined && result.exclusiveMaximum !== undefined && result.minimum >= result.exclusiveMaximum) ||
        (result.exclusiveMinimum !== undefined && result.exclusiveMaximum !== undefined && result.exclusiveMinimum >= result.exclusiveMaximum)) reject("contradictory bounds");
    if (source.pattern !== undefined) {
      if (typeof source.pattern !== "string" || !source.pattern.startsWith("^") || !source.pattern.endsWith("$") ||
          /[^\x20-\x7e]/.test(source.pattern) || /\(\?/.test(source.pattern) ||
          (source.pattern.match(/\\./g) ?? []).some((escape) => !"\\.*+?()[]{}^$/-".includes(escape[1]))) reject("unsupported pattern");
      try { new RegExp(source.pattern); } catch { reject("malformed pattern"); }
      result.pattern = source.pattern;
    }
    if (source.enum !== undefined) {
      if (!Array.isArray(source.enum) || !source.enum.length || !source.enum.every(scalar) ||
          new Set(source.enum.map((value) => JSON.stringify(value))).size !== source.enum.length) reject("malformed enum");
      if (valueSchema && source.type !== "string") reject("unsupported enum representation");
      if (source.type === "string" && !source.enum.every((value) => typeof value === "string")) reject("ambiguous enum");
      result.enum = source.enum;
    }
    if (source.const !== undefined) {
      if (!scalar(source.const)) reject("unsupported constant");
      result.const = source.const;
    }
    if (source.required !== undefined) {
      if (!Array.isArray(source.required) || !source.required.every((key) => typeof key === "string" && key.length > 0) ||
          new Set(source.required).size !== source.required.length) reject("malformed required members");
      result.required = source.required;
    }
    if (source.properties !== undefined) {
      result.properties = Object.fromEntries(Object.entries(object(source.properties)).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)
        .map(([name, property]) => [name, node(property, valueSchema)]));
    }
    if (source.additionalProperties !== undefined) {
      if (typeof source.additionalProperties !== "boolean") reject("unsupported object map");
      result.additionalProperties = source.additionalProperties;
    }
    if (source.items !== undefined) result.items = node(source.items);
    if (source.type === "array" && source.items === undefined) reject("unbound array items");
    if (source.uniqueItems !== undefined) {
      if (typeof source.uniqueItems !== "boolean") reject("malformed uniqueness");
      result.uniqueItems = source.uniqueItems;
    }
    for (const key of ["allOf", "anyOf", "oneOf"]) {
      if (source[key] !== undefined) {
        if (!Array.isArray(source[key]) || !source[key].length) reject("malformed composition");
        result[key] = source[key].map((child) => node(child, false));
      }
    }
    for (const key of ["not", "if", "then", "else"]) if (source[key] !== undefined) result[key] = node(source[key], false);
    if ((source.then !== undefined || source.else !== undefined) && source.if === undefined) reject("unbound conditional");
    return result;
  }

  for (const name of roots) {
    if (schemas[name].type !== "object" || schemas[name].additionalProperties !== false) reject("open marked provider");
    component(name);
  }
  return { roots, schemas: Object.fromEntries(Object.entries(compiled).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)) };
}

module.exports = { compile };
if (require.main === module) {
  try {
    const parsed = Yaml.parse(fs.readFileSync(process.argv[2], "utf8"));
    if (parsed.diagnostics.length) reject("invalid source document");
    process.stdout.write(JSON.stringify(compile(parsed.data)));
  } catch (error) {
    process.stderr.write(error instanceof Error && error.message.startsWith("provider constraint generation rejected:")
      ? error.message + "\n" : "provider constraint generation rejected: invalid source document\n");
    process.exitCode = 1;
  }
}
