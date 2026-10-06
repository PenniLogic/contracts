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
const { SCHEMA_ANNOTATIONS } = require("../../spec/spectral-functions/_shared");
const { SOURCE, loadSource } = require("../../spec/spectral-functions/_customDestinationSource");
const { compile } = require("../provider_constraints.cjs");

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
  doc.paths["/ai/inference"] = { post: {
    operationId: "inferenceLintProbe", description: "Synthetic source-admission control only.",
    tags: ["CustomDestinations"],
    parameters: [{ $ref: "#/components/parameters/DPoPProof" }, { $ref: "#/components/parameters/IdempotencyKey" }],
    requestBody: { required: true, content: { "application/json": { schema: shape } } },
    responses: { "204": { description: "Synthetic response only." } },
  } };
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

test("real Spectral CLI finds all four copied enums under example/examples/default field and component names", () => {
  withLintProbe((lint) => {
    const doc = structuredClone(base);
    const expected = [];
    for (const member of ["example", "examples", "default"]) {
      const properties = {};
      for (const [name, values] of Object.entries(consequence.enums)) {
        properties[name] = { type: "object", additionalProperties: false, required: [member], properties: {
          [member]: { type: "string", enum: ["synthetic_extra", ...[...values].reverse()] },
        } };
        expected.push(["components", "schemas", member, "properties", name, "properties", member, "enum"]);
      }
      doc.components.schemas[member] = {
        type: "object", "x-pennilogic-strict-provider": true, additionalProperties: false,
        required: Object.keys(properties), properties,
      };
      doc.components.schemas[`ReferenceTo${member}`] = { $ref: `#/components/schemas/${member}` };
    }
    assert.doesNotThrow(() => compile(doc));
    assert.deepEqual(findings(doc).map((finding) => finding.path), expected);
    const result = lint(doc);
    assert.equal(result.status, 1, result.stdout + result.stderr);
    for (const location of expected) assert.ok(result.stderr.split(/\r?\n/).some((line) =>
      line.includes("[pl-custom-destination-contract]") && line.includes(`(${location.join(".")})`) &&
      line.includes("duplicate") && line.includes("use $ref")), result.stdout + result.stderr);
  });
});

test("real Spectral CLI traverses schema maps, nested arrays and every schema applicator without treating names as annotations", () => {
  withLintProbe((lint) => {
    const doc = structuredClone(base);
    const copy = () => ({ type: "string", enum: [...consequence.enums.CredentialHeader, "synthetic_extra"] });
    const shape = { type: "object", properties: {
      examples: { type: "array", items: { allOf: [copy()] } },
    } };
    const suffixes = [["properties", "examples", "items", "allOf", "0", "enum"]];
    for (const keyword of ["patternProperties", "dependentSchemas", "$defs"]) {
      shape[keyword] = { examples: copy() };
      suffixes.push([keyword, "examples", "enum"]);
    }
    for (const keyword of ["allOf", "anyOf", "oneOf", "prefixItems"]) {
      shape[keyword] = [copy()];
      suffixes.push([keyword, "0", "enum"]);
    }
    for (const keyword of ["additionalProperties", "unevaluatedProperties", "unevaluatedItems", "contains",
      "propertyNames", "not", "if", "then", "else", "contentSchema"]) {
      shape[keyword] = copy();
      suffixes.push([keyword, "enum"]);
    }
    doc.components.schemas.SchemaPositions = shape;
    const expected = suffixes.map((suffix) => ["components", "schemas", "SchemaPositions", ...suffix].join(".")).sort();
    assert.deepEqual(findings(doc).map((finding) => finding.path.join(".")).sort(), expected);
    const result = lint(doc);
    assert.equal(result.status, 1, result.stdout + result.stderr);
    for (const location of expected) assert.ok(result.stderr.split(/\r?\n/).some((line) =>
      line.includes("[pl-custom-destination-contract]") && line.includes(`(${location})`) &&
      line.includes("duplicate CredentialHeader enumeration")), result.stdout + result.stderr);
  });
});

test("real Spectral CLI visits OpenAPI schema containers including named components, headers, callbacks and webhooks", () => {
  withLintProbe((lint) => {
    const doc = structuredClone(base);
    const copy = () => ({ type: "string", enum: [...consequence.enums.CredentialHeader, "synthetic_extra"] });
    const parameter = () => ({ name: "selection", in: "query", description: "Synthetic enum control.", schema: copy() });
    const body = () => ({ content: { "application/json": { schema: copy() } } });
    const operation = (id) => ({
      operationId: id, description: "Synthetic enum control.", tags: ["CustomDestinations"],
      parameters: [{ $ref: "#/components/parameters/DPoPProof" },
        { $ref: "#/components/parameters/IdempotencyKey" }, parameter()],
      requestBody: body(), responses: { "200": { description: "Synthetic response.", ...body() } },
    });
    const callbackPath = "{$request.query.callback}";
    doc.components.parameters.examples = parameter();
    doc.components.headers.default = { description: "Synthetic header.", schema: copy() };
    doc.components.requestBodies.example = body();
    doc.components.responses = { ...doc.components.responses, examples: {
      description: "Synthetic response.", ...body(), headers: { default: { schema: copy() } },
    } };
    doc.components.pathItems = { examples: { post: operation("componentEnumProbe") } };
    doc.components.callbacks = { default: { [callbackPath]: { post: operation("componentCallbackEnumProbe") } } };
    doc.paths["/enum-copy"] = { parameters: [parameter()], post: operation("pathEnumProbe") };
    doc.paths["/enum-copy"].post.callbacks = { examples: { [callbackPath]: { post: operation("callbackEnumProbe") } } };
    doc.paths["/enum-copy"].post.responses.default = { description: "Synthetic fallback.", ...body() };
    doc.webhooks = { examples: { post: operation("webhookEnumProbe") } };
    doc.components.requestBodies.encoding = { content: { "multipart/form-data": {
      schema: { type: "object", properties: { examples: { type: "string" } } },
      encoding: { examples: { headers: { default: { schema: copy() } } } },
    } } };
    const expected = [
      "components.parameters.examples.schema.enum", "components.headers.default.schema.enum",
      "components.requestBodies.example.content.application/json.schema.enum",
      "components.responses.examples.content.application/json.schema.enum",
      "components.responses.examples.headers.default.schema.enum",
      "components.requestBodies.encoding.content.multipart/form-data.encoding.examples.headers.default.schema.enum",
      "paths./enum-copy.parameters.0.schema.enum",
      "paths./enum-copy.post.responses.default.content.application/json.schema.enum",
    ];
    for (const prefix of [
      "components.pathItems.examples.post", `components.callbacks.default.${callbackPath}.post`,
      "paths./enum-copy.post", `paths./enum-copy.post.callbacks.examples.${callbackPath}.post`, "webhooks.examples.post",
    ]) {
      expected.push(`${prefix}.parameters.2.schema.enum`, `${prefix}.requestBody.content.application/json.schema.enum`,
        `${prefix}.responses.200.content.application/json.schema.enum`);
    }
    assert.deepEqual(findings(doc).map((finding) => finding.path.join(".")).sort(), expected.sort());
    const result = lint(doc);
    assert.equal(result.status, 1, result.stdout + result.stderr);
    for (const location of expected) assert.ok(result.stderr.split(/\r?\n/).some((line) =>
      line.includes("[pl-custom-destination-contract]") && line.includes(`(${location})`) &&
      line.includes("duplicate CredentialHeader enumeration")), result.stdout + result.stderr);
  });
});

test("real Spectral CLI preserves genuine annotation and example data, canonical refs and partial-overlap fields", () => {
  withLintProbe((lint) => {
    const doc = structuredClone(base);
    const data = { enum: [...consequence.enums.CredentialHeader] };
    const shape = { type: "object", additionalProperties: false, required: ["enum"], properties: {
      enum: { type: "array", items: { type: "string" } },
    }, example: data, examples: [data], default: data, const: data, enum: [data],
    "x-synthetic-data": { schema: { enum: data.enum }, properties: { examples: { enum: data.enum } } } };
    doc.components.schemas.AnnotationPayload = shape;
    doc.components.examples = { example: { value: data }, examples: { value: data }, default: { value: data } };
    doc.components.responses = { ...doc.components.responses, example: { description: "Synthetic data response.",
      content: { "application/json": { schema: shape, example: data, examples: { default: { value: data } } } },
    } };
    doc.components.schemas.examples = { type: "object", properties: {} };
    for (const [name, values] of Object.entries(consequence.enums)) {
      doc.components.schemas.examples.properties[name] = { type: "object", properties: {
        examples: { $ref: `#/components/schemas/${name}` },
        default: { type: "string", enum: values.slice(1) },
        example: { type: "string", enum: [...values.slice(1), "synthetic_extra", "synthetic_other"] },
      } };
    }
    doc.paths["x-synthetic-data"] = { post: {
      operationId: "dataTemplate", description: "Schema-shaped extension data.", tags: ["CustomDestinations"],
      parameters: [{ $ref: "#/components/parameters/IdempotencyKey" }],
      requestBody: { content: { "application/json": { schema: { enum: data.enum } } } },
      responses: { "204": { description: "Schema-shaped data only." } },
    } };
    doc["x-synthetic-data"] = { components: { schemas: { example: { enum: data.enum } } } };
    assert.deepEqual(findings(doc), []);
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

test("real Spectral CLI refuses enrollment address provenance through benign aliases, compositions and nested arrays", () => {
  withLintProbe((lint) => {
    const host = base.components.schemas.CustomDestinationRegistrationRequest.properties.host;
    const prefix = base.components.schemas.CustomDestinationRegistrationRequest.properties.pathPrefix;
    const doc = withRequest({ type: "object", additionalProperties: false, properties: {
      target: structuredClone(host),
      segment: structuredClone(prefix),
      chain: { $ref: "#/components/schemas/ScalarAliasFirst" },
      combined: { allOf: [structuredClone(host), { type: "string" }] },
      alternatives: { anyOf: [structuredClone(prefix), { type: "null" }] },
      choice: { oneOf: [structuredClone(host), { type: "null" }] },
      entries: { type: "array", items: { type: "object", additionalProperties: false, properties: {
        value: structuredClone(host),
      } } },
      tuple: { type: "array", items: false, prefixItems: [structuredClone(prefix)] },
      sibling: { $ref: "#/components/schemas/PlainText", allOf: [structuredClone(host)] },
    } });
    doc.components.schemas.ScalarAliasFirst = { $ref: "#/components/schemas/ScalarAlias~1Second~0" };
    doc.components.schemas["ScalarAlias/Second~"] = { $ref: "#/components/schemas/%43ustomDestinationHost" };
    doc.components.schemas.PlainText = { type: "string" };
    const result = lint(doc);
    assert.equal(result.status, 1, result.stdout + result.stderr);
    const lines = result.stderr.split(/\r?\n/);
    for (const suffix of [
      "properties.target.$ref", "properties.segment.$ref", "ScalarAlias/Second~.$ref",
      "properties.combined.allOf.0.$ref", "properties.alternatives.anyOf.0.$ref",
      "properties.choice.oneOf.0.$ref", "properties.entries.items.properties.value.$ref",
      "properties.tuple.prefixItems.0.$ref", "properties.sibling.allOf.0.$ref",
    ]) {
      assert.ok(lines.some((line) => line.includes("[pl-no-inference-address]") &&
        line.includes(`${suffix})`) && line.includes("registration-only") &&
        line.includes("destinationId/custom_model_id")), `${suffix}: ${result.stdout}${result.stderr}`);
    }
  });
});

test("real Spectral CLI refuses explicit URI, IRI, host and IP formats under innocuous field names", () => {
  withLintProbe((lint) => {
    const formats = ["uri", "uri-reference", "uri-template", "iri", "iri-reference",
      "url", "hostname", "idn-hostname", "ipv4", "ipv6"];
    const doc = withRequest({ type: "object", additionalProperties: false, properties: {} });
    formats.forEach((format, index) => {
      doc.components.schemas[`FormattedScalar${index}`] = { type: "string", format };
      doc.paths["/ai/inference"].post.requestBody.content["application/json"].schema.properties[`value${index}`] = {
        allOf: [{ $ref: `#/components/schemas/FormattedScalar${index}` }],
      };
    });
    const result = lint(doc);
    assert.equal(result.status, 1, result.stdout + result.stderr);
    const lines = result.stderr.split(/\r?\n/);
    formats.forEach((format, index) => assert.ok(lines.some((line) =>
      line.includes("[pl-no-inference-address]") && line.includes(`(components.schemas.FormattedScalar${index}.format)`) &&
      line.includes(`address-bearing format "${format}"`) && line.includes("destinationId/custom_model_id")),
    `${format}: ${result.stdout}${result.stderr}`));
  });
});

test("real Spectral CLI keeps prompt URL text, ordinary aliases, identifiers and all six enrollment operations", () => {
  withLintProbe((lint) => {
    const doc = withRequest({ type: "object", additionalProperties: false, properties: {
      prompt: { $ref: "#/components/schemas/CustomDestinationHostLookingText" },
      destinationId: { $ref: "#/components/schemas/CustomDestinationId" },
      custom_model_id: { $ref: "#/components/schemas/CustomDestinationModelId" },
      moment: { type: "string", format: "date-time" },
      day: { type: "string", format: "date" },
      entries: { type: "array", items: { $ref: "#/components/schemas/OrdinaryTextAlias" } },
    } });
    doc.components.schemas.CustomDestinationHostLookingText = {
      type: "string", examples: ["Explain https://content.example/path and /v1 as text, not a destination."],
    };
    doc.components.schemas.OrdinaryTextAlias = { allOf: [{ $ref: "#/components/schemas/CustomDestinationHostLookingText" }] };
    for (const [route, item] of Object.entries(base.paths)) assert.deepEqual(doc.paths[route], item);
    assert.deepEqual(noAddress(doc), []);
    const result = lint(doc);
    assert.equal(result.status, 0, result.stdout + result.stderr);
  });
});

test("enrollment scalar provenance follows its declared role rather than a fixed component name or shared primitive base", () => {
  const doc = withRequest({ type: "object", additionalProperties: false, properties: {} });
  const properties = doc.paths["/ai/inference"].post.requestBody.content["application/json"].schema.properties;
  const registration = doc.components.schemas.CustomDestinationRegistrationRequest.properties;
  for (const field of ["host", "pathPrefix"]) {
    doc.components.schemas[`Relocated${field}`] = structuredClone(base.components.schemas[
      registration[field].$ref.split("/").at(-1)]);
    doc.components.schemas[`RoleAlias${field}`] = { $ref: `#/components/schemas/Relocated${field}`, description: "Synthetic alias." };
    registration[field] = { $ref: `#/components/schemas/RoleAlias${field}` };
    properties[`value${field}`] = { $ref: `#/components/schemas/Relocated${field}` };
    assert.ok(noAddress(doc).some((finding) => finding.message.includes(`registration-only ${field} address schema`)));
  }
  doc.components.schemas.GeneralText = { type: "string" };
  registration.host = { $ref: "#/components/schemas/GeneralText", pattern: base.components.schemas.CustomDestinationHost.pattern };
  registration.pathPrefix = { $ref: "#/components/schemas/GeneralText", pattern: base.components.schemas.CustomDestinationPathPrefix.pattern };
  for (const key of Object.keys(properties)) delete properties[key];
  properties.prompt = { $ref: "#/components/schemas/GeneralText" };
  assert.deepEqual(noAddress(doc), []);
  properties.target = { $ref: "#/components/schemas/CustomDestinationRegistrationRequest/properties/host" };
  assert.ok(noAddress(doc).some((finding) => finding.path.at(-1) === "$ref" &&
    finding.message.includes("registration-only host address schema")));
});

test("real Spectral CLI preserves enrollment roles through each compiler-supported annotation on direct and intermediate refs", () => {
  withLintProbe((lint) => {
    const annotations = {
      title: "Synthetic scalar", description: "Synthetic scalar.", default: null,
      example: null, examples: null, deprecated: true, "x-pennilogic-strict-provider": false,
      "x-pennilogic-provider-validator": "synthetic", "x-not-money": true, "x-state-denials": {},
    };
    assert.deepEqual([...SCHEMA_ANNOTATIONS], Object.keys(annotations));
    for (const [annotation, value] of Object.entries(annotations)) {
      for (const intermediate of [false, true]) {
        const doc = withRequest({ type: "object", additionalProperties: false, properties: {
          target: { $ref: "#/components/schemas/CustomDestinationHost" },
          segment: { $ref: "#/components/schemas/CustomDestinationPathPrefix" },
        } });
        const registration = doc.components.schemas.CustomDestinationRegistrationRequest.properties;
        for (const field of ["host", "pathPrefix"]) {
          if (intermediate) {
            doc.components.schemas[`EnrollmentAlias${field}`] = structuredClone(registration[field]);
            registration[field] = { $ref: `#/components/schemas/EnrollmentAlias${field}` };
          }
        }
        const control = compile(doc);
        for (const field of ["host", "pathPrefix"]) {
          const target = intermediate ? doc.components.schemas[`EnrollmentAlias${field}`] : registration[field];
          const example = field === "host" ? "synthetic.example" : "/v1";
          target[annotation] = annotation === "example" ? example : annotation === "examples" ? [example] : value;
        }
        const compiled = compile(doc);
        assert.deepEqual(compiled.schemas, control.schemas, annotation);
        assert.deepEqual(compiled.roots, control.roots, annotation);
        assert.equal(noAddress(doc).length, 2, `${annotation}/${intermediate}`);
        const result = lint(doc);
        assert.equal(result.status, 1, result.stdout + result.stderr);
        for (const [member, role] of [["target", "host"], ["segment", "pathPrefix"]]) {
          assert.ok(result.stderr.split(/\r?\n/).some((line) =>
            line.includes("[pl-no-inference-address]") && line.includes(`properties.${member}.$ref)`) &&
            line.includes(`registration-only ${role} address schema`) && line.includes("destinationId/custom_model_id")),
          `${annotation}/${intermediate}: ${result.stdout}${result.stderr}`);
        }
      }
    }
  });
});

test("supported annotations do not taint constrained general-text bases or widen the compiler vocabulary", () => {
  withLintProbe((lint) => {
    const doc = withRequest({ type: "object", additionalProperties: false, properties: {
      prompt: { $ref: "#/components/schemas/GeneralText" },
    } });
    doc.components.schemas.GeneralText = {
      type: "string", examples: ["Discuss https://content.example/v1 as content, not routing."],
    };
    const registration = doc.components.schemas.CustomDestinationRegistrationRequest.properties;
    for (const field of ["host", "pathPrefix"]) {
      registration[field] = {
        $ref: "#/components/schemas/GeneralText",
        pattern: base.components.schemas[field === "host" ? "CustomDestinationHost" : "CustomDestinationPathPrefix"].pattern,
        description: "Constrained registration role, not a general-text alias.", "x-not-money": true,
      };
    }
    assert.deepEqual(noAddress(doc), []);
    assert.doesNotThrow(() => compile(doc));
    const result = lint(doc);
    assert.equal(result.status, 0, result.stdout + result.stderr);
    for (const annotation of ["summary", "readOnly", "writeOnly", "externalDocs", "x-unknown-annotation"]) {
      const altered = structuredClone(base);
      altered.components.schemas.CustomDestinationRegistrationRequest.properties.host[annotation] = true;
      assert.throws(() => compile(altered), /provider constraint generation rejected: unsupported keyword/, annotation);
    }
    const summary = withRequest({ type: "object", additionalProperties: false, properties: {
      target: { $ref: "#/components/schemas/CustomDestinationHost" },
    } });
    summary.components.schemas.CustomDestinationRegistrationRequest.properties.host.summary = "Existing lint-only annotation.";
    assert.equal(noAddress(summary).length, 1);
    const nonnullDefault = structuredClone(base);
    nonnullDefault.components.schemas.CustomDestinationRegistrationRequest.properties.host.default = "synthetic.example";
    assert.throws(() => compile(nonnullDefault), /provider constraint generation rejected: unsupported default/);
  });
});

test("real Spectral CLI preserves registration roles through implied types, equal or looser bounds and scalar conjunctions", () => {
  withLintProbe((lint) => {
    const wrappers = [
      (ref) => ({ ...ref, type: "string" }),
      (ref) => ({ ...ref, minLength: 0 }),
      (ref, target) => ({ ...ref, type: "string", minLength: target.minLength, maxLength: target.maxLength }),
      (ref, target) => ({ ...ref, minLength: 0, maxLength: target.maxLength + 10, pattern: target.pattern }),
      (ref) => ({ type: "string", allOf: [ref] }),
      (ref, target) => ({ type: "string", allOf: [{ minLength: 0 }, ref, { maxLength: target.maxLength + 10 }] }),
    ];
    for (const wrap of wrappers) {
      const doc = withRequest({ type: "object", additionalProperties: false, properties: {
        selector: { $ref: "#/components/schemas/CustomDestinationHost" },
        segment: { $ref: "#/components/schemas/CustomDestinationPathPrefix" },
      } });
      const registration = doc.components.schemas.CustomDestinationRegistrationRequest.properties;
      for (const field of ["host", "pathPrefix"]) {
        const ref = structuredClone(registration[field]);
        const target = doc.components.schemas[ref.$ref.split("/").at(-1)];
        registration[field] = wrap(ref, target);
        const shape = { ...registration[field], components: doc.components };
        assert.deepEqual(validate(field === "host" ? "outside.example" : "/v1", shape), []);
        assert.ok(validate(field === "host" ? "bad host with spaces" : "https://outside.example", shape).length > 0);
      }
      doc.components.schemas.ScalarAliasArguments = {
        ...doc.paths["/ai/inference"].post.requestBody.content["application/json"].schema,
        "x-pennilogic-strict-provider": true,
      };
      const compiled = compile(doc);
      assert.ok(compiled.roots.includes("ScalarAliasArguments"));
      assert.ok(compiled.schemas.CustomDestinationHost);
      assert.ok(compiled.schemas.CustomDestinationPathPrefix);
      assert.equal(noAddress(doc).length, 2);
      const result = lint(doc);
      assert.equal(result.status, 1, result.stdout + result.stderr);
      for (const [member, field] of [["selector", "host"], ["segment", "pathPrefix"]]) {
        assert.ok(result.stderr.split(/\r?\n/).some((line) => line.includes("[pl-no-inference-address]") &&
          line.includes(`properties.${member}.$ref)`) && line.includes(`registration-only ${field} address schema`) &&
          line.includes("destinationId/custom_model_id")), result.stdout + result.stderr);
      }
    }
  });
});

test("real Spectral CLI retains renamed multi-hop enrollment roles at every equivalent intermediate reference", () => {
  withLintProbe((lint) => {
    const doc = withRequest({ type: "object", additionalProperties: false, properties: {} });
    const properties = doc.paths["/ai/inference"].post.requestBody.content["application/json"].schema.properties;
    for (const [index, field] of ["host", "pathPrefix"].entries()) {
      const registration = doc.components.schemas.CustomDestinationRegistrationRequest.properties;
      const target = structuredClone(doc.components.schemas[registration[field].$ref.split("/").at(-1)]);
      const names = ["RenamedScalar", "FirstScalar", "SecondScalar", "ThirdScalar"].map((name) => `${name}${index}`);
      doc.components.schemas[names[0]] = target;
      doc.components.schemas[names[1]] = {
        $ref: `#/components/schemas/${names[0]}`, type: "string", "x-not-money": true,
      };
      doc.components.schemas[names[2]] = {
        type: "string", allOf: [{ $ref: `#/components/schemas/${names[1]}` }, { minLength: 0 }],
      };
      doc.components.schemas[names[3]] = {
        $ref: `#/components/schemas/${names[2]}`, minLength: target.minLength, maxLength: target.maxLength + 1,
      };
      registration[field] = { type: "string", allOf: [{ $ref: `#/components/schemas/${names[3]}` }] };
      names.forEach((name, hop) => { properties[`value${index}${hop}`] = { $ref: `#/components/schemas/${name}` }; });
      properties[`nested${index}`] = { type: "array", items: { type: "object", additionalProperties: false,
        properties: { value: { $ref: `#/components/schemas/${names[0]}` } } } };
    }
    doc.components.schemas.ScalarAliasArguments = {
      ...doc.paths["/ai/inference"].post.requestBody.content["application/json"].schema,
      "x-pennilogic-strict-provider": true,
    };
    assert.doesNotThrow(() => compile(doc));
    const result = lint(doc);
    assert.equal(result.status, 1, result.stdout + result.stderr);
    for (const index of [0, 1]) {
      for (const hop of [0, 1, 2, 3]) assert.ok(result.stderr.split(/\r?\n/).some((line) =>
        line.includes("[pl-no-inference-address]") && line.includes(`properties.value${index}${hop}.$ref)`) &&
        line.includes(`registration-only ${index === 0 ? "host" : "pathPrefix"} address schema`)),
      result.stdout + result.stderr);
      assert.ok(result.stderr.includes(`properties.nested${index}.items.properties.value.$ref)`));
    }
  });
});

test("real Spectral CLI distinguishes genuinely narrowed general text from its equivalent registration wrappers", () => {
  withLintProbe((lint) => {
    const doc = withRequest({ type: "object", additionalProperties: false, properties: {
      prompt: { $ref: "#/components/schemas/SharedText" },
    } });
    doc.components.schemas.SharedText = { type: "string", minLength: 0, "x-not-money": true };
    for (const field of ["host", "pathPrefix"]) {
      const registration = doc.components.schemas.CustomDestinationRegistrationRequest.properties;
      const source = doc.components.schemas[registration[field].$ref.split("/").at(-1)];
      doc.components.schemas[`Constrained${field}`] = {
        type: "string", allOf: [{ $ref: "#/components/schemas/SharedText" }, { pattern: source.pattern }],
      };
      registration[field] = { $ref: `#/components/schemas/Constrained${field}`, type: "string", minLength: 0 };
    }
    assert.doesNotThrow(() => compile(doc));
    assert.deepEqual(noAddress(doc), []);
    const positive = lint(doc);
    assert.equal(positive.status, 0, positive.stdout + positive.stderr);
    doc.paths["/ai/inference"].post.requestBody.content["application/json"].schema.properties.selection = {
      $ref: "#/components/schemas/Constrainedhost",
    };
    const negative = lint(doc);
    assert.equal(negative.status, 1, negative.stdout + negative.stderr);
    assert.match(negative.stderr, /registration-only host address schema/);
    assert.deepEqual(validate("Discuss https://content.example/v1 as ordinary text.",
      { $ref: "#/components/schemas/SharedText", components: doc.components }), []);
  });
});

test("real Spectral CLI refuses unproved registration compositions rather than silently losing address provenance", () => {
  withLintProbe((lint) => {
    const wrappers = [
      (ref) => ({ type: "string", anyOf: [ref] }),
      (ref) => ({ type: "string", oneOf: [ref] }),
      (ref) => ({ type: "string", not: { not: ref } }),
      (ref) => ({ type: "string", if: { type: "string" }, then: ref }),
      (ref) => ({ ...ref, pattern: "^.*$" }),
    ];
    for (const wrap of wrappers) {
      const doc = withRequest({ type: "object", additionalProperties: false, properties: {
        selector: { $ref: "#/components/schemas/CustomDestinationHost" },
        segment: { $ref: "#/components/schemas/CustomDestinationPathPrefix" },
      } });
      const registration = doc.components.schemas.CustomDestinationRegistrationRequest.properties;
      for (const field of ["host", "pathPrefix"]) registration[field] = wrap(registration[field]);
      assert.doesNotThrow(() => compile(doc));
      const result = lint(doc);
      assert.equal(result.status, 1, result.stdout + result.stderr);
      assert.match(result.stderr, /\[pl-no-inference-address\].*registration address provenance is unproved/);
      assert.match(result.stderr, /destinationId\/custom_model_id/);
    }
  });
});

test("bounded alias proof preserves finite constraint identity and refuses malformed or ambiguous sources", () => {
  const doc = withRequest({ type: "object", additionalProperties: false, properties: {
    selector: { $ref: "#/components/schemas/FiniteScalar" },
  } });
  doc.components.schemas.FiniteScalar = { type: "string", enum: ["a", "b"], minLength: 1, maxLength: 1 };
  doc.components.schemas.CustomDestinationRegistrationRequest.properties.host = {
    $ref: "#/components/schemas/FiniteScalar", enum: ["b", "a", "c"], type: "string", minLength: 0, maxLength: 2,
  };
  assert.ok(noAddress(doc).some((finding) => finding.message.includes("registration-only host")));
  const constrained = structuredClone(doc);
  constrained.components.schemas.CustomDestinationRegistrationRequest.properties.host.enum = ["a"];
  assert.deepEqual(noAddress(constrained), []);
  for (const mutate of [
    (copy) => { copy.components.schemas.FiniteScalar.enum = ["a", "a"]; },
    (copy) => { copy.components.schemas.FiniteScalar.type = ["string", "null"]; },
    (copy) => { copy.components.schemas.FiniteScalar.minLength = 1.5; },
    (copy) => { copy.components.schemas.FiniteScalar.maxLength = 0; },
    (copy) => { copy.components.schemas.FiniteScalar.$ref = "#/components/schemas/FiniteScalar"; },
    (copy) => { copy.components.schemas.FiniteScalar.$dynamicRef = "#root"; },
    (copy) => { copy.components.schemas.FiniteScalar.$ref = "#/components/schemas/MissingScalar"; },
    (copy) => { copy.components.schemas.FiniteScalar.$ref = "https://invalid.example/schema"; },
    (copy) => { copy.components.schemas.FiniteScalar.allOf = []; },
    (copy) => { copy.components.schemas.FiniteScalar.allOf = Array.from({ length: 257 }, () => ({ type: "string" })); },
    (copy) => { copy.jsonSchemaDialect = "https://invalid.example/dialect"; },
  ]) {
    const altered = structuredClone(doc);
    mutate(altered);
    assert.ok(noAddress(altered).some((finding) => finding.message.includes("registration address provenance is unproved")));
  }
  for (const length of [63, 64]) {
    const long = structuredClone(doc);
    for (let index = 0; index < length; index += 1) long.components.schemas[`BoundedAlias${index}`] = {
      $ref: index === length - 1 ? "#/components/schemas/FiniteScalar" : `#/components/schemas/BoundedAlias${index + 1}`,
    };
    long.components.schemas.CustomDestinationRegistrationRequest.properties.host = { $ref: "#/components/schemas/BoundedAlias0" };
    assert.equal(noAddress(long).some((finding) => finding.message.includes("traversal bound")), length === 64);
  }
  const wide = structuredClone(doc);
  wide.components.schemas.ScalarLayer = {
    allOf: Array.from({ length: 128 }, () => ({ $ref: "#/components/schemas/FiniteScalar" })),
  };
  wide.components.schemas.CustomDestinationRegistrationRequest.properties.host = {
    type: "string", allOf: Array.from({ length: 128 }, () => ({ $ref: "#/components/schemas/ScalarLayer" })),
  };
  assert.ok(noAddress(wide).some((finding) => finding.message.includes("traversal bound")));
});

test("custom guards treat combined enum/example/default and literal ref payloads as data without certifying stock example resolution", () => {
  const data = {
    enum: [...consequence.enums.CredentialHeader], example: "example", default: "default",
    $ref: "#/components/schemas/CustomDestinationHost",
  };
  const shape = { type: "object", additionalProperties: false, properties: {
    enum: { type: "array", items: { type: "string" } },
    example: { type: "string" }, default: { type: "string" }, $ref: { type: "string" },
  }, examples: [data] };
  assert.deepEqual(validate(data, shape), []);
  const doc = withRequest(shape);
  assert.deepEqual(noAddress(doc), []);
  assert.deepEqual(findings(doc), []);
});

test("real Spectral CLI refuses negative composition through body refs, parameters, aliases, nested arrays and compositions", () => {
  withLintProbe((lint) => {
    const negative = (schema) => ({ type: "string", not: { not: schema } });
    const host = { $ref: "#/components/schemas/CustomDestinationHost" };
    const prefix = { $ref: "#/components/schemas/CustomDestinationPathPrefix" };
    for (const [shape, accepted, refused] of [
      [host, "outside.example", "bad host with spaces"],
      [prefix, "/v1", "https://outside.example"],
      [{ type: "string", format: "uri" }, "https://outside.example/v1", "not an absolute URI"],
    ]) {
      for (const candidate of [shape, negative(shape)]) {
        assert.deepEqual(validate(accepted, { ...candidate, components: base.components }), []);
        assert.ok(validate(refused, { ...candidate, components: base.components }).length > 0);
      }
    }
    const doc = withRequest({ type: "object", additionalProperties: false, properties: {
      selector: negative(host), segment: negative(prefix),
      referenced: { $ref: "#/components/schemas/NegativeScalarAlias" },
      entries: { type: "array", items: { type: "object", additionalProperties: false,
        properties: { value: negative(prefix) } } },
      values: { type: "array", items: negative(host) },
      combined: { allOf: [negative({ type: "string", format: "uri" })] },
      excluded: { type: "string", not: { not: { const: "synthetic-selected" } } },
      formatted: { type: "string", not: { format: "uri" } },
      reference: { type: "string", not: host },
      structured: { type: "string", not: { const: { selector: "synthetic.example" } } },
    } });
    doc.components.schemas.NegativeScalarAlias = negative(host);
    doc.components.requestBodies.ConstraintBody = doc.paths["/ai/inference"].post.requestBody;
    doc.paths["/ai/inference"].post.requestBody = { $ref: "#/components/requestBodies/ConstraintBody" };
    doc.paths["/ai/inference"].parameters = [
      { in: "query", name: "selector", description: "Synthetic negative.", schema: negative(host) },
    ];
    doc.paths["/ai/inference"].post.parameters.push({
      in: "header", name: "Selection", description: "Synthetic negative.", schema: negative(prefix),
    });
    const result = lint(doc);
    assert.equal(result.status, 1, result.stdout + result.stderr);
    const body = "components.requestBodies.ConstraintBody.content.application/json.schema.properties";
    const locations = [
      `${body}.selector.not`, `${body}.segment.not`, `${body}.entries.items.properties.value.not`,
      `${body}.values.items.not`, `${body}.combined.allOf.0.not`, `${body}.excluded.not`,
      `${body}.formatted.not`, `${body}.reference.not`, `${body}.structured.not`,
      "components.schemas.NegativeScalarAlias.not",
      "paths./ai/inference.parameters.0.schema.not", "paths./ai/inference.post.parameters.2.schema.not",
    ];
    assert.equal(noAddress(doc).length, locations.length);
    for (const location of locations) assert.ok(result.stderr.split(/\r?\n/).some((line) =>
      line.includes("[pl-no-inference-address]") && line.includes(`(${location})`) &&
      line.includes("negative (not) request schema composition beyond plain scalar exclusions is unsupported") &&
      line.includes("destinationId/custom_model_id")), result.stdout + result.stderr);
  });
});

test("real Spectral CLI keeps not/example/examples/default member names and schema-shaped annotation data as ordinary content", () => {
  withLintProbe((lint) => {
    const doc = withRequest({ type: "object", additionalProperties: false, properties: {
      prompt: { type: "string" },
      not: { type: "object", additionalProperties: false, properties: {
        not: { type: "object", additionalProperties: false, properties: { $ref: { type: "string" } } },
      } },
      example: { type: "string" }, examples: { type: "string" }, default: { type: "string" },
      limited: { type: "string", not: { const: "synthetic-excluded" } },
      patterned: { type: "string", not: { pattern: "^synthetic-excluded$" } },
      enumerated: { type: "string", not: { enum: ["synthetic-excluded"] } },
      typed: { type: "string", not: { type: "null" } },
      bounded: { type: "string", not: { minLength: 2, maxLength: 3, description: "Scalar-only exclusion." } },
      count: { type: "integer", not: { minimum: 1, maximum: 3, exclusiveMinimum: 0, exclusiveMaximum: 4, multipleOf: 1 } },
      enabled: { type: "boolean", not: { const: false } },
      destinationId: { $ref: "#/components/schemas/CustomDestinationId" },
      custom_model_id: { $ref: "#/components/schemas/CustomDestinationModelId" },
      selection: { $ref: "#/components/schemas/CredentialHeader" },
      partial: { type: "string", enum: consequence.enums.CredentialHeader.slice(1) },
    }, examples: [{
      prompt: "Explain https://content.example/v1 as text, not routing.",
      not: { not: { $ref: "#/components/schemas/CustomDestinationHost" } },
      example: "example", examples: "examples", default: "default",
    }] });
    assert.deepEqual(noAddress(doc), []);
    assert.deepEqual(findings(doc), []);
    for (const [route, item] of Object.entries(base.paths)) assert.deepEqual(doc.paths[route], item);
    const result = lint(doc);
    assert.equal(result.status, 0, result.stdout + result.stderr);
  });
});

test("real Spectral CLI refuses semantic address parameters and fake enrollment exceptions", () => {
  withLintProbe((lint) => {
    const doc = withRequest({ type: "object", additionalProperties: false, properties: {} });
    doc.paths["/ai/inference"].parameters = [
      { in: "query", name: "selector", description: "Synthetic negative.", schema: { $ref: "#/components/schemas/CustomDestinationHost" } },
      { in: "header", name: "Segment-Selector", description: "Synthetic negative.", schema: { type: "string", format: "uri-reference" } },
    ];
    const original = structuredClone(base.paths["/ai/custom-destinations"].post);
    doc.paths["/ai/custom-destinations"].post.requestBody.content["application/json"].schema = {
      type: "object", additionalProperties: false, properties: { target: { $ref: "#/components/schemas/CustomDestinationHost" } },
    };
    doc.paths["/ai/pretend-enrollment"] = { post: { ...original, operationId: "pretendEnrollment" } };
    const result = lint(doc);
    assert.equal(result.status, 1, result.stdout + result.stderr);
    const lines = result.stderr.split(/\r?\n/);
    for (const [location, origin] of [
      ["paths./ai/inference.parameters.0.schema.$ref", "POST /ai/inference"],
      ["paths./ai/inference.parameters.1.schema.format", "POST /ai/inference"],
      ["paths./ai/custom-destinations.post.requestBody.content.application/json.schema.properties.target.$ref", "POST /ai/custom-destinations"],
      ["components.schemas.CustomDestinationRegistrationRequest.properties.host.$ref", "POST /ai/pretend-enrollment"],
    ]) {
      assert.ok(lines.some((line) => line.includes("[pl-no-inference-address]") &&
        line.includes(`(${location})`) && line.includes(origin) &&
        /registration-only|address-bearing format/.test(line)), `${location}: ${result.stdout}${result.stderr}`);
    }
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
