"use strict";
const { AMOUNT_GRAMMAR, registry } = require("./_shared");

function looksLikeMoney(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value) && "amount" in value && "currency" in value;
}

function check(value, where, context) {
  if (typeof value.amount !== "string" || typeof value.currency !== "string") {
    return `${where}: Money example members must be JSON strings (number_not_string; ADR-015 §1.5)`;
  }
  if (!AMOUNT_GRAMMAR.test(value.amount)) {
    return `${where}: Money example amount violates the canonical grammar (ADR-015 §1.2)`;
  }
  const exponent = registry(context).get(value.currency);
  if (exponent === undefined) {
    return `${where}: Money example currency is not in currency-registry.v1.json (currency_unknown)`;
  }
  const fraction = value.amount.includes(".") ? value.amount.split(".")[1].length : 0;
  if (fraction !== exponent) {
    return `${where}: Money example has ${fraction} fraction digits; the registry exponent for ${value.currency} is ${exponent} (scale_mismatch; ADR-015 §1.2)`;
  }
  return null;
}

// targetVal is any example value; nested Money objects inside object examples are checked as well.
module.exports = function moneyExampleScale(targetVal, _options, context) {
  const results = [];
  const visit = (value, path) => {
    if (looksLikeMoney(value)) {
      const problem = check(value, "Money example", context);
      if (problem) results.push({ message: problem, path });
      return;
    }
    if (Array.isArray(value)) value.forEach((item, i) => visit(item, [...path, i]));
    else if (value && typeof value === "object") for (const [k, v] of Object.entries(value)) visit(v, [...path, k]);
  };
  visit(targetVal, context.path);
  return results;
};
