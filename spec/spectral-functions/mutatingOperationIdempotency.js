"use strict";
const { EXEMPTIONS, referencesMoney } = require("./_shared");

const KEY_REF = "#/components/parameters/IdempotencyKey";

function referencesKey(parameters) {
  return Array.isArray(parameters) && parameters.some((p) => p && typeof p === "object" && p.$ref === KEY_REF);
}

// targetVal is a POST, PUT, PATCH or DELETE operation object; context.path ends with [path, method].
module.exports = function mutatingOperationIdempotency(targetVal, _options, context) {
  if (!targetVal || typeof targetVal !== "object") return [];
  const [, pathKey, method] = context.path;
  const pathItem = context.document.data.paths[pathKey] || {};
  const where = `${String(method).toUpperCase()} ${pathKey}`;
  const declared = referencesKey(targetVal.parameters) || referencesKey(pathItem.parameters);
  const exemption = targetVal["x-idempotency"];
  const results = [];
  if (exemption !== undefined) {
    if (!EXEMPTIONS.has(exemption)) {
      results.push({
        message: `${where}: x-idempotency must be one of ${[...EXEMPTIONS].join(", ")} (ADR-015 §4.1)`,
        path: [...context.path, "x-idempotency"],
      });
    }
    // Money may hide behind any chain of $refs (requestBody -> Entry -> Money); the walk follows them.
    const document = context.document.data;
    if (referencesMoney(targetVal, document) || referencesMoney(pathItem.parameters || [], document)) {
      results.push({
        message: `${where}: an operation whose request or response carries Money (directly or through referenced schemas) can never be exempt from the Idempotency-Key requirement (ADR-015 §4.1)`,
        path: [...context.path, "x-idempotency"],
      });
    }
    if (declared) {
      results.push({
        message: `${where}: declares the IdempotencyKey parameter and an x-idempotency exemption; keep exactly one`,
        path: [...context.path, "x-idempotency"],
      });
    }
    return results;
  }
  if (!declared) {
    results.push({
      message: `${where}: every state-changing operation references ${KEY_REF} (required header) or declares a reasoned x-idempotency exemption (ADR-015 §4.1)`,
      path: context.path,
    });
  }
  return results;
};
