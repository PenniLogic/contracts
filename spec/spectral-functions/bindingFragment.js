"use strict";
// Deep-compares a component against the binding fragment of an ADR-015 shape.
// Descriptive keys (description, example(s), summary, title, externalDocs) may be added to a schema
// object at any level; every other key of the fragment must be present with an identical value and
// no other binding key may be added. The keys of a `properties` (or `patternProperties`) mapping are
// property NAMES, never descriptive keys: `Money.properties.title` is an additional member and fails.
const DESCRIPTIVE = new Set(["description", "examples", "example", "summary", "title", "externalDocs"]);
const NAME_MAPS = new Set(["properties", "patternProperties"]);

function stripSchema(value) {
  if (Array.isArray(value)) return value.map(stripSchema);
  if (value && typeof value === "object") {
    const out = {};
    for (const key of Object.keys(value).sort()) {
      if (DESCRIPTIVE.has(key)) continue;
      out[key] = NAME_MAPS.has(key) ? stripNameMap(value[key]) : stripSchema(value[key]);
    }
    return out;
  }
  return value;
}

function stripNameMap(map) {
  if (!map || typeof map !== "object" || Array.isArray(map)) return map;
  const out = {};
  for (const name of Object.keys(map).sort()) {
    out[name] = stripSchema(map[name]);
  }
  return out;
}

function diff(expected, actual, path, out) {
  if (Array.isArray(expected) || Array.isArray(actual)) {
    if (JSON.stringify(expected) !== JSON.stringify(actual)) {
      out.push(`${path} must be ${JSON.stringify(expected)}`);
    }
    return;
  }
  if (expected && typeof expected === "object") {
    if (!actual || typeof actual !== "object") {
      out.push(`${path} must be an object with keys ${Object.keys(expected).join(", ")}`);
      return;
    }
    for (const key of Object.keys(expected)) {
      if (!(key in actual)) out.push(`${path}.${key} is missing (must be ${JSON.stringify(expected[key])})`);
      else diff(expected[key], actual[key], `${path}.${key}`, out);
    }
    for (const key of Object.keys(actual)) {
      if (!(key in expected)) out.push(`${path}.${key} is not part of the binding fragment; remove it or supersede ADR-015`);
    }
    return;
  }
  if (expected !== actual) out.push(`${path} must be ${JSON.stringify(expected)}`);
}

module.exports = function bindingFragment(targetVal, options, context) {
  const problems = [];
  diff(stripSchema(options.fragment), stripSchema(targetVal), options.name, problems);
  return problems.map((message) => ({
    message: `${message} (ADR-015 binding shape; see docs/development.md#lint)`,
    path: context.path,
  }));
};
