"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { HTTP_METHODS } = require("./_shared");
const DESCRIPTIVE = new Set(["description", "title", "summary", "example", "examples", "externalDocs", "deprecated",
  "x-pennilogic-strict-provider", "x-pennilogic-provider-validator"]);
const SHARED = ["ImportPreview", "ImportCommitRequest", "ImportCommitResult", "ImportColumnMapping", "ImportRowError", "ConfidenceBand", "DedupOutcome"];

function canonical(value, key = "") {
  if (Array.isArray(value)) {
    const items = value.map((item) => canonical(item));
    return key === "required" || key === "enum" ? items.sort() : items;
  }
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(Object.keys(value).sort().filter((name) => !DESCRIPTIVE.has(name))
    .map((name) => [name, canonical(value[name], name)]));
}

function walk(value, location, visitor) {
  if (!value || typeof value !== "object") return;
  visitor(value, location);
  for (const [name, member] of Object.entries(value)) walk(member, [...location, Array.isArray(value) ? Number(name) : name], visitor);
}

module.exports = function providerImports(document, _options, context) {
  const source = context.document.source;
  if (typeof source !== "string") throw new Error("import provider requires a document path");
  const policy = JSON.parse(fs.readFileSync(path.join(path.dirname(source), "import-group.v1.json"), "utf8"));
  const schemas = document.components && document.components.schemas || {};
  const problems = [];
  const add = (message, location = ["components", "schemas"]) => problems.push({ message, path: location });
  if (document["x-pennilogic-contract-metadata"].import_group_version !== policy.group_version ||
      JSON.stringify(schemas.ImportGroupVersion && schemas.ImportGroupVersion.enum) !== JSON.stringify([policy.group_version])) {
    add("Import group metadata and ImportGroupVersion must match import-group.v1.json");
  }
  if (JSON.stringify(schemas.ConfidenceBand && schemas.ConfidenceBand.enum) !== JSON.stringify(policy.confidence.vocabulary)) {
    add("ConfidenceBand must equal the closed shared high/low threshold-policy vocabulary");
  }
  if (schemas.ImportPreview?.properties?.rows?.maxItems !== policy.preview.maximum_rows ||
      schemas.ImportCommitResult?.properties?.rows?.maxItems !== policy.preview.maximum_rows ||
      schemas.ImportRowCount?.maximum !== policy.preview.maximum_rows ||
      schemas.ImportColumnIndex?.maximum !== policy.preview.maximum_columns ||
      schemas.DedupPrecedence?.properties?.user_confirmed_preserved?.const !== true) {
    add("Import row/column limits and preservation invariant must bind the canonical group policy");
  }
  const outcome = schemas.DedupOutcome;
  const override = document.components.parameters.DuplicateOverride;
  const matchedRef = `#/components/schemas/${policy.matched_identifier.schema}`;
  if (!outcome || outcome.properties?.matched_record_id?.$ref !== matchedRef ||
      !override || override.name !== "Duplicate-Override" || override.in !== "header" ||
      override.required !== false || !override.schema || override.schema.$ref !== matchedRef) {
    add("DuplicateOverride and DedupOutcome.matched_record_id must reference the same normative DedupRecordId");
  }
  const uuid = "[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}";
  for (const [name, prefix] of Object.entries({ ImportPreviewRef: "prv_", DedupRecordId: "rec_", DuplicateLinkId: "dln_", InstitutionPresetRef: "pre_" })) {
    const schema = schemas[name];
    if (!schema || schema.type !== "string" || schema.pattern !== `^${prefix}${uuid}$` ||
        schema.minLength !== 40 || schema.maxLength !== 40) add("Public import/dedup references require their distinct random UUIDv4 namespaces");
  }
  const requiredReferences = {
    ImportPreview: { preview_ref: "ImportPreviewRef", mapping: "ImportColumnMapping", counts: "ImportPreviewCounts" },
    ImportCommitRequest: { preview_ref: "ImportPreviewRef" },
    ImportCommitResult: { preview_ref: "ImportPreviewRef", counts: "ImportCommitCounts" },
    ImportPreviewRow: { confidence_band: "ConfidenceBand", dedup_outcome: "DedupOutcome", row_error: "ImportRowError" },
    ImportCommitRow: { created_record_id: "DedupRecordId", dedup_outcome: "DedupOutcome", row_error: "ImportRowError" },
    ImportRowError: { problem: "ServiceProblemDetail" },
    DedupOutcome: { confidence_band: "ConfidenceBand", link: "DuplicateLink", precedence: "DedupPrecedence" },
    DuplicateLink: { link_id: "DuplicateLinkId", suppressed_record_id: "DedupRecordId" },
  };
  for (const [name, fields] of Object.entries(requiredReferences)) {
    for (const [field, target] of Object.entries(fields)) {
      if (!schemas[name] || schemas[name].properties?.[field]?.$ref !== `#/components/schemas/${target}`) {
        add("Import/error/confidence/dedup members must reference their one shared provider, never local substitutes");
      }
    }
  }
  for (const name of ["ImportPreview", "ImportCommitRequest", "ImportCommitResult"]) {
    if (!schemas[name] || !schemas[name].required.includes("preview_ref") ||
        !schemas[name].required.includes("group_version")) add("Every preview/commit shape requires its immutable preview reference and group version");
  }
  const owned = new Set(policy.schemas);
  const banned = new Set(policy.privacy.absent_fields);
  for (const name of owned) {
    if (!schemas[name]) { add("Every published import-group component must exist"); continue; }
    if (schemas[name].type === "object" && schemas[name]["x-pennilogic-strict-provider"] !== true) {
      add("Every new closed import provider must select strict generated conversion and serialization", ["components", "schemas", name]);
    }
    walk(schemas[name], ["components", "schemas", name], (value, location) => {
      if (value.type === "object" && value.additionalProperties !== false) {
        add("Import group objects must be closed; arbitrary metadata is not a raw-content escape hatch", location);
      }
      if (value.properties && Object.keys(value.properties).some((field) => banned.has(field))) {
        add("Raw file/row/account content, internal IDs, fingerprints and blind indexes are absent from import components", location);
      }
      if (value.type === "string" && !value.enum && !value.pattern) {
        add("Import group strings are only typed vocabulary or bounded public references, never free raw text", location);
      }
    });
  }
  for (const [name, method] of Object.entries({
    DedupOutcome: "verifyDedup", ImportColumnMapping: "verifyMapping", ImportPreview: "verifyPreview",
    ImportCommitResult: "verifyCommitResult",
  })) {
    if (schemas[name]?.["x-pennilogic-provider-validator"] !== `com.pennilogic.contracts.imports.ImportContract.${method}`) {
      add("Import root serializers retain their published semantic verification, not only wire-kind checks", ["components", "schemas", name]);
    }
  }
  const fingerprints = new Map(SHARED.filter((name) => schemas[name]).map((name) => [JSON.stringify(canonical(schemas[name])), name]));
  walk(document, [], (value, location) => {
    if (location[0] === "components" && location[1] === "schemas" && owned.has(location[2])) return;
    if (value.$ref) return;
    const duplicate = fingerprints.get(JSON.stringify(canonical(value)));
    if (duplicate) add(`Reference ${duplicate}; an equivalent inline/local provider shape is forbidden`, location);
    if (!value.properties) return;
    for (const [member, target] of Object.entries({ confidence_band: "ConfidenceBand", dedup_outcome: "DedupOutcome" })) {
      if (value.properties[member] && value.properties[member].$ref !== `#/components/schemas/${target}`) {
        add("Locally declared confidence/dedup values must reference the shared component", [...location, "properties", member]);
      }
    }
    const fields = Object.keys(value.properties);
    if (fields.includes("preview_ref") && (fields.includes("rows") || fields.includes("counts")) &&
        !(location[0] === "components" && location[1] === "schemas" && owned.has(location[2]))) {
      add("Locally declared preview/commit shapes must reference the shared import provider", location);
    }
  });
  const windows = policy.source_pair_windows;
  const sources = policy.source_precedence;
  const pairs = new Set();
  for (const entry of windows) {
    const pair = [...entry.sources].sort().join("|");
    if (entry.sources.length !== 2 || entry.sources.some((name) => !sources.includes(name)) ||
        !["PT5M", "P1D", "P3D"].includes(entry.window) || pairs.has(pair)) add("Source-pair windows must be complete, symmetric, unique and typed");
    pairs.add(pair);
  }
  if (pairs.size !== sources.length * (sources.length + 1) / 2) add("Every source pair requires one published match window");
  for (const [route, item] of Object.entries(document.paths || {})) {
    for (const method of HTTP_METHODS) {
      const operation = item[method];
      if (!operation || operation["x-import-group"] === undefined) continue;
      if (operation["x-import-group"] !== policy.group_version ||
          !["preview", "commit"].includes(operation["x-import-operation"])) {
        add("Import consumer operations must declare the published group and separate preview/commit roles", ["paths", route, method]);
      }
      const expected = operation["x-import-operation"] === "commit" ? "ImportCommitResult" : "ImportPreview";
      const parameters = [...(item.parameters || []), ...(operation.parameters || [])];
      if (!parameters.some((parameter) => parameter.$ref === "#/components/parameters/IdempotencyKey")) {
        add("Both import preview and commit require the shared IdempotencyKey parameter", ["paths", route, method]);
      }
      const success = Object.entries(operation.responses || {}).filter(([status]) => /^2[0-9]{2}$/.test(status));
      if (!success.some(([, response]) => response.content && response.content["application/json"] &&
          response.content["application/json"].schema.$ref === `#/components/schemas/${expected}`)) {
        add("Import consumer responses must reference their shared preview/commit component", ["paths", route, method]);
      }
      if (operation["x-import-operation"] === "commit" &&
          !(operation.requestBody && operation.requestBody.content && operation.requestBody.content["application/json"] &&
            operation.requestBody.content["application/json"].schema.$ref === "#/components/schemas/ImportCommitRequest")) {
        add("Import commit consumers must reference ImportCommitRequest, never inline an upload or preview", ["paths", route, method]);
      }
    }
  }
  return problems;
};
