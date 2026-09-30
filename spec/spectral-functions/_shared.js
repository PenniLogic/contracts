"use strict";
// Shared helpers for the ADR-015 lint functions: the currency registry and the money grammar.

const MONEY_REF = "#/components/schemas/Money";
const AMOUNT_GRAMMAR = /^(0(\.[0-9]+)?|-?[1-9][0-9]*(\.[0-9]+)?|-0\.[0-9]*[1-9][0-9]*)$/;
// A property name says money when one of its words is a money word and none of its words says the
// value is a count, rate, code or name (fee_rate, total_count, currency_code are not money).
const MONEY_WORDS = new Set(["amount", "amounts", "balance", "balances", "price", "prices", "fee", "fees", "total", "totals", "cost", "costs", "minor"]);
const NOT_MONEY_WORDS = new Set(["count", "counts", "percent", "percentage", "rate", "rates", "ratio", "bps", "exponent", "scale", "digits", "name", "names", "code", "codes", "id", "ids", "type", "kind", "currency", "unit", "units", "label", "at", "date", "time"]);

function words(name) {
  return String(name)
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter(Boolean);
}

function isMoneyName(name) {
  const parts = words(name);
  if (parts.length === 1 && parts[0] === "minor") return false;
  return parts.some((w) => MONEY_WORDS.has(w)) && !parts.some((w) => NOT_MONEY_WORDS.has(w));
}
const HTTP_METHODS = ["get", "put", "post", "delete", "options", "head", "patch", "trace"];
const SAFE_METHODS = new Set(["get", "head", "options", "trace"]);
const MUTATING_METHODS = new Set(["post", "put", "patch", "delete"]);
const EXEMPTIONS = new Set(["not-applicable", "per-item", "provider-event", "auth"]);

let registryCache = new Map();

function registry(context) {
  // The registry travels with the specification: it is read from the linted document's directory
  // (spec/currency-registry.v1.json next to spec/openapi.yaml) and is the single source of exponents.
  const fs = require("node:fs");
  const path = require("node:path");
  const source = context && context.document && context.document.source;
  if (typeof source !== "string") {
    throw new Error("currency registry lookup needs a document path; lint the file, not stdin");
  }
  const file = path.join(path.dirname(source), "currency-registry.v1.json");
  if (!registryCache.has(file)) {
    const parsed = JSON.parse(fs.readFileSync(file, "utf8"));
    registryCache.set(file, new Map(parsed.currencies.map((entry) => [entry.code, entry.exponent])));
  }
  return registryCache.get(file);
}

function isMoneyRef(value) {
  return Boolean(value) && typeof value === "object" && value.$ref === MONEY_REF && Object.keys(value).every((k) => k === "$ref" || k === "description");
}

function mentionsMoney(value) {
  return JSON.stringify(value).includes(`"${MONEY_REF}"`);
}

function pathString(pathParts) {
  return pathParts.map((part) => (typeof part === "number" ? `[${part}]` : `.${part}`)).join("").replace(/^\./, "");
}

module.exports = {
  MONEY_REF, AMOUNT_GRAMMAR, HTTP_METHODS, SAFE_METHODS, MUTATING_METHODS, EXEMPTIONS,
  registry, isMoneyRef, isMoneyName, mentionsMoney, pathString,
};
