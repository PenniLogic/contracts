"use strict";

function nonEmptyRequirement(security) {
  return Array.isArray(security) && security.length > 0 && security.some((item) => item && typeof item === "object" && Object.keys(item).length > 0);
}

// targetVal is an operation object. A requirement may come from the operation or from the root-level
// `security` (ADR-019 §16: the root DPoP scheme, owned by contracts#1). An explicit `security: []`
// on an operation is an anonymous operation and is refused here; the security scheme rule of the
// OAS ruleset checks that every named scheme exists.
module.exports = function operationSecurity(targetVal, _options, context) {
  if (!targetVal || typeof targetVal !== "object") return [];
  const [, pathKey, method] = context.path;
  const where = `${String(method).toUpperCase()} ${pathKey}`;
  if ("security" in targetVal) {
    if (nonEmptyRequirement(targetVal.security)) return [];
    return [{ message: `${where}: security must name at least one security scheme; anonymous operations are not published (ADR-019 §16)`, path: [...context.path, "security"] }];
  }
  if (nonEmptyRequirement(context.document.data.security)) return [];
  return [{ message: `${where}: no security requirement; declare operation-level security or a root-level security requirement (ADR-019 §16)`, path: context.path }];
};
