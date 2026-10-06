"use strict";

const { HTTP_METHODS, MUTATING_METHODS, resolveLocalRef, referencesMoney } = require("./_shared");
const { isDeepStrictEqual } = require("node:util");

const TAGS = new Set(["Auth", "Accounts", "Transactions", "Categories"]);
const OBJECTS = new Set(["ApplicationProblemDetail", "SessionRevokedProblemDetail", "CursorPage",
  "CreateAccountRequest", "UpdateAccountRequest", "Account", "AccountPage", "OpeningBalanceRequest",
  "LedgerEntryInput", "LedgerEntry", "PostTransactionRequest", "Transaction", "TransactionPage",
  "ReverseTransactionRequest", "CorrectTransactionRequest", "TransactionCorrection",
  "CategorisationView", "CategoryAllocationLine", "CategoryExactLineInput", "CategoryWeightedLineInput",
  "CategoryEntryAssignmentInput", "CategoryAssignmentRequest", "Categorisation", "CreateCategoryRequest",
  "UpdateCategoryRequest", "Category", "CategoryPage", "RedirectCategoryRequest", "CategoryRedirect"]);
const RAW = new Set(["rawsms", "rawemail", "smsbody", "emailbody", "rawmessage", "rawmessages",
  "rawcontent", "messagetext", "rawhash", "rawdigest", "raweventdigest", "messagedigest",
  "ownerid", "dedupekey", "dedupekeyversion"]);
const key = (value) => value.toLowerCase().replace(/[^a-z0-9]/g, "");
const IDEMPOTENCY_POLICY = { scope: ["principal", "method", "path_template", "key"], lifetime: "P30D", retryHorizon: "P14D" };
const DECISION_RESPONSE = "#/components/responses/DuplicateSuspected";
const DECISION_SCHEMA = "#/components/schemas/DedupOutcome";

module.exports = function coreEndpointContract(document) {
  const findings = [];
  const add = (message, path) => findings.push({ message: `Core contract: ${message}`, path });
  const schemas = document.components?.schemas || {};
  for (const [name, schema] of Object.entries(schemas)) {
    if (OBJECTS.has(name) || (name.startsWith("Auth") && schema.type === "object")) {
      if (schema.type !== "object" || schema.additionalProperties !== false ||
          schema["x-pennilogic-strict-provider"] !== true) {
        add("closed providers must retain strict generated conversion and serialization",
          ["components", "schemas", name]);
      }
    }
  }
  function resolve(value, active = new Set()) {
    if (!value || typeof value !== "object" || !value.$ref) return value;
    if (active.has(value.$ref)) return undefined;
    active.add(value.$ref);
    return resolve(resolveLocalRef(value.$ref, document), active);
  }
  function containsDecision(value, active = new Set(), depth = 0, work = { count: 0 }) {
    if (!value || typeof value !== "object") return false;
    if (++work.count > 16384 || depth > 64) return undefined;
    if (value.$ref === DECISION_SCHEMA) return true;
    if (value.$ref) {
      if (active.has(value.$ref)) return undefined;
      const target = resolveLocalRef(value.$ref, document);
      if (!target) return undefined;
      const reference = containsDecision(target, new Set(active).add(value.$ref), depth + 1, work);
      if (reference !== false) return reference;
    }
    const children = [...Object.values(value.properties || {}), value.items, value.not, value.if, value.then, value.else,
      ...(value.allOf || []), ...(value.oneOf || []), ...(value.anyOf || [])];
    for (const child of children) {
      const found = containsDecision(child, new Set(active), depth + 1, work);
      if (found !== false) return found;
    }
    return false;
  }
  function requestShape(value, path, active = new Set()) {
    if (!value || typeof value !== "object") return;
    if (value.$ref) {
      if (!value.$ref.startsWith("#/components/schemas/")) {
        add("request schemas must reference named local components", path);
        return;
      }
      if (!active.has(value.$ref)) {
        const next = new Set(active).add(value.$ref);
        requestShape(resolveLocalRef(value.$ref, document), path, next);
      }
    }
    if (value.type === "object" && value.additionalProperties !== false) {
      add("request objects must reject unknown/raw fields, including nested objects", path);
    }
    for (const [name, member] of Object.entries(value.properties || {})) {
      if (RAW.has(key(name))) add("raw content, digests and client owner/fingerprint claims are absent", [...path, "properties", name]);
      if (member.readOnly === true) add("server-derived readOnly response members are not request fields", [...path, "properties", name]);
      requestShape(member, [...path, "properties", name], new Set(active));
    }
    if (value.items) requestShape(value.items, [...path, "items"], new Set(active));
    for (const branch of ["allOf", "oneOf", "anyOf"]) {
      for (const [index, child] of (value[branch] || []).entries()) {
        requestShape(child, [...path, branch, index], new Set(active));
      }
    }
  }
  if (JSON.stringify(document.security) !== '[{"DPoP":[]}]') {
    add("root security must retain the accepted raw-header DPoP binding", ["security"]);
  }
  if (document["x-core-contract"]?.pagination?.maximumPageSize !== 100 ||
      document.components?.parameters?.CorePageSize?.schema?.maximum !== 100 ||
      schemas.CursorPage?.properties?.page_size?.maximum !== 100) {
    add("one shared maximum page size binds list parameters and the cursor envelope", ["x-core-contract", "pagination"]);
  }
  for (const [route, item] of Object.entries(document.paths || {})) {
    for (const method of HTTP_METHODS) {
      const operation = item[method];
      if (!operation?.tags?.some((tag) => TAGS.has(tag))) continue;
      const at = ["paths", route, method];
      const parameters = [...(item.parameters || []), ...(operation.parameters || [])];
      if (!parameters.some((parameter) => parameter.$ref === "#/components/parameters/DPoPProof")) {
        add("every operation requires a fresh per-send DPoP proof; no static global proof", at);
      }
      if (!parameters.some((parameter) => parameter.$ref === "#/components/parameters/CoreApiVersion")) {
        add("each core operation declares the version-selection boundary", at);
      }
      if (MUTATING_METHODS.has(method)) {
        const header = parameters.some((parameter) => parameter.$ref === "#/components/parameters/IdempotencyKey");
        const exempt = operation.tags.includes("Auth") && operation["x-idempotency"] === "auth" &&
          typeof operation["x-idempotency-reason"] === "string" && operation["x-idempotency-reason"].trim();
        if (!header && !exempt) add("mutations need the canonical key or a reasoned authentication exemption", at);
        if (header && !isDeepStrictEqual(operation["x-idempotency-policy"], IDEMPOTENCY_POLICY)) {
          add("keyed mutations declare the accepted principal/operation/key scope and P30D/P14D bounds", at);
        }
        if (referencesMoney(operation.requestBody, document) &&
            !["reverseTransaction"].includes(operation.operationId) && !operation["x-duplicate-screen"]) {
          add("financial writes publish their structured duplicate-screen field set and window", at);
        }
        if (operation["x-duplicate-screen"]) {
          const decision = operation.responses?.["202"];
          const shared = resolve(decision);
          const expected = { allOf: [{ $ref: DECISION_SCHEMA },
            { properties: { outcome: { const: "duplicate_suspected" } } }] };
          if (decision?.$ref !== DECISION_RESPONSE ||
              Object.keys(decision).some((field) => !["$ref", "summary", "description"].includes(field)) ||
              shared?.["x-response-status"] !== 202 ||
              !isDeepStrictEqual(shared?.content?.["application/json"]?.schema, expected)) {
            add("duplicate screens require the canonical shared T-CON-10 decision under its distinct 202 status",
              [...at, "responses", "202"]);
          }
          const created = Object.entries(operation.responses || {}).filter(([status]) => /^2[0-9]{2}$/.test(status) && status !== "202");
          if (!created.length) add("a duplicate decision is distinct from the successful resource/effect status", [...at, "responses"]);
          for (const [status, response] of created) {
            const resolved = resolve(response);
            if (!resolved || Object.values(resolved.content || {}).some((content) =>
              containsDecision(content.schema) !== false)) {
              add("a successful resource/effect response cannot also carry the shared duplicate decision",
                [...at, "responses", status]);
            }
          }
        }
      }
      const body = resolve(operation.requestBody);
      for (const [media, message] of Object.entries(body?.content || {})) {
        if (media !== "application/json") add("core requests are typed JSON, not raw messages or uploads", at);
        requestShape(message.schema, [...at, "requestBody", "content", media, "schema"]);
      }
      if (!operation.responses?.["401"] || !operation.responses?.default) {
        add("explicit DPoP challenge and shared RFC9457 error contracts are required", at);
      }
      if (operation.deprecated === true) {
        for (const [status, response] of Object.entries(operation.responses || {})) {
          const headers = resolve(response)?.headers || {};
          for (const [name, component] of [["Deprecation", "CoreDeprecation"], ["Sunset", "CoreSunset"]]) {
            if (headers[name]?.$ref !== `#/components/headers/${component}`) {
              add("deprecated operations require the shared notice and sunset on every response", [...at, "responses", status]);
            }
          }
        }
      }
    }
  }
  if (schemas.Transaction?.properties?.transaction_id?.$ref !== "#/components/schemas/DedupRecordId" ||
      document.components?.parameters?.TransactionId?.schema?.$ref !== "#/components/schemas/DedupRecordId") {
    add("transaction paths/resources reuse the exact matched-record alias provider", ["components", "schemas", "Transaction"]);
  }
  for (const member of ["occurred_at", "booked_at"]) {
    if (!schemas.Transaction?.required?.includes(member) ||
        schemas.Transaction?.properties?.[member]?.$ref !== "#/components/schemas/Instant") {
      add("posted transactions require both canonical effective and booking instants", ["components", "schemas", "Transaction"]);
    }
    for (const name of ["Transaction", "Categorisation"]) {
      const projection = schemas[name]?.properties?.category_id;
      if (projection?.readOnly !== true || JSON.stringify(projection.anyOf) !==
          '[{"$ref":"#/components/schemas/ResourceId"},{"type":"null"}]') {
        add("ADR-016 category projections must declare readOnly and preserve explicit null",
          ["components", "schemas", name, "properties", "category_id"]);
      }
    }
    if (schemas.Transaction?.required?.includes("category_id")) {
      add("transaction projection omission remains permitted; do not invent mandatory presence",
        ["components", "schemas", "Transaction", "required"]);
    }
  }
  if (document.paths?.["/transactions/{transactionId}"]?.patch ||
      document.paths?.["/transactions/{transactionId}"]?.delete ||
      document.paths?.["/categories/{categoryId}"]?.delete) {
    add("ledger correction and category redirect/archive replace destructive edits", ["paths"]);
  }
  return findings;
};
