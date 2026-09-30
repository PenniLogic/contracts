"use strict";
const { MONEY_REF, isMoneyRef, isMoneyName } = require("./_shared");

// targetVal is a `properties` object. The Money component's own members (amount, currency) are the
// one place where the raw wire shape is spelled out; everywhere else a money-named property must be
// exactly `$ref: '#/components/schemas/Money'` unless it declares `x-not-money: <reason>`.
module.exports = function moneyBearingProperties(targetVal, _options, context) {
  if (!targetVal || typeof targetVal !== "object") return [];
  const owner = context.path.slice(0, -1).join("/");
  if (owner === "components/schemas/Money") return [];
  const results = [];
  for (const [name, schema] of Object.entries(targetVal)) {
    if (!isMoneyName(name)) continue;
    if (schema && typeof schema === "object" && typeof schema["x-not-money"] === "string" && schema["x-not-money"].trim().length > 0) continue;
    if (isMoneyRef(schema)) continue;
    results.push({
      message: `property '${name}' looks like money; reference ${MONEY_REF} (integer minor units with currency travel as the Money object, never as a string or JSON number) or declare x-not-money with a reason (ADR-015 §1.1)`,
      path: [...context.path, name],
    });
  }
  return results;
};
