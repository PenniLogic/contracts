"use strict";
const { SAFE_METHODS } = require("./_shared");

const KEY_REF = "#/components/parameters/IdempotencyKey";

function keyIndexes(parameters) {
  if (!Array.isArray(parameters)) return [];
  return parameters.map((p, i) => (p && typeof p === "object" && p.$ref === KEY_REF ? i : -1)).filter((i) => i >= 0);
}

// targetVal is a path item; the key is refused on safe operations and on a path-level parameter
// list shared with a safe operation (ADR-015 §4.1: the header is ignored on safe methods).
module.exports = function safeMethodNoIdempotencyKey(targetVal, _options, context) {
  if (!targetVal || typeof targetVal !== "object") return [];
  const pathKey = context.path[context.path.length - 1];
  const results = [];
  const safeHere = Object.keys(targetVal).filter((m) => SAFE_METHODS.has(m));
  for (const method of safeHere) {
    for (const index of keyIndexes(targetVal[method] && targetVal[method].parameters)) {
      results.push({
        message: `${method.toUpperCase()} ${pathKey}: the Idempotency-Key header is ignored on safe methods and must not be declared (ADR-015 §4.1)`,
        path: [...context.path, method, "parameters", index],
      });
    }
  }
  if (safeHere.length > 0) {
    for (const index of keyIndexes(targetVal.parameters)) {
      results.push({
        message: `${pathKey}: path-level Idempotency-Key parameter also applies to ${safeHere.map((m) => m.toUpperCase()).join(", ")}; declare it on the mutating operations instead (ADR-015 §4.1)`,
        path: [...context.path, "parameters", index],
      });
    }
  }
  return results;
};
