"use strict";
const { referencesMoney, isMoneyName } = require("./_shared");

// targetVal is the ProblemDetail schema. A problem body names fields and reasons; it never carries
// an amount, a balance or any Money object (ADR-015 §1.5, §4.4, §5).
module.exports = function problemDetailNoMoney(targetVal, _options, context) {
  if (!targetVal || typeof targetVal !== "object") return [];
  const results = [];
  if (referencesMoney(targetVal, context.document.data)) {
    results.push({ message: "ProblemDetail must not reference Money; problem bodies never carry a monetary value (ADR-015 §4.4)", path: context.path });
  }
  const properties = targetVal.properties || {};
  for (const name of Object.keys(properties)) {
    if (isMoneyName(name)) {
      results.push({ message: `ProblemDetail.${name}: a problem body never carries a monetary value (ADR-015 §5)`, path: [...context.path, "properties", name] });
    }
  }
  return results;
};
