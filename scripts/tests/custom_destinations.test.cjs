"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const { createHash } = require("node:crypto");
const { spawnSync } = require("node:child_process");
const { parse } = require("@stoplight/yaml");
const { schema: validateSchema } = require("@stoplight/spectral-functions");
const contract = require("../../spec/spectral-functions/customDestinationContract");
const noAddress = require("../../spec/spectral-functions/noInferenceAddress");
const { SOURCE, loadSource } = require("../../spec/spectral-functions/_customDestinationSource");

const ROOT = path.resolve(__dirname, "..", "..");
const base = parse(fs.readFileSync(path.join(ROOT, "spec", "openapi.yaml"), "utf8"));
const fixture = JSON.parse(fs.readFileSync(path.join(ROOT, "spec", "fixtures", "custom-destination-wire.v1.json"), "utf8"));
const planted = JSON.parse(fs.readFileSync(path.join(ROOT, "spec", "fixtures", "custom-destination-inference-negative.v1.json"), "utf8"));
const { consequence, schema } = loadSource();
const context = () => ({ path: [], documentInventory: {}, rule: { resolved: false } });
const validate = (value, shape) => validateSchema(value, { schema: shape, dialect: "draft2020-12", allErrors: true }, context());
const model = (name, value) => validate(value, { $ref: `#/components/schemas/${name}`, components: base.components });
const findings = (doc) => contract(doc, null, context());

function probe() {
  const doc = structuredClone(base);
  Object.assign(doc.paths, structuredClone(planted.paths));
  Object.assign(doc.components.schemas, structuredClone(planted.schemas));
  Object.assign(doc.components.requestBodies, structuredClone(planted.requestBodies));
  return doc;
}

function withRequest(shape) {
  const doc = structuredClone(base);
  doc.paths["/ai/inference"] = { post: { requestBody: { content: { "application/json": { schema: shape } } } } };
  return doc;
}

function withLintProbe(check) {
  fs.mkdirSync(path.join(ROOT, "build"), { recursive: true });
  const directory = fs.mkdtempSync(path.join(ROOT, "build", "custom-destination-lint-"));
  try {
    for (const name of ["currency-registry.v1.json", "error-catalogue.v1.json", "client-state-bindings.v1.json", "import-group.v1.json"]) {
      fs.copyFileSync(path.join(ROOT, "spec", name), path.join(directory, name));
    }
    const file = path.join(directory, "probe.json");
    check((doc) => {
      fs.writeFileSync(file, JSON.stringify(doc));
      const result = spawnSync("python", [path.join(ROOT, "scripts", "lint_spec.py"), "--spec", file], { cwd: ROOT, encoding: "utf8" });
      assert.equal(result.error, undefined);
      return result;
    });
  } finally {
    fs.rmSync(directory, { recursive: true });
  }
}

test("canonical accepted consequence satisfies its complete published closed schema", () => {
  assert.deepEqual(validateSchema(consequence, { schema, dialect: "draft7", allErrors: true }, context()), []);
  assert.deepEqual(base["x-custom-destination-source"], SOURCE);
  assert.deepEqual(findings(base), []);
});

test("canonical source rejects duplicate-key/whitespace drift and changed closed-schema bytes", () => {
  fs.mkdirSync(path.join(ROOT, "build"), { recursive: true });
  const directory = fs.mkdtempSync(path.join(ROOT, "build", "custom-destination-source-"));
  try {
    for (const name of ["ai-egress-consequences.json", "ai-egress-consequences.schema.json"]) {
      fs.copyFileSync(path.join(ROOT, "spec", "adr022", name), path.join(directory, name));
    }
    assert.deepEqual(loadSource(directory).consequence, consequence);
    const input = path.join(directory, "ai-egress-consequences.json");
    const bytes = fs.readFileSync(input, "utf8");
    fs.writeFileSync(input, bytes.replace("{", '{"schema_version":1,'));
    assert.throws(() => loadSource(directory), /canonical byte digest mismatch/);
    fs.writeFileSync(input, bytes + "\n");
    assert.throws(() => loadSource(directory), /canonical byte digest mismatch/);
    fs.writeFileSync(input, bytes);
    fs.appendFileSync(path.join(directory, "ai-egress-consequences.schema.json"), "\n");
    assert.throws(() => loadSource(directory), /canonical byte digest mismatch/);
  } finally {
    fs.rmSync(directory, { recursive: true });
  }
});

test("canonical source fails closed on unknown members, contradictory ON and nonempty approval/inventory", () => {
  const mutations = [
    (copy) => { copy.extra = true; },
    (copy) => { copy.wire.extra = true; },
    (copy) => { copy.enablement.global_default = "on"; },
    (copy) => { copy.enablement.custom_default = "on"; },
    (copy) => { copy.enablement.approved_providers = ["synthetic"]; },
    (copy) => { copy.enablement.approved_custom_destinations = ["synthetic"]; },
    (copy) => { copy.enablement.deployment_inventory = ["synthetic"]; },
    (copy) => { copy.enablement.runtime_implementation_claimed = true; },
    (copy) => { copy.address_policy.denied_ipv4.pop(); },
    (copy) => { copy.wire.registration_raw_key = true; },
    (copy) => { copy.state_denials.revoked = "destination_suspended"; },
  ];
  for (const mutate of mutations) {
    const copy = structuredClone(consequence);
    mutate(copy);
    assert.ok(validateSchema(copy, { schema, dialect: "draft7", allErrors: true }, context()).length > 0);
  }
});

test("all four canonical enums reject missing, additional, duplicate, reordered and copied definitions", () => {
  for (const [name, values] of Object.entries(consequence.enums)) {
    for (const invalid of [values.slice(1), [...values, "invented"], [...values, values[0]], [...values].reverse()]) {
      const doc = structuredClone(base);
      doc.components.schemas[name].enum = invalid;
      assert.ok(findings(doc).some((finding) => finding.message.includes(`${name} must declare exactly`)), name);
    }
    const doc = structuredClone(base);
    doc.components.schemas.CustomDestinationCopiedEnum = { type: "string", enum: [...values] };
    assert.ok(findings(doc).some((finding) => finding.message.includes(`duplicate ${name}`)), name);
  }
});

test("real Spectral CLI rejects complete canonical enum copies regardless of widening, order or repeated members", async (t) => {
  for (const [name, values] of Object.entries(consequence.enums)) {
    await t.test(name, () => withLintProbe((lint) => {
      const variants = {
        Exact: [...values],
        Reordered: [...values].reverse(),
        Widened: [...values, "synthetic_extra"],
        ReorderedSuperset: ["synthetic_extra", ...[...values].reverse(), "synthetic_other"],
        RepeatedMember: [...values, values[0]],
        WidenedRepeatedMember: ["synthetic_extra", ...values, values[0]],
      };
      const doc = structuredClone(base);
      for (const [variant, members] of Object.entries(variants)) {
        doc.components.schemas[`EnumCopy${variant}`] = { type: "string", enum: members };
      }
      const result = lint(doc);
      assert.equal(result.status, 1, result.stdout + result.stderr);
      const diagnostics = result.stderr.split(/\r?\n/);
      for (const variant of Object.keys(variants)) {
        assert.ok(diagnostics.some((line) =>
          line.includes("[pl-custom-destination-contract]") &&
          line.includes(`(components.schemas.EnumCopy${variant}.enum)`) &&
          line.includes(`duplicate ${name} enumeration; use $ref: '#/components/schemas/${name}'`)),
        `${name}/${variant}: ${result.stdout}${result.stderr}`);
      }
    }));
  }
});

test("real Spectral CLI permits unchanged canonical enums, ref-only aliases and unrelated or partial-overlap enums", () => {
  withLintProbe((lint) => {
    const doc = structuredClone(base);
    for (const [name, values] of Object.entries(consequence.enums)) {
      doc.components.schemas[`EnumAlias${name}`] = { $ref: `#/components/schemas/${name}` };
      doc.components.schemas[`EnumUnrelated${name}`] = {
        type: "string", enum: values.map((_, index) => `synthetic_${index}`),
      };
      doc.components.schemas[`EnumPartial${name}`] = { type: "string", enum: values.slice(1) };
      doc.components.schemas[`EnumPartialSuperset${name}`] = {
        type: "string", enum: [...values.slice(1), "synthetic_extra", "synthetic_other"],
      };
    }
    const result = lint(doc);
    assert.equal(result.status, 0, result.stdout + result.stderr);
  });
});

test("source reference, state mapping, registration closure and header references cannot drift", () => {
  const mutations = [
    (doc) => { doc["x-custom-destination-source"].commit = "0".repeat(40); },
    (doc) => { doc["x-custom-destination-source"].consequencesSha256 = "0".repeat(64); },
    (doc) => { doc.components.schemas.CustomDestination["x-state-denials"].revoked = "destination_suspended"; },
    (doc) => { doc.components.schemas.CustomDestinationRegistrationRequest.additionalProperties = true; },
    (doc) => { doc.components.schemas.CustomDestinationRegistrationRequest.properties.providerKey = { type: "string" }; },
    (doc) => { doc.components.schemas.CustomDestinationRegistrationRequest.properties.credentialHeader = { type: "string" }; },
    (doc) => { delete doc.components.schemas.CustomDestinationModel.additionalProperties; },
    (doc) => { delete doc.components.schemas.CustomDestinationRegistrationRequest["x-pennilogic-strict-provider"]; },
  ];
  for (const mutate of mutations) {
    const doc = structuredClone(base);
    mutate(doc);
    assert.ok(findings(doc).length > 0);
  }
});

test("valid enrollment, lifecycle and owner-only response schemas accept synthetic wire fixtures", () => {
  assert.deepEqual(model("CustomDestinationRegistrationRequest", fixture.registration), []);
  assert.deepEqual(model("CustomDestinationLifecycleRequest", fixture.lifecycle), []);
  assert.deepEqual(model("CustomDestinationValidationResult", fixture.validation), []);
  assert.deepEqual(model("CustomDestination", fixture.destination), []);
  assert.deepEqual(model("CustomDestinationList", { destinations: [fixture.destination] }), []);
  for (const credentialHeader of consequence.enums.CredentialHeader) {
    assert.deepEqual(model("CustomDestinationRegistrationRequest", {
      host: fixture.registration.host, credentialHeader, models: ["fixture-model"],
    }), []);
  }
});

test("closed schemas reject every planted registration and lifecycle payload before generation claims", () => {
  for (const vector of fixture.invalid_registration) {
    assert.ok(model("CustomDestinationRegistrationRequest", { ...fixture.registration, ...vector.add }).length > 0, vector.name);
  }
  for (const vector of fixture.invalid_lifecycle) {
    assert.ok(model("CustomDestinationLifecycleRequest", vector.wire).length > 0, vector.name);
  }
  assert.ok(model("CustomDestinationRegistrationRequest", { ...fixture.registration, models: Array.from({ length: 17 }, (_, i) => `fixture-${i}`) }).length > 0);
  const response = structuredClone(fixture.destination);
  response.models[0].host = "blocked.example";
  assert.ok(model("CustomDestination", response).length > 0);
  delete response.models[0].host;
  response.destinationClass = "code_managed_provider";
  assert.ok(model("CustomDestination", response).length > 0);
  for (const codepoint of fixture.ecmascript_whitespace) {
    const character = String.fromCodePoint(codepoint);
    for (const add of [{ host: `models${character}.example` }, { pathPrefix: `/v1${character}/model` }]) {
      assert.ok(model("CustomDestinationRegistrationRequest", { ...fixture.registration, ...add }).length > 0, String(codepoint));
    }
  }
});

test("six operations stay namespaced, nonmonetary and owner-bound; mutations retain idempotency", () => {
  const operations = [];
  for (const [route, item] of Object.entries(base.paths)) {
    if (!route.startsWith("/ai/custom-destinations")) continue;
    for (const method of ["get", "post", "put", "patch", "delete"]) {
      const op = item[method];
      if (!op) continue;
      operations.push(op.operationId);
      assert.deepEqual(op.tags, ["CustomDestinations"]);
      const refs = (op.parameters || []).map((parameter) => parameter.$ref);
      if (method !== "get") assert.ok(refs.includes("#/components/parameters/IdempotencyKey"));
      else assert.ok(!refs.includes("#/components/parameters/IdempotencyKey"));
      assert.equal(op["x-idempotency"], undefined);
      assert.ok(refs.includes("#/components/parameters/DPoPProof"));
      assert.equal(refs.includes("#/components/parameters/StepUpToken"),
        ["registerCustomDestination", "activateCustomDestination"].includes(op.operationId));
      assert.equal(op.responses["401"].$ref, "#/components/responses/CustomDestinationDPoPChallenge");
      assert.ok(!JSON.stringify(op).includes("#/components/schemas/Money"));
    }
  }
  assert.deepEqual(operations.sort(), [
    "activateCustomDestination", "listCustomDestinations", "registerCustomDestination",
    "revokeCustomDestination", "suspendCustomDestination", "validateCustomDestination",
  ]);
  assert.deepEqual(Object.keys(base.components.schemas.CustomDestinationRegistrationRequest.properties).sort(),
    [...consequence.wire.registration_fields].sort());
  assert.deepEqual(base.security, [{ DPoP: [] }]);
  assert.equal(base.components.securitySchemes.DPoP.type, "apiKey");
  assert.equal(base.components.securitySchemes.DPoP.name, "Authorization");
  assert.equal(base.components.securitySchemes.DPoP.in, "header");
  assert.equal(base.paths["/ai/custom-destinations/{destinationId}"].delete.operationId, "revokeCustomDestination");
  for (const name of ["DPoPProofValue", "StepUpTokenValue"]) {
    assert.equal(base.components.schemas[name].default, undefined);
    assert.equal(base.components.schemas[name].example, undefined);
  }
});

test("recursive lint finds a planted address through requestBody refs, arrays, compositions and cycles", () => {
  const doc = probe();
  const result = noAddress(doc);
  assert.ok(result.some((finding) => finding.path.join(".").endsWith("CustomDestinationLintTarget.properties.base_url")));
  assert.ok(result.some((finding) => finding.message.includes("destinationId/custom_model_id")));
  delete doc.components.schemas.CustomDestinationLintTarget.properties.base_url;
  assert.deepEqual(noAddress(doc), []);
  assert.deepEqual(noAddress(base), []);
});

test("real Spectral CLI fails nested address fixtures actionably and passes the repaired control", () => {
  withLintProbe((lint) => {
    const doc = probe();
    for (const field of ["base_url", "endpoint", "host"]) {
      doc.components.schemas.CustomDestinationLintTarget.properties = { [field]: { type: "string" } };
      const result = lint(doc);
      assert.equal(result.status, 1, result.stdout + result.stderr);
      assert.match(result.stderr, /\[pl-no-inference-address\]/);
      assert.ok(result.stderr.includes(`CustomDestinationLintTarget.properties.${field}`));
      assert.match(result.stderr, /destinationId\/custom_model_id/);
    }
    doc.components.schemas.CustomDestinationLintTarget.properties = {};
    const result = lint(doc);
    assert.equal(result.status, 0, result.stdout + result.stderr);
  });
});

test("lint refuses every canonical address alias and camel/encoded spelling in nested input fields", () => {
  for (const field of [...consequence.wire.forbidden_inference_fields, "endpointUrl", "baseAddress", "host_name", "apiBase", "%68ost"]) {
    const shape = { type: "object", additionalProperties: false, properties: {
      arguments: { type: "object", additionalProperties: false, properties: { [field]: { type: "string" } } },
    } };
    const result = noAddress(withRequest(shape));
    assert.ok(result.some((finding) => finding.path.at(-1) === field), field);
  }
});

test("lint follows ref siblings, escaped pointers, path refs and all request composition branches", () => {
  for (const branch of ["allOf", "anyOf", "oneOf", "prefixItems"]) {
    const doc = withRequest({ [branch]: [{ type: "object", additionalProperties: false, properties: { endpoint: { type: "string" } } }] });
    assert.ok(noAddress(doc).some((finding) => finding.path.includes("endpoint")), branch);
  }
  const doc = withRequest({
    $ref: "#/components/schemas/CustomDestinationLint~1Escaped~0",
    properties: { host: { type: "string" } }, additionalProperties: false,
  });
  doc.components.schemas["CustomDestinationLint/Escaped~"] = {
    type: "object", additionalProperties: false, properties: { endpoint: { type: "string" } },
  };
  assert.ok(noAddress(doc).some((finding) => finding.path.includes("endpoint")));
  assert.ok(noAddress(doc).some((finding) => finding.path.includes("host")));
  doc.components.pathItems = { Target: doc.paths["/ai/inference"] };
  doc.paths["/ai/inference"] = { $ref: "#/components/pathItems/Target" };
  assert.ok(noAddress(doc).some((finding) => finding.path.includes("endpoint")));
});

test("lint refuses open nested maps, unconstrained schemas, encoded tool payloads and external refs", () => {
  for (const nested of [
    { type: "object" },
    { type: "object", additionalProperties: { type: "string" } },
    { type: "object", additionalProperties: false, patternProperties: { ".*": { type: "string" } } },
    { type: "object", additionalProperties: false, unevaluatedProperties: true },
    {},
    true,
    { type: "string", contentEncoding: "base64" },
    { type: "string", contentMediaType: "application/json" },
    { type: "string" },
    { $ref: "https://blocked.example/request.json" },
    { $ref: "#/components/schemas/Missing" },
    { $dynamicRef: "#request" },
  ]) {
    const doc = withRequest({ type: "object", additionalProperties: false, properties: { arguments: nested } });
    assert.ok(noAddress(doc).length > 0, JSON.stringify(nested));
  }
});

test("lint covers query/header parameters and standalone tool request components without relying on tags", () => {
  const doc = withRequest({ type: "object", additionalProperties: false, properties: { destinationId: { type: "string" } } });
  doc.components.parameters.HiddenHost = { name: "host", in: "query", schema: { type: "string" } };
  doc.paths["/ai/inference"].parameters = [{ $ref: "#/components/parameters/HiddenHost" }];
  assert.ok(noAddress(doc).some((finding) => finding.path.includes("HiddenHost")));
  delete doc.paths["/ai/inference"];
  doc.components.schemas.AssistantToolRequest = { type: "object", additionalProperties: false, properties: { endpoint: { type: "string" } } };
  assert.ok(noAddress(doc).some((finding) => finding.path.includes("AssistantToolRequest")));
});

test("lint rejects constant objects, untyped array items and media without a schema", () => {
  for (const shape of [
    { const: { host: "blocked.example" } },
    { enum: [{ endpoint: "https://blocked.example" }] },
    { type: "array" },
    { type: "array", items: true },
    { not: { type: "string" } },
  ]) assert.ok(noAddress(withRequest(shape)).length > 0);
  const doc = withRequest({ type: "string" });
  delete doc.paths["/ai/inference"].post.requestBody.content["application/json"].schema;
  assert.ok(noAddress(doc).some((finding) => finding.message.includes("every inference/tool request media")));
});

test("lint refuses ref or union encoded tool arguments without rejecting ordinary prompt strings", () => {
  const doc = withRequest({ type: "object", additionalProperties: false, properties: {
    prompt: { $ref: "#/components/schemas/CustomDestinationLintText" },
    toolArguments: { anyOf: [{ $ref: "#/components/schemas/CustomDestinationLintText" }, { type: "null" }] },
  } });
  doc.components.schemas.CustomDestinationLintText = { type: "string" };
  assert.ok(noAddress(doc).some((finding) => finding.message.includes("string carrying encoded")));
  delete doc.paths["/ai/inference"].post.requestBody.content["application/json"].schema.properties.toolArguments;
  assert.deepEqual(noAddress(doc), []);
});

test("enrollment path cannot hide a replaced body or address header behind its exception", () => {
  const doc = structuredClone(base);
  doc.paths["/ai/custom-destinations"].post.requestBody.content["application/json"].schema = {
    type: "object", additionalProperties: false, properties: { endpoint: { type: "string" } },
  };
  assert.ok(noAddress(doc).some((finding) => finding.path.at(-1) === "endpoint"));
  const header = structuredClone(base);
  header.paths["/ai/custom-destinations"].post.parameters.push({ in: "header", name: "Host", schema: { type: "string" } });
  assert.ok(noAddress(header).some((finding) => finding.message.includes("forbidden in request parameters")));
});

test("namespaced registration is the only address exception, not a tag or arbitrary AI path", () => {
  const doc = withRequest({ $ref: "#/components/schemas/CustomDestinationRegistrationRequest" });
  doc.paths["/ai/inference"].post.tags = ["CustomDestinations"];
  assert.ok(noAddress(doc).some((finding) => finding.path.includes("host")));
  const unrelated = structuredClone(base);
  unrelated.paths["/profile"] = { post: { requestBody: { content: { "application/json": { schema: {
    type: "object", properties: { homepage: { type: "string", format: "uri" } },
  } } } } } };
  assert.deepEqual(noAddress(unrelated), []);
});

test("step-up-request@1 finite vectors bind the complete unnormalized body and target", () => {
  const vectors = JSON.parse(fs.readFileSync(path.join(ROOT, "spec", "fixtures", "custom-destination-step-up.v1.json"), "utf8"));
  assert.equal(vectors.version, "step-up-request@1");
  const binding = base["x-step-up-request-binding"];
  assert.equal(binding.version, vectors.version);
  assert.equal(binding.normalizeBeforeDigest, false);
  assert.equal(binding.insertDefaults, false);
  assert.equal(binding.preserveOmittedFields, true);
  assert.deepEqual(binding.reject, ["unknown-fields", "duplicate-JSON-names", "non-IJSON-values", "unknown-query-parameters"]);
  assert.equal(binding.freshnessSeconds, 300);
  assert.equal(binding.singleUse, "atomic");
  const digests = new Set();
  for (const vector of vectors.vectors) {
    // These fixed ASCII bytes are reviewed JCS examples, not a replacement canonicalizer.
    assert.match(vector.canonical, /^[\x20-\x7e]+$/);
    const envelope = JSON.parse(vector.canonical);
    assert.deepEqual(Object.keys(envelope), ["body", "method", "operation", "target", "version"]);
    assert.deepEqual(Object.keys(envelope.body), Object.keys(envelope.body).sort());
    assert.equal(envelope.version, vectors.version);
    assert.equal(envelope.method, "POST");
    const registration = envelope.operation === "registerCustomDestination";
    assert.ok(registration || envelope.operation === "activateCustomDestination");
    assert.deepEqual(Object.keys(envelope.target), registration ? [] : ["destinationId"]);
    assert.deepEqual(model(registration ? "CustomDestinationRegistrationRequest" : "CustomDestinationLifecycleRequest", envelope.body), []);
    assert.equal(createHash("sha256").update(vector.canonical, "utf8").digest("hex"), vector.sha256, vector.name);
    assert.equal(createHash("sha256").update(vector.canonical, "utf8").digest("base64url"), vector.rq, vector.name);
    assert.match(vector.rq, /^[A-Za-z0-9_-]{43}$/);
    digests.add(vector.rq);
  }
  assert.equal(digests.size, vectors.vectors.length);
  assert.equal(vectors.vectors.length, 11);
});

test("DPoP proof shape and opaque step-up do not pretend to verify credentials", () => {
  assert.deepEqual(model("DPoPProofValue", "a.b.c"), []);
  for (const invalid of ["", "a.b", "a.b.c=", "a..c", "a.b.c.d", "a.b.c\n"]) {
    assert.ok(model("DPoPProofValue", invalid).length > 0);
  }
  assert.deepEqual(model("StepUpTokenValue", "opaque-not-jwt"), []);
  assert.ok(model("StepUpTokenValue", "").length > 0);
  assert.equal(base["x-dpop-protocol"].apiKeyPrefix, "unset");
  assert.equal(base["x-dpop-protocol"].iatWindowSeconds, 60);
  assert.equal(base["x-dpop-protocol"].jtiReplayCacheSeconds, 300);
});
