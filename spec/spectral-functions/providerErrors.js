"use strict";

const fs = require("node:fs");
const path = require("node:path");
const Ajv = require("ajv/dist/2020");
const addFormats = require("ajv-formats");
const { HTTP_METHODS, resolveLocalRef } = require("./_shared");

const FAMILIES = new Set(["ServiceProblemDetail", "EgressDeniedProblemDetail",
  "AuthenticationProblemDetail", "AuthenticationContextProblemDetail", "AuthenticationRequiredProblemDetail", "SessionRevokedProblemDetail",
  "AuthenticationChallengeProblemDetail", "ApplicationProblemDetail", "OperationProblemDetail"]);
const AUTH_STATUS = {authentication_required: 401, step_up_required: 403, session_revoked: 401,
  step_up_credential_too_new: 403, restricted_after_recovery: 403, recovery_notification_pending: 409,
  recovery_locked: 403, recovery_pending_elsewhere: 409};

function resolve(value, document, visited = new Set()) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  if (value.$ref === undefined) return value;
  if (visited.has(value.$ref)) return undefined;
  visited.add(value.$ref);
  return resolve(resolveLocalRef(value.$ref, document), document, visited);
}

function referencesStrictFamily(schema, document, visited = new Set()) {
  if (!schema || typeof schema !== "object") return false;
  if (schema.$ref !== undefined) {
    if (typeof schema.$ref !== "string") return false;
    if (FAMILIES.has(schema.$ref.slice("#/components/schemas/".length)) &&
        schema.$ref.startsWith("#/components/schemas/")) return true;
    if (visited.has(schema.$ref)) return false;
    visited.add(schema.$ref);
    if (referencesStrictFamily(resolveLocalRef(schema.$ref, document), document, visited)) return true;
  }
  if (schema.allOf?.some((branch) => referencesStrictFamily(branch, document, new Set(visited)))) return true;
  return ["anyOf", "oneOf"].some((keyword) => Array.isArray(schema[keyword]) && schema[keyword].length > 0 &&
    schema[keyword].every((branch) => referencesStrictFamily(branch, document, new Set(visited))));
}

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
  for (const name of ["ValidationIssue", "Allowance", "EntitlementDenial", "AiRefusal", ...FAMILIES]) {
    if (schemas[name]?.["x-pennilogic-strict-provider"] !== true) {
      add("Every new closed error/content provider must select strict generated conversion and serialization", ["components", "schemas", name]);
    }
  }
  if (schemas.ProblemDetail?.["x-pennilogic-strict-provider"] !== undefined || schemas.Money?.["x-pennilogic-strict-provider"] !== undefined) {
    add("The accepted legacy scaffold keeps its existing DTO and money serializer bindings");
  }
  const codes = catalogue.codes;
  const authentication = catalogue.authentication_codes;
  if (!Array.isArray(codes) || !codes.length || !Array.isArray(authentication) || !authentication.length) {
    add("error-catalogue.v1.json must publish service and separately classified authentication rows");
    return problems;
  }
  if (catalogue.group_version !== "1.2.0" || document["x-pennilogic-contract-metadata"]?.error_group_version !== catalogue.group_version) {
    add("The shared error source group must bind catalogue version 1.2.0");
  }
  if (catalogue.taxonomy_version !== bindings.taxonomy_version || bindings.taxonomy_version !== "1.1.0") {
    add("error catalogue must bind the accepted taxonomy 1.1.0 projection");
  }
  if (JSON.stringify(schemas.ClientState && schemas.ClientState.enum) !== JSON.stringify(bindings.states)) {
    add("ClientState must equal the accepted taxonomy identifiers in client-state-bindings.v1.json");
  }
  const vocabulary = [...codes, ...authentication].map((entry) => entry.code).sort();
  if (new Set(vocabulary).size !== vocabulary.length ||
      [codes, authentication].some((rows) => JSON.stringify(rows.map((entry) => entry.code)) !==
        JSON.stringify(rows.map((entry) => entry.code).sort())) ||
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
  if (JSON.stringify(properties.code?.enum) !== JSON.stringify(codes.map((entry) => entry.code)) ||
      properties.code?.type !== "string" || properties.code?.not !== undefined) {
    add("ServiceProblemDetail.code must intersect the one global ProblemCode with exactly the positive catalogue service subset");
  }
  for (const [member, reference] of Object.entries({
    code: "ProblemCode", correlation_id: "PublicCorrelationId", field: "ProblemField",
    reason: "ValidationReason", instance: "PublicProblemInstance",
    direction: "AllocationMismatchDirection",
  })) {
    if (!properties[member] || properties[member].$ref !== `#/components/schemas/${reference}`) {
      add(`ServiceProblemDetail.${member} must reference ${reference}`);
    }
  }
  const allowed = new Set([...required, "instance", "field", "reason", "direction", "validation_errors",
    "idempotency_key", "retry_after_seconds", "allowance", "entitlement"]);
  if (Object.keys(properties).some((key) => !allowed.has(key))) {
    add("ServiceProblemDetail may carry only the published safe members; content and internal identifiers are forbidden");
  }
  const directionValues = ["shortfall", "excess"];
  const directionBinding = catalogue.validation_reason_bindings?.find((entry) => entry.reason === "allocation_sum_mismatch");
  const directionBranch = (problem.allOf || []).find((branch) =>
    branch.if?.properties?.code?.const === "validation_rejected" &&
    branch.if?.properties?.reason?.const === "allocation_sum_mismatch");
  const issueBranch = (schemas.ValidationIssue?.allOf || []).find((branch) =>
    branch.if?.properties?.reason?.const === "allocation_sum_mismatch");
  if (JSON.stringify(schemas.AllocationMismatchDirection?.enum) !== JSON.stringify(directionValues) ||
      directionBinding?.code !== "validation_rejected" || directionBinding?.monetary_member !== false ||
      JSON.stringify(directionBinding?.required_members) !== '["field","direction"]' ||
      JSON.stringify(directionBinding?.direction_values) !== JSON.stringify(directionValues) ||
      schemas.ValidationIssue?.properties?.direction?.$ref !== "#/components/schemas/AllocationMismatchDirection" ||
      JSON.stringify(directionBranch?.then?.required) !== '["field","direction"]' ||
      JSON.stringify(directionBranch?.else?.not?.required) !== '["direction"]' ||
      JSON.stringify(issueBranch?.then?.required) !== '["direction"]' ||
      JSON.stringify(issueBranch?.else?.not?.required) !== '["direction"]') {
    add("ADR-016 allocation_sum_mismatch requires field and typed shortfall/excess direction, with no monetary member");
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
    const expected = {
      type: `urn:pennilogic:problem:${entry.code}`, title: entry.title,
      status: entry.status, detail: entry.detail,
    };
    for (const name of new Set(["ServiceProblemDetail", entry.schema || "ServiceProblemDetail"])) {
      const branches = (schemas[name]?.allOf || []).filter((branch) =>
        branch.if?.properties?.code?.const === entry.code && branch.then?.properties?.type);
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
  for (const entry of authentication) {
    if (entry.classification !== "authentication_owned" || entry.state !== null ||
        entry.flow !== "authentication_required" || !bindings.excluded_conditions.includes(entry.flow) ||
        entry.condition !== entry.code ||
        entry.status !== AUTH_STATUS[entry.code]) {
      add("Authentication codes require their exact owning status and excluded flow, with NONE/null service state");
    }
  }
  if (JSON.stringify(authentication.map((entry) => entry.code).sort()) !== JSON.stringify(Object.keys(AUTH_STATUS).sort())) {
    add("ADR-019 requires every named session, step-up and proven-recovery condition in the shared catalogue");
  }
  const authPolicies = catalogue.authentication_policies || {};
  if (JSON.stringify(Object.keys(authPolicies).sort()) !== JSON.stringify(Object.keys(AUTH_STATUS).sort())) {
    add("Every authentication code needs its separately typed retry/lifecycle/context policy");
  }
  for (const entry of authentication) {
    const policy = authPolicies[entry.code];
    if (!policy || typeof policy.retryable !== "boolean" || !retryClasses.has(policy.retry_class) ||
        policy.idempotency !== "authentication_lifecycle" || !treatments.has(policy.idempotency) ||
        !Array.isArray(policy.required_context) || !policy.precondition?.trim() ||
        (policy.retryable && entry.code !== "recovery_notification_pending")) {
      add("Auth retry is only same proven-flow delay; credential responses never use financial replay semantics");
    }
  }
  const union = schemas.OperationProblemDetail;
  const expectedUnion = ["ServiceProblemDetail", "EgressDeniedProblemDetail", "AuthenticationProblemDetail"]
    .map((name) => ({ $ref: `#/components/schemas/${name}` }));
  const stepUpBinding = { if: { properties: { code: { enum: ["egress_denied", "step_up_required"] } }, required: ["code"] },
    then: { required: ["egress_denial_reason"] } };
  if (JSON.stringify(union?.allOf) !== JSON.stringify([{ oneOf: expectedUnion }, stepUpBinding]) ||
      schemas.EgressDeniedProblemDetail?.properties?.egress_denial_reason?.$ref !== "#/components/schemas/EgressDenialReason" ||
      !schemas.EgressDeniedProblemDetail?.required?.includes("egress_denial_reason") ||
      schemas.AuthenticationProblemDetail?.properties?.egress_denial_reason?.$ref !== "#/components/schemas/EgressDenialReason" ||
      schemas.ValidationReason?.enum?.some((reason) => schemas.EgressDenialReason?.enum?.includes(reason))) {
    add("The closed shared transport union must separate service, typed egress and authentication without forking ValidationReason");
  }
  for (const name of FAMILIES) {
    if (schemas[name]?.type !== "object" || schemas[name]?.additionalProperties !== false ||
        required.some((member) => !schemas[name]?.required?.includes(member)) ||
        schemas[name]?.properties?.code?.$ref !== "#/components/schemas/ProblemCode") {
      add("Every shared error family must be closed, require the public problem members and reuse the one ProblemCode");
    }
  }
  const reasons = schemas.EgressDenialReason?.enum || [];
  const binding = catalogue.egress_binding;
  const reasonCodes = binding?.reason_codes || {};
  if (binding?.consequence !== "adr-022-ai-egress-consequences@1.1.0" ||
      binding?.policy_version !== "2026-10-05.2" ||
      binding?.service_schema !== "EgressDeniedProblemDetail" || binding?.authentication_schema !== "AuthenticationProblemDetail" ||
      binding?.member !== "egress_denial_reason" || binding?.schema !== "EgressDenialReason" ||
      JSON.stringify(Object.keys(reasonCodes).sort()) !== JSON.stringify([...reasons].sort()) ||
      reasons.some((reason) => !vocabulary.includes(reasonCodes[reason]))) {
    add("The owning catalogue must bind every exact canonical egress reason to one shared classified code");
  }
  const egress = codes.find((entry) => entry.code === "egress_denied");
  if (egress?.schema !== "EgressDeniedProblemDetail" || egress?.status !== 403 ||
      egress?.condition !== "request_failed" || egress?.state !== "error" || egress?.retryable !== false ||
      egress?.retry_class !== "never" || egress?.idempotency !== "reuse_unchanged") {
    add("egress_denied is the non-retryable 403 request_failed/error policy, never a new state or automatic fallback");
  }
  const ajv = new Ajv({ strict: false });
  addFormats(ajv);
  ajv.addSchema({ components: document.components }, "error-contract");
  const validators = {};
  try {
    for (const name of FAMILIES) validators[name] = ajv.getSchema(`error-contract#/components/schemas/${name}`);
  } catch (error) {
    if (!(error instanceof Error)) throw error;
    add("Shared error family validation failed: malformed or unresolved source schema");
    return problems;
  }
  const example = (entry) => ({
    type: `urn:pennilogic:problem:${entry.code}`, title: entry.title, status: entry.status,
    detail: entry.detail, code: entry.code, correlation_id: "cor_00000000-0000-4000-8000-000000000001",
  });
  function authExample(entry) {
    const contexts = {
      session_revoked: {revocation_reason: "signout"},
      step_up_credential_too_new: {matures_at: "2026-10-01T00:00:00.000Z"},
      restricted_after_recovery: {restricted_until: "2026-10-01T00:00:00.000Z", not_me_until: "2026-10-01T00:00:00.000Z"},
      recovery_notification_pending: {window_ends_at: null, retry_after_seconds: 30},
      recovery_locked: {locked_until: "2026-10-01T00:00:00.000Z"},
      recovery_pending_elsewhere: {pending_route: "recovery_code", window_ends_at: "2026-10-01T00:00:00.000Z", action: "cancel_and_restart"},
    };
    return {...example(entry), ...(contexts[entry.code] || {})};
  }
  if (egress) {
    const wire = example(egress);
    if (!validators.ServiceProblemDetail(wire) || validators.OperationProblemDetail(wire) ||
        validators.EgressDeniedProblemDetail(wire) || validators.AuthenticationProblemDetail(wire) ||
        validators.ServiceProblemDetail({ ...wire, status: 401 }) ||
        validators.ServiceProblemDetail({ ...wire, detail: "PRIVATE_SYNTHETIC_CANARY" })) {
      add("The positive service subset preserves safe egress_denied while the egress-aware union still requires its typed reason");
    }
  }
  for (const entry of authentication) {
    const wire = authExample(entry);
    const old = ["authentication_required", "step_up_required"].includes(entry.code);
    const family = old ? "AuthenticationProblemDetail" : "AuthenticationContextProblemDetail";
    if (!validators[family](wire) || !validators.ApplicationProblemDetail(wire) ||
        validators[old ? "AuthenticationContextProblemDetail" : "AuthenticationProblemDetail"](wire) ||
        (!old && validators.OperationProblemDetail(wire)) ||
        (old && validators.OperationProblemDetail(wire) !== (entry.code === "authentication_required")) ||
        validators.ServiceProblemDetail(wire) || validators.EgressDeniedProblemDetail(wire) ||
        validators.AuthenticationRequiredProblemDetail(wire) !== (entry.code === "authentication_required") ||
        validators.SessionRevokedProblemDetail(wire) !== (entry.code === "session_revoked") ||
        validators[family]({ ...wire, status: entry.status === 401 ? 403 : 401 }) ||
        validators[family]({ ...wire, detail: "PRIVATE_SYNTHETIC_CANARY" }) ||
        validators[family]({ ...wire, provider: "PRIVATE_SYNTHETIC_CANARY" })) {
      add("Authentication family must enforce exact catalogue constants, safe closure and 401-versus-step-up discrimination");
    }
    for (const name of authPolicies[entry.code]?.required_context || []) {
      const missing = {...wire};
      delete missing[name];
      if (validators[family](missing)) add("Authentication context members are required by the owning code, never silently omitted");
    }
  }
  for (const reason of reasons) {
    const entry = [...codes, ...authentication].find((row) => row.code === reasonCodes[reason]);
    if (!entry) continue;
    const wire = { ...example(entry), egress_denial_reason: reason };
    if (entry.code === "rate_limited") {
      wire.retry_after_seconds = 30;
      wire.allowance = { limit: 5, unit: "requests", window: "hour" };
    }
    const family = reason === "step_up_required" ? "AuthenticationProblemDetail" : "EgressDeniedProblemDetail";
    const other = reason === "step_up_required" ? "EgressDeniedProblemDetail" : "AuthenticationProblemDetail";
    if (!validators[family](wire) || !validators.OperationProblemDetail(wire) || validators[other](wire) ||
        validators.ServiceProblemDetail(wire) || validators[family]({ ...wire, detail: "PRIVATE_SYNTHETIC_CANARY" }) ||
        validators[family]({ ...wire, host: "PRIVATE_SYNTHETIC_CANARY" }) ||
        (family === "EgressDeniedProblemDetail" && validators[family](example(entry)))) {
      add(`Canonical ${reason} must select exactly its safe, closed ${family} and catalogue code/status`);
    }
    for (const candidate of [...codes, ...authentication]) {
      if (candidate.code !== entry.code &&
          validators[family]({ ...wire, ...example(candidate) })) {
        add(`Canonical ${reason} cannot be remapped to another global code/status`);
      }
    }
  }
  for (const [route, item] of Object.entries(document.paths || {})) {
    for (const method of HTTP_METHODS) {
      const operation = item[method];
      if (!operation) continue;
      for (const [status, response] of Object.entries(operation.responses || {})) {
        if (status !== "default" && !/^[45](?:[0-9]{2}|XX)$/.test(status)) continue;
        const location = ["paths", route, method, "responses", status];
        const resolved = resolve(response, document);
        const body = resolved?.content?.["application/problem+json"];
        if (!referencesStrictFamily(body?.schema, document) ||
            Object.keys(resolved?.content || {}).some((media) => media !== "application/problem+json")) {
          add("Service error responses must reference a resolved strict shared error family, not inline or permissive problems", location);
        }
        if (status === "401") {
          function onlyAuthentication401(value) {
            const schema = resolve(value, document);
            if (schema?.oneOf) return schema.oneOf.length > 0 && schema.oneOf.every(onlyAuthentication401);
            if (schema?.properties?.status?.const === 401 &&
                schema?.allOf?.length === 1 && schema.allOf[0].oneOf) {
              return schema.allOf[0].oneOf.every(onlyAuthentication401);
            }
            return ["authentication_required", "session_revoked"].includes(schema?.properties?.code?.const) &&
              schema?.properties?.status?.const === 401;
          }
          const authenticate = resolve(resolved?.headers?.["WWW-Authenticate"], document);
          if (!onlyAuthentication401(body?.schema) ||
              resolved?.["x-response-status"] !== 401 || authenticate?.required !== true ||
              resolved?.headers?.["WWW-Authenticate"]?.$ref !== "#/components/headers/DPoPAuthenticate" ||
              resolved?.headers?.["DPoP-Nonce"]?.$ref !== "#/components/headers/DPoPNonce" ||
              resolved?.["x-dpop-nonce-challenge"]?.authenticate !== 'DPoP error="use_dpop_nonce"' ||
              resolved?.["x-dpop-nonce-challenge"]?.["required-header"] !== "DPoP-Nonce") {
            add("401 requires the closed authentication-only family and exact DPoP challenge/fresh-nonce header contract", location);
          }
        }
        if (operation.tags?.includes("CustomDestinations")) {
          const noStore = resolve(resolved?.headers?.["Cache-Control"], document);
          if (!resolved?.["x-required-response-headers"]?.includes("Cache-Control") ||
              noStore?.required !== true || noStore?.schema?.const !== "no-store") {
            add("Custom-destination error responses require no-store, never cached provider or account data", location);
          }
          if (status === "default" && (resolved?.headers?.["Retry-After"]?.$ref !== "#/components/headers/RetryAfter" ||
              resolved?.["x-response-header-bindings"]?.retry_after_seconds !== "Retry-After")) {
            add("A supplied retry_after_seconds requires the equal Retry-After delay without authorizing automatic retry", location);
          }
        }
      }
    }
  }
  return problems;
};
