"use strict";

// targetVal is the value of a `type` or `format` key anywhere in the document (context.path names
// the key). A property that happens to be called `type` or `format` is an object and is skipped; its
// own `type` value is visited separately. JSON numbers never carry money or exact decimals
// (ADR-015 §1.6): `type: number` (also inside a 3.1 type array) and the floating-point formats are
// refused everywhere in the document.
module.exports = function noJsonNumber(targetVal, _options, context) {
  const key = context.path[context.path.length - 1];
  if (key === "type") {
    const types = Array.isArray(targetVal) ? targetVal : [targetVal];
    if (types.some((t) => t === "number")) {
      return [{
        message: "type: number is refused; money is the Money object and other exact decimals are decimal strings with a fixed per-field scale (ADR-015 §1.6)",
        path: context.path,
      }];
    }
    return [];
  }
  if (key === "format" && typeof targetVal === "string" && ["float", "double"].includes(targetVal.toLowerCase())) {
    return [{
      message: `format: ${targetVal} is refused; no floating-point value crosses the contract (ADR-015 §1, constitution)`,
      path: context.path,
    }];
  }
  return [];
};