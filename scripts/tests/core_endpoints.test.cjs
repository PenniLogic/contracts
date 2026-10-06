"use strict";

const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const { Yaml } = require("@stoplight/spectral-parsers");
const Ajv = require("ajv/dist/2020").default;
const addFormats = require("ajv-formats");
const coreLint = require("../../spec/spectral-functions/coreEndpointContract");
const { resolveLocalRef, HTTP_METHODS } = require("../../spec/spectral-functions/_shared");

const root = join(__dirname, "..", "..");
const source = Yaml.parse(readFileSync(join(root, "spec", "openapi.yaml"), "utf8"));
assert.equal(source.diagnostics.length, 0);
const doc = JSON.parse(JSON.stringify(source.data));
const fixture = JSON.parse(readFileSync(join(root, "spec", "fixtures", "core-endpoints.v1.json"), "utf8"));
const authFixture = JSON.parse(readFileSync(join(root, "spec", "fixtures", "auth-endpoints.v1.json"), "utf8"));
fixture.payloads = { ...fixture.payloads, ...authFixture.payloads };
fixture.endpoints.push(...authFixture.endpoints);
const ajv = new Ajv({ strict: false });
addFormats(ajv);
ajv.addSchema({ components: doc.components }, "core");
const validators = new Map();
function valid(schema, value) {
  const key = JSON.stringify(schema);
  if (!validators.has(key)) validators.set(key, ajv.compile({ ...schema, components: doc.components }));
  return validators.get(key)(value);
}
function resolve(value) {
  return value?.$ref ? resolve(resolveLocalRef(value.$ref, doc)) : value;
}
function materialize(vector, invalid = false) {
  const request = structuredClone(vector.valid);
  request.headers = { ...fixture.request_headers, ...request.headers };
  if (request.body_ref) {
    request.body = structuredClone(fixture.payloads[request.body_ref]);
    delete request.body_ref;
  }
  if (invalid) {
    const mutation = vector.invalid;
    for (const [target, add] of Object.entries({
      body: mutation.body_add || mutation.body_set,
      query: mutation.query_add || mutation.query_set,
      path_parameters: mutation.path_set,
    })) if (add) request[target] = { ...request[target], ...add };
    for (const name of mutation.header_remove || []) delete request.headers[name];
  }
  return request;
}
function requestValid(vector, request) {
  const item = doc.paths[vector.path];
  const operation = item[vector.method.toLowerCase()];
  if (operation.operationId !== vector.operation_id) return false;
  const security = operation.security ?? doc.security;
  if (!security.some((requirement) => Object.keys(requirement).every((name) => {
    const scheme = doc.components.securitySchemes[name];
    if (scheme?.in === "cookie") return typeof request.cookie?.[scheme.name] === "string" &&
      valid({ $ref: "#/components/schemas/AuthRefreshTokenValue" }, request.cookie[scheme.name]);
    return scheme?.type === "apiKey" && typeof request.headers[scheme.name] === "string" &&
      (scheme.name !== "Authorization" || /^DPoP [A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(request.headers[scheme.name]));
  }))) return false;
  const parameters = [...(item.parameters || []), ...(operation.parameters || [])].map(resolve);
  for (const location of ["query", "path"]) {
    const supplied = request[location === "path" ? "path_parameters" : "query"] || {};
    if (Object.keys(supplied).some((name) => !parameters.some((p) => p.in === location && p.name === name))) return false;
  }
  for (const parameter of parameters) {
    const supplied = parameter.in === "header" ? request.headers :
      request[parameter.in === "path" ? "path_parameters" : parameter.in] || {};
    if (!(parameter.name in supplied)) {
      if (parameter.required) return false;
    } else if (!valid(parameter.schema, supplied[parameter.name])) return false;
  }
  const query = request.query || {};
  if (query.occurred_from && query.occurred_before && query.occurred_from >= query.occurred_before) return false;
  if (query.as_of && (query.view ?? "CURRENT") === "CURRENT") return false;
  const body = resolve(operation.requestBody);
  if (!body) return request.body === undefined;
  if (request.body === undefined) return body.required !== true;
  return valid(body.content["application/json"].schema, request.body);
}

test("every actual core operation has one reusable valid and invalid endpoint request fixture", () => {
  const operations = [];
  for (const [path, item] of Object.entries(doc.paths)) for (const method of HTTP_METHODS) {
    if (item[method]?.tags.some((tag) => ["Auth", "Accounts", "Transactions", "Categories"].includes(tag))) {
      operations.push(item[method].operationId);
    }
  }
  assert.deepEqual(fixture.endpoints.map((row) => row.operation_id).sort(), operations.sort());
  for (const row of fixture.endpoints) {
    assert.ok(requestValid(row, materialize(row)), `${row.operation_id}: valid request`);
    assert.equal(requestValid(row, materialize(row, true)), false, `${row.operation_id}: invalid request`);
  }
});

test("every operation rejects missing or malformed per-call proof and authenticated token headers", () => {
  for (const row of fixture.endpoints) {
    const request = materialize(row);
    const required = doc.paths[row.path][row.method.toLowerCase()].security?.[0]?.DPoPBootstrap !== undefined
      ? ["DPoP"] : ["DPoP", "Authorization"];
    for (const name of required) {
      const missing = structuredClone(request);
      delete missing.headers[name];
      assert.equal(requestValid(row, missing), false, `${row.operation_id}: missing ${name}`);
    }
    for (const proof of ["", "bare-token", "synthetic.proof.signature\n", 1]) {
      assert.equal(requestValid(row, { ...request, headers: { ...request.headers, DPoP: proof } }), false, row.operation_id);
    }
    if (required.includes("Authorization")) {
      assert.equal(requestValid(row, { ...request, headers: { ...request.headers, Authorization: "Bearer synthetic.access.signature" } }), false, row.operation_id);
    }
  }
});

test("all non-auth mutations require canonical random keys and published scope/lifetime", () => {
  for (const row of fixture.endpoints.filter((row) => row.method !== "GET" && !row.path.startsWith("/auth/"))) {
    const request = materialize(row);
    const missing = structuredClone(request);
    delete missing.headers["Idempotency-Key"];
    assert.equal(requestValid(row, missing), false, row.operation_id);
    assert.equal(requestValid(row, { ...request, headers: { ...request.headers,
      "Idempotency-Key": "00000000-0000-5000-8000-000000000010" } }), false, row.operation_id);
    const operation = doc.paths[row.path][row.method.toLowerCase()];
    assert.deepEqual(operation["x-idempotency-policy"],
      { scope: ["principal", "method", "path_template", "key"], lifetime: "P30D", retryHorizon: "P14D" });
  }
});

test("category create/update and response use only accepted icon/colour registries", () => {
  const create = fixture.endpoints.find((row) => row.operation_id === "createCategory");
  const request = materialize(create);
  for (const field of ["icon", "colour"]) {
    const missing = structuredClone(request);
    delete missing.body[field];
    assert.equal(requestValid(create, missing), false, `createCategory: missing ${field}`);
    for (const value of [null, 1, "", "SYNTHETIC_UNKNOWN"]) {
      assert.equal(requestValid(create, { ...request, body: { ...request.body, [field]: value } }), false, field);
      assert.equal(valid({ $ref: "#/components/schemas/UpdateCategoryRequest" },
        { [field]: value }), false, field);
    }
    assert.equal(valid({ $ref: "#/components/schemas/UpdateCategoryRequest" },
      { [field]: request.body[field] }), true, field);
  }
  const source = fixture.payloads.category;
  assert.ok(valid({ $ref: "#/components/schemas/Category" }, source));
  for (const field of ["icon", "colour"]) {
    assert.equal(valid({ $ref: "#/components/schemas/Category" },
      { ...source, [field]: "SYNTHETIC_UNKNOWN" }), false, field);
  }
});

test("paging, closed filters, stable sort, interval and explicit retired-version boundaries reject", () => {
  for (const row of fixture.invalid_query_boundaries) {
    const endpoint = fixture.endpoints.find((v) => v.operation_id === row.operation_id);
    const request = materialize(endpoint);
    request.query = { ...request.query, ...row.set };
    assert.equal(requestValid(endpoint, request), false, row.name);
  }
  for (const row of fixture.endpoints) for (const version of fixture.invalid_api_versions) {
    const request = materialize(row);
    request.headers["API-Version"] = version;
    assert.equal(requestValid(row, request), false, row.operation_id);
  }
  assert.ok(valid({ $ref: "#/components/schemas/CursorPage" }, fixture.payloads.cursor_end));
  assert.ok(valid({ $ref: "#/components/schemas/CursorPage" }, fixture.payloads.cursor_more));
  assert.equal(valid({ $ref: "#/components/schemas/CursorPage" }, { page_size: 1, has_more: true }), false);
  assert.equal(valid({ $ref: "#/components/schemas/CursorPage" }, { ...fixture.payloads.cursor_end, cursor: "unexpected" }), false);
});

test("all own posted transaction dates and nested money shapes use canonical provider schemas", () => {
  const schema = { $ref: "#/components/schemas/Transaction" };
  assert.ok(valid(schema, fixture.payloads.transaction));
  for (const field of ["occurred_at", "booked_at"]) {
    const wire = structuredClone(fixture.payloads.transaction);
    delete wire[field];
    assert.equal(valid(schema, wire), false, field);
  }
  for (const input of [1, 1.5, "0.01", { amount: 1, currency: "INR" }]) {
    const wire = structuredClone(fixture.payloads.post_transaction);
    wire.entries[0].amount = input;
    assert.equal(valid({ $ref: "#/components/schemas/PostTransactionRequest" }, wire), false, "numeric/bare money refused");
  }
  for (const field of ["raw_sms", "raw_email", "raw_event_digest", "owner_id", "dedupe_key"]) {
    const wire = { ...fixture.payloads.post_transaction, [field]: "SYNTHETIC_FORBIDDEN" };
    assert.equal(valid({ $ref: "#/components/schemas/PostTransactionRequest" }, wire), false, field);
  }
});

test("new source lint refuses weakened auth, date, key, immutable and raw-content constraints", () => {
  assert.deepEqual(coreLint(doc), []);
  const defects = [
    (v) => { v.security = []; },
    (v) => { v.paths["/accounts"].get.parameters = []; },
    (v) => { delete v.paths["/transactions"].post["x-duplicate-screen"]; },
    (v) => { v.paths["/transactions"].post["x-idempotency-policy"].lifetime = "P1D"; },
    (v) => { v.components.schemas.Transaction.required = v.components.schemas.Transaction.required.filter((x) => x !== "booked_at"); },
    (v) => { v.components.schemas.PostTransactionRequest.additionalProperties = true; },
    (v) => { v.components.schemas.LedgerEntryInput.properties.raw_email = { type: "string" }; },
    (v) => { v.paths["/transactions/{transactionId}"].delete = v.paths["/transactions/{transactionId}"].get; },
    (v) => { v.components.schemas.Transaction.properties.transaction_id = { type: "string" }; },
    (v) => { delete v.components.schemas.AuthStepUpIntentTarget["x-pennilogic-strict-provider"]; },
    (v) => { delete v.components.schemas.AuthStepUpIntentBody["x-pennilogic-strict-provider"]; },
  ];
  for (const mutate of defects) {
    const planted = structuredClone(doc);
    mutate(planted);
    assert.ok(coreLint(planted).length > 0);
  }
});

test("each registered step-up intent binds the exact operation method complete target and strict wire body", () => {
  const schema = { $ref: "#/components/schemas/AuthStepUpIntent" };
  const enums = doc.components.schemas.AuthStepUpIntent.properties.operation.enum;
  const destinations = JSON.parse(readFileSync(join(root, "spec", "fixtures", "custom-destination-wire.v1.json"), "utf8"));
  for (const operation of enums) {
    const core = fixture.endpoints.find((row) => row.operation_id === operation);
    const envelope = core ? {
      version: "step-up-request@1", operation, method: core.method,
      target: core.valid.path_parameters || {},
      body: core.valid.body_ref ? fixture.payloads[core.valid.body_ref] : {},
    } : {
      version: "step-up-request@1", operation, method: "POST",
      target: operation === "registerCustomDestination" ? {} : { destinationId: destinations.destination.destinationId },
      body: operation === "registerCustomDestination" ? destinations.registration : destinations.lifecycle,
    };
    assert.ok(valid(schema, envelope), `${operation}: exact intent`);
    for (const mutation of [
      { ...envelope, method: "GET" }, { ...envelope, target: { ...envelope.target, owner_id: "SYNTHETIC" } },
      { ...envelope, body: { ...envelope.body, owner_id: "SYNTHETIC" } },
      { ...envelope, sid: "SYNTHETIC" }, { ...envelope, rq: "SYNTHETIC" },
    ]) assert.equal(valid(schema, mutation), false, `${operation}: altered intent`);
    for (const member of Object.keys(envelope.target)) {
      const target = { ...envelope.target }; delete target[member];
      assert.equal(valid(schema, { ...envelope, target }), false, `${operation}: omitted target`);
    }
  }
});

test("the one-shot recovering-device add remains distinct from mature-passkey step-up and protective actions", () => {
  const ordinary = doc.paths["/auth/credentials"].post;
  assert.ok(ordinary.parameters.some((parameter) => parameter.$ref === "#/components/parameters/StepUpToken"));
  const recovery = doc.paths["/auth/recoveries/{recoveryId}/credential-grant"].post;
  assert.equal(recovery.operationId, "addRecoveryCredential");
  assert.deepEqual(recovery["x-auth-recovery-exception"], {
    maximumAdds: 1, recoveringDeviceOnly: true, restrictedPeriodOnly: true, hybridAllowed: false,
    proof: "verified-recovery-and-current-session-key",
  });

  test("unproven recovery has no account/window context and every proven active phase carries its bound instrument", () => {
    const schema = { $ref: "#/components/schemas/AuthRecoveryProgress" };
    const unproven = { recovery_id: "00000000-0000-7000-8000-000000000024", state: "unproven" };
    assert.ok(valid(schema, unproven));
    for (const [field, value] of Object.entries({
      recovery_proof: "synthetic_recovery_instrument", window_ends_at: null, retry_after_seconds: 30,
      restricted_until: "2026-10-02T00:00:00.000Z", not_me_until: "2026-10-02T00:00:00.000Z",
      pending_route: "recovery_code", action: "cancel_and_restart",
    })) assert.equal(valid(schema, { ...unproven, [field]: value }), false, field);
    for (const value of [authFixture.payloads.notification_pending, authFixture.payloads.cooling_off,
      authFixture.payloads.recovery_completion.recovery]) {
      assert.ok(valid(schema, value));
      const missing = { ...value }; delete missing.recovery_proof;
      assert.equal(valid(schema, missing), false, "proven instrument omitted");
    }
  });

  test("all accepted category source/reason/provenance pairs are closed and carried references are opaque", () => {
    const schema = { $ref: "#/components/schemas/Categorisation" };
    const allowed = {
      USER_EDIT: ["USER"], USER_CONFIRMATION: ["USER"], RULE_APPLIED: ["RULE"], IMPORT_COLUMN: ["IMPORT"],
      AI_APPLIED: ["AI"], POSTING_DEFAULT: ["SYSTEM"], REVERSAL_MIRROR: ["SYSTEM"], REFUND_LINK: ["SYSTEM"],
      REPOST_CARRY: ["USER", "RULE", "IMPORT", "AI", "SYSTEM"],
      DUPLICATE_CARRY: ["USER", "RULE", "IMPORT", "AI", "SYSTEM"],
      RULE_ROLLBACK: ["USER", "RULE", "IMPORT", "AI", "SYSTEM"],
    };
    assert.deepEqual(Object.keys(allowed).sort(), [...doc.components.schemas.CategoryAssignmentReason.enum].sort());
    for (const [reason, sources] of Object.entries(allowed)) {
      for (const source of doc.components.schemas.CategoryAssignmentSource.enum) {
        const required = !["USER_EDIT", "USER_CONFIRMATION", "POSTING_DEFAULT"].includes(reason);
        const wire = { ...fixture.payloads.categorisation, reason, source,
          ...(required ? { provenance_ref: "00000000-0000-7000-8000-000000000030" } : {}) };
        assert.equal(valid(schema, wire), sources.includes(source), `${reason}/${source}`);
        if (required) {
          const omitted = { ...wire }; delete omitted.provenance_ref;
          assert.equal(valid(schema, omitted), false, `${reason}: missing provenance`);
        } else if (reason !== "USER_CONFIRMATION") {
          assert.equal(valid(schema, { ...wire, provenance_ref: "00000000-0000-7000-8000-000000000030" }), false);
        }
      }
    }
  });
  assert.ok(!recovery.parameters.some((parameter) => parameter.$ref === "#/components/parameters/StepUpToken"));
  assert.ok(!doc.components.schemas.AuthStepUpIntent.properties.operation.enum.includes("addRecoveryCredential"));
  for (const id of ["revokeSession", "revokeAllSessions", "cancelRecoveryFromSession", "reverseRecovery"]) {
    const row = fixture.endpoints.find((value) => value.operation_id === id);
    const operation = doc.paths[row.path][row.method.toLowerCase()];
    assert.ok(!operation.parameters.some((parameter) => parameter.$ref === "#/components/parameters/StepUpToken"), id);
  }
});

module.exports = { materialize, requestValid };
