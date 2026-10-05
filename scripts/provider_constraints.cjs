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

function validatePattern(pattern) {
  if (typeof pattern !== "string" || pattern[0] !== "^" || pattern.at(-1) !== "$" ||
      /[^\x20-\x7e]/.test(pattern)) reject("unsupported pattern");
  let index = 1;
  let depth = 0;
  const end = pattern.length - 1;
  const peek = () => pattern[index];
  function escaped(inClass) {
    index += 1;
    const character = peek();
    if (index >= end || !(inClass ? "\\.^$*+?()[]{}|/-" : "\\.^$*+?()[]{}|/").includes(character)) {
      reject("unsupported pattern escape");
    }
    index += 1;
    return character;
  }
  function characterClass() {
    index += 1;
    if (peek() === "^") index += 1;
    const characters = [];
    while (index < end && peek() !== "]") {
      const character = peek();
      if (character === "[" || ["&&", "||", "~~", "--"].includes(pattern.slice(index, index + 2))) {
        reject("unsupported character class");
      }
      if (character === "\\") characters.push({ character: escaped(true), range: false });
      else { characters.push({ character, range: character === "-" }); index += 1; }
    }
    if (!characters.length || index >= end || peek() !== "]") reject("malformed character class");
    let previousRangeEnd = -1;
    for (let position = 1; position + 1 < characters.length; position += 1) {
      if (characters[position].range && (characters[position - 1].range || characters[position + 1].range ||
          position - 1 <= previousRangeEnd ||
          characters[position - 1].character.charCodeAt(0) > characters[position + 1].character.charCodeAt(0))) {
        reject("malformed character range");
      }
      if (characters[position].range) previousRangeEnd = position + 1;
    }
    index += 1;
  }
  function repetition() {
    if (["*", "+", "?"].includes(peek())) { index += 1; return; }
    if (peek() !== "{") return;
    const start = ++index;
    while (index < end && /[0-9,]/.test(peek())) index += 1;
    const match = /^(0|[1-9][0-9]*)(?:,(0|[1-9][0-9]*)?)?$/.exec(pattern.slice(start, index));
    if (!match || peek() !== "}") reject("malformed repetition");
    const lower = Number(match[1]), upper = match[2] === undefined ? lower : Number(match[2]);
    if (lower > 2147483647 || upper > 2147483647 || lower > upper) reject("unsupported repetition");
    index += 1;
  }
  function sequence(inGroup) {
    let count = 0;
    while (index < end && (!inGroup || (peek() !== ")" && peek() !== "|"))) {
      const character = peek();
      if (character === "\\") escaped(false);
      else if (character === "[") characterClass();
      else if (character === "(") {
        if (++depth > 64) reject("unsupported pattern depth");
        index += 1;
        if (!sequence(true)) reject("empty pattern group");
        while (peek() === "|") {
          index += 1;
          if (!sequence(true)) reject("empty pattern alternative");
        }
        if (peek() !== ")") reject("malformed pattern group");
        index += 1;
        depth -= 1;
      } else if ("^$|)*+?{}]".includes(character)) reject("unsupported pattern syntax");
      else index += 1;
      repetition();
      count += 1;
    }
    return count;
  }
  sequence(false);
  if (index !== end) reject("malformed pattern");
  try { new RegExp(pattern, "u"); } catch { reject("malformed pattern"); }
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
      validatePattern(source.pattern);
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
