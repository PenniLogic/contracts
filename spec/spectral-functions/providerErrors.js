"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { HTTP_METHODS } = require("./_shared");

function load(context, name) {
  const source = context.document.source;
  if (typeof source !== "string") throw new Error("provider catalogue requires a document path");
  return JSON.parse(fs.readFileSync(path.join(path.dirname(source), name), "utf8"));
}

module.exports = function providerErrors(document, _options, context) {
  const catalogue = load(context, "error-catalogue.v1.json");
  const bindings = load(context, "client-state-bindings.v1.json");
  const schemas = document.components && document.components.schemas || {};
  const problems = [];
  const add = (message, location = ["components", "schemas"]) => problems.push({ message, path: location });
  for (const name of ["ValidationIssue", "Allowance", "EntitlementDenial", "ServiceProblemDetail", "AiRefusal"]) {
    if (schemas[name]?.["x-pennilogic-strict-provider"] !== true) {
      add("Every new closed error/content provider must select strict generated conversion and serialization", ["components", "schemas", name]);
    }
  }
  if (schemas.ProblemDetail?.["x-pennilogic-strict-provider"] !== undefined || schemas.Money?.["x-pennilogic-strict-provider"] !== undefined) {
    add("The accepted legacy scaffold keeps its existing DTO and money serializer bindings");
  }
  const codes = catalogue.codes;
  if (!Array.isArray(codes) || !codes.length) {
    add("error-catalogue.v1.json must publish a non-empty code catalogue");
    return problems;
  }
  if (catalogue.taxonomy_version !== bindings.taxonomy_version || bindings.taxonomy_version !== "1.1.0") {
    add("error catalogue must bind the accepted taxonomy 1.1.0 projection");
  }
  if (JSON.stringify(schemas.ClientState && schemas.ClientState.enum) !== JSON.stringify(bindings.states)) {
    add("ClientState must equal the accepted taxonomy identifiers in client-state-bindings.v1.json");
  }
  const vocabulary = codes.map((entry) => entry.code);
  if (new Set(vocabulary).size !== vocabulary.length ||
      JSON.stringify(vocabulary) !== JSON.stringify([...vocabulary].sort()) ||
      JSON.stringify(schemas.ProblemCode && schemas.ProblemCode.enum) !== JSON.stringify(vocabulary)) {
    add("ProblemCode must equal the unique, sorted error catalogue; adding an unclassified code fails");
  }
  const problem = schemas.ServiceProblemDetail;
  const required = ["type", "title", "status", "detail", "code", "correlation_id"];
  if (!problem || problem.additionalProperties !== false ||
      required.some((member) => !problem.required || !problem.required.includes(member))) {
    add("ServiceProblemDetail must be closed and require type/title/status/detail/code/correlation_id");
    return problems;
  }
  const properties = problem.properties || {};
  for (const [member, reference] of Object.entries({
    code: "ProblemCode", correlation_id: "PublicCorrelationId", field: "ProblemField",
    reason: "ValidationReason", instance: "PublicProblemInstance",
  })) {
    if (!properties[member] || properties[member].$ref !== `#/components/schemas/${reference}`) {
      add(`ServiceProblemDetail.${member} must reference ${reference}`);
    }
  }
  const allowed = new Set([...required, "instance", "field", "reason", "validation_errors",
    "idempotency_key", "retry_after_seconds", "allowance", "entitlement"]);
  if (Object.keys(properties).some((key) => !allowed.has(key))) {
    add("ServiceProblemDetail may carry only the published safe members; content and internal identifiers are forbidden");
  }
  const keySchema = document.components.parameters.IdempotencyKey.schema;
  if (!schemas.IdempotencyKeyValue || schemas.IdempotencyKeyValue.type !== keySchema.type ||
      schemas.IdempotencyKeyValue.pattern !== keySchema.pattern ||
      properties.idempotency_key.$ref !== "#/components/schemas/IdempotencyKeyValue") {
    add("The presented key value must bind to the unchanged ADR-015 header schema");
  }
  const retryClasses = new Set(schemas.RetryClass && schemas.RetryClass.enum || []);
  const treatments = new Set(schemas.IdempotencyTreatment && schemas.IdempotencyTreatment.enum || []);
  for (const entry of codes) {
    if (typeof entry.code !== "string" || !/^[a-z][a-z0-9_]{0,63}$/.test(entry.code) ||
        typeof entry.state !== "string" ||
        bindings.service_conditions[entry.condition] !== entry.state ||
        !bindings.states.includes(entry.state)) {
      add("Every error code must map to exactly one accepted service condition and its taxonomy state");
    }
    if (typeof entry.retryable !== "boolean" || !retryClasses.has(entry.retry_class) ||
        !treatments.has(entry.idempotency) || typeof entry.precondition !== "string" || !entry.precondition.trim()) {
      add("Every error code needs an explicit retry classification, key treatment and safe precondition");
    }
    if (entry.retryable && entry.idempotency !== "reuse_unchanged") {
      add("Retryable writes must retain the same persisted key and bytes (ADR-015)");
    }
    if (!Number.isInteger(entry.status) || entry.status < 400 || entry.status > 599 ||
        [entry.title, entry.detail].some((text) => typeof text !== "string" ||
          !/^[\x20-\x7e]+$/.test(text) || /[0-9$]/.test(text))) {
      add("Catalogue status and diagnostic text must be safe static literals, never amounts or provider input");
    }
    const branches = (problem.allOf || []).filter((branch) =>
      branch.if && branch.if.properties && branch.if.properties.code &&
      branch.if.properties.code.const === entry.code && branch.then && branch.then.properties &&
      branch.then.properties.type);
    const expected = {
      type: `urn:pennilogic:problem:${entry.code}`, title: entry.title,
      status: entry.status, detail: entry.detail,
    };
    if (branches.length !== 1 || Object.entries(expected).some(([member, value]) => {
      const binding = branches[0].then.properties[member];
      if (member === "status" && entry.status_by_field) {
        return !binding || JSON.stringify(binding.enum) !== JSON.stringify([value, ...Object.values(entry.status_by_field)].sort());
      }
      return !binding || binding.const !== value;
    })) {
      add("Every ServiceProblemDetail code must bind exactly its catalogue type/title/status/detail constants");
    }
  }
  const mismatch = codes.find((entry) => entry.code === "idempotency_payload_mismatch");
  if (!mismatch || mismatch.retryable !== false ||
      mismatch.idempotency !== "never_replace_to_escape_mismatch" || mismatch.state !== "error") {
    add("ADR-015 mismatch must fail without retry or key substitution and bind to request_failed/error");
  }
  const validation = codes.find((entry) => entry.code === "validation_rejected");
  if (!validation || validation.idempotency !== "new_after_body_edit_reuse_for_override" ||
      JSON.stringify(validation.status_by_field) !== '{"duplicate_override":400}') {
    add("ADR-015 unknown/foreign override is 400 validation_rejected and header-only correction retains the same key/bytes");
  }
  if (vocabulary.includes("ai_refusal") || vocabulary.includes("duplicate_suspected")) {
    add("Successful AI refusal and dedup decisions are content outcomes, never ProblemCode values");
  }
  if (!schemas.AiRefusal || schemas.AiRefusal.additionalProperties !== false ||
      JSON.stringify(schemas.AiRefusalCode && schemas.AiRefusalCode.enum) !== '["ai_refusal"]' ||
      schemas.AiRefusal.properties.code.$ref !== "#/components/schemas/AiRefusalCode" ||
      schemas.AiRefusal.properties.message.$ref !== "#/components/schemas/AiRefusalMessage" ||
      JSON.stringify(schemas.AiRefusalMessage && schemas.AiRefusalMessage.enum) !==
        JSON.stringify([catalogue.content_outcomes[0].message])) {
    add("AiRefusal must retain its separate successful code and fixed safe message");
  }
  for (const [route, item] of Object.entries(document.paths || {})) {
    for (const method of HTTP_METHODS) {
      const operation = item[method];
      if (!operation) continue;
      for (const [status, response] of Object.entries(operation.responses || {})) {
        if (!/^[45][0-9]{2}$/.test(status)) continue;
        const body = response.content && response.content["application/problem+json"];
        if (response.$ref !== "#/components/responses/ServiceProblem" &&
            (!body || !body.schema || body.schema.$ref !== "#/components/schemas/ServiceProblemDetail")) {
          add("Service error responses must reference ServiceProblemDetail, not inline or permissive problems",
            ["paths", route, method, "responses", status]);
        }
      }
    }
  }
  return problems;
};
