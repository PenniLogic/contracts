"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const { Yaml } = require("@stoplight/spectral-parsers");
const { collectSchemaUsage, SchemaUsageError, schemaChildren } = require("../../spec/spectral-functions/_schemaUsage");
const { compile } = require("../provider_constraints.cjs");
const coreLint = require("../../spec/spectral-functions/coreEndpointContract");

const base = JSON.parse(JSON.stringify(Yaml.parse(fs.readFileSync(path.join(__dirname, "..", "..", "spec", "openapi.yaml"), "utf8")).data));
const ref = { $ref: "#/components/schemas/UsageReceipt" };
function specimen(shape) {
  const doc = structuredClone(base);
  doc.components.schemas.UsageReceipt = {
    type: "object", "x-pennilogic-strict-provider": true, additionalProperties: false,
    required: ["result"], properties: { result: { type: "string", readOnly: true } },
  };
  doc.paths["/usage-proof"] = {
    get: { operationId: "getUsageProof", tags: ["UsageProof"], responses: {
      "200": { description: "Synthetic response.", content: { "application/json": { schema: ref } } },
    } },
    post: { operationId: "postUsageProof", tags: ["UsageProof"], requestBody: {
      content: { "application/json": { schema: shape } },
    }, responses: { "204": { description: "Synthetic empty response." } } },
  };
  return doc;
}
function schema(doc) {
  return doc.paths["/usage-proof"].post.requestBody.content["application/json"];
}
function refusesMixed(doc, label) {
  const usage = collectSchemaUsage(doc);
  assert.equal(usage.request.has("UsageReceipt"), true, label);
  assert.throws(() => compile(doc), /unsupported keyword: readOnly model must be response-only/, label);
  assert.ok(coreLint(doc).some((finding) => finding.message.includes("readOnly response members")), label);
}

test("all evaluated schema-map, applicator and reference contexts share the request-use proof", () => {
  const shapes = [
    ref,
    { type: "object", additionalProperties: ref },
    { type: "object", additionalProperties: false, patternProperties: { "^snapshot$": ref } },
    { type: "object", dependentSchemas: { trigger: { properties: { snapshot: ref } } } },
    { type: "array", items: { type: "object", additionalProperties: ref } },
    { type: "array", items: false, prefixItems: [ref] },
    { type: "object", unevaluatedProperties: ref },
    { type: "array", unevaluatedItems: ref },
    { type: "array", contains: ref },
    { type: "object", propertyNames: ref },
    { allOf: [ref] }, { anyOf: [ref, { type: "null" }] }, { oneOf: [ref, { type: "null" }] },
    { not: ref },
    { type: "object", if: { required: ["trigger"] }, then: ref, else: { type: "object" } },
    { type: "object", if: { required: ["trigger"] }, then: { type: "object" }, else: ref },
    { type: "string", contentSchema: ref },
  ];
  shapes.forEach((shape, index) => refusesMixed(specimen(shape), index));
  const aliases = specimen({ $ref: "#/components/schemas/UseAlias" });
  aliases.components.schemas.UseAlias = { allOf: [{ $ref: "#/components/schemas/UseMap" }] };
  aliases.components.schemas.UseMap = { type: "object", additionalProperties: ref };
  refusesMixed(aliases, "renamed alias through composition and map");
  const definitions = specimen({ $ref: "#/components/schemas/UseDefinition/$defs/receipt" });
  definitions.components.schemas.UseDefinition = { $defs: { receipt: ref } };
  refusesMixed(definitions, "evaluated $defs reference");
  const names = specimen({ type: "object", properties: {
    examples: { type: "object", additionalProperties: ref },
    default: { type: "array", items: ref }, "x-natural-field": ref,
  } });
  refusesMixed(names, "schema-map member names are not annotation keywords");
  const mapped = specimen({ type: "object", discriminator: {
    propertyName: "kind", mapping: { "x-natural-tag": "#/components/schemas/UsageReceipt" },
  } });
  refusesMixed(mapped, "explicit discriminator reference context");
});

test("OpenAPI request contexts, headers, callbacks, webhooks and proper Path Item aliases cannot hide mixed use", () => {
  const cases = [];
  const parameter = specimen({ type: "string" });
  parameter.components.parameters.Usage = { name: "snapshot", in: "query", schema: { type: "object", additionalProperties: ref } };
  parameter.paths["/usage-proof"].post.parameters = [{ $ref: "#/components/parameters/Usage" }];
  cases.push(parameter);
  const content = specimen({ type: "string" });
  content.paths["/usage-proof"].post.parameters = [{ name: "snapshot", in: "query",
    content: { "application/json": { schema: ref } } }];
  cases.push(content);
  const body = specimen({ type: "string" });
  body.components.requestBodies.Usage = { content: { "application/json": { schema: { type: "object", additionalProperties: ref } } } };
  body.paths["/usage-proof"].post.requestBody = { $ref: "#/components/requestBodies/Usage" };
  cases.push(body);
  const encoded = specimen({ type: "string" });
  encoded.paths["/usage-proof"].post.requestBody.content = { "multipart/form-data": {
    schema: { type: "object", properties: { caption: { type: "string" } } },
    encoding: { caption: { headers: { "x-snapshot": { schema: ref } } } },
  } };
  cases.push(encoded);
  const aliased = specimen({ type: "object", additionalProperties: ref });
  aliased.components.pathItems = { UsagePath: aliased.paths["/usage-proof"] };
  aliased.paths["/usage-proof"] = { $ref: "#/components/pathItems/UsagePath" };
  cases.push(aliased);
  const callback = specimen({ type: "string" });
  callback.components.callbacks = { Usage: {
    "{$request.query.callback}": { post: { operationId: "callbackUsage", requestBody: {
      content: { "application/json": { schema: ref } },
    }, responses: { "204": { description: "Synthetic callback response." } } } },
  } };
  callback.paths["/usage-proof"].get.callbacks = { signal: { $ref: "#/components/callbacks/Usage" } };
  cases.push(callback);
  const namedCallback = specimen({ type: "string" });
  namedCallback.paths["/usage-proof"].get.callbacks = { labels: {
    summary: { post: { operationId: "literalCallback", requestBody: {
      content: { "application/json": { schema: ref } },
    }, responses: { "204": { description: "Synthetic relative callback response." } } } },
  } };
  cases.push(namedCallback);
  const webhook = specimen({ type: "string" });
  webhook.webhooks = { signal: { post: { operationId: "webhookUsage", requestBody: {
    content: { "application/json": { schema: ref } },
  }, responses: { "204": { description: "Synthetic webhook response." } } } } };
  cases.push(webhook);
  cases.forEach((doc, index) => refusesMixed(doc, index));
});

test("genuine annotation and literal data do not become usages, while response-only and ordinary map controls remain positive", () => {
  const payload = { $ref: "#/components/schemas/UsageReceipt", readOnly: true,
    additionalProperties: ref, patternProperties: { ".*": ref }, dependentSchemas: { trigger: ref } };
  const doc = specimen({ type: "object", additionalProperties: false, properties: {
    examples: { type: "string" }, default: { type: "string" }, schema: { type: "string" },
    readOnly: { type: "boolean" }, additionalProperties: { type: "string" },
  }, example: payload, examples: [payload], default: payload, const: payload, enum: [payload],
  "x-annotation": payload, $comment: "Synthetic schema-looking literal data only." });
  const usage = collectSchemaUsage(doc);
  assert.equal(usage.request.has("UsageReceipt"), false);
  assert.equal(usage.response.has("UsageReceipt"), true);
  const projected = compile(doc).generation_input.components.schemas.UsageReceipt;
  assert.equal(projected["x-pennilogic-read-only-response"], true);
  const map = specimen({ type: "object", additionalProperties: { type: "string" } });
  assert.equal(collectSchemaUsage(map).request.has("UsageReceipt"), false);
  assert.doesNotThrow(() => compile(map));
  const unused = specimen({ type: "string", $defs: { unusedReceipt: ref } });
  assert.equal(collectSchemaUsage(unused).request.has("UsageReceipt"), false);
  assert.doesNotThrow(() => compile(unused));
  const response = specimen({ type: "string" });
  response.paths["/usage-proof"].get.responses["200"].headers = { "x-snapshot": { schema: ref } };
  assert.equal(collectSchemaUsage(response).request.has("UsageReceipt"), false);
  assert.doesNotThrow(() => compile(response));
  const aliases = specimen({ type: "string" });
  aliases.components.responses.Usage = aliases.paths["/usage-proof"].get.responses["200"];
  aliases.paths["/usage-proof"].get.responses["200"] = { $ref: "#/components/responses/Usage", description: "A genuine reference annotation." };
  assert.doesNotThrow(() => compile(aliases));
});

test("malformed, unsupported and wrong-context schemas/ref layouts refuse rather than certify absence", () => {
  for (const shape of [
    { type: "object", unknownClause: ref },
    { type: "object", dependencies: { trigger: ref } },
    { type: "object", patternProperties: [ref] },
    { type: "object", dependentSchemas: { trigger: 1 } },
    { type: "object", additionalProperties: null },
    { type: "array", prefixItems: {} },
    { type: "object", $dynamicRef: "#/components/schemas/UsageReceipt" },
    { type: "object", $id: "https://schema.example/" },
    { $ref: "https://schema.example/Receipt" },
    { $ref: "#/components/schemas/Missing" },
    { $ref: "#/components/schemas/__proto__" },
    { $ref: "#/components/schemas/UsageReceipt/example" },
    { $ref: "#/components/schemas/UsageReceipt/properties" },
    { $ref: "#/components/schemas/UsageReceipt%2fproperties%2fresult" },
    { $ref: "#/components/schemas/UsageReceipt/~broken" },
    { $ref: "#/components/responses/Usage" },
  ]) {
    const doc = specimen(shape);
    assert.throws(() => collectSchemaUsage(doc), SchemaUsageError);
    assert.throws(() => compile(doc), /provider constraint generation rejected: unproved readOnly usage/);
    assert.ok(coreLint(doc).some((finding) => finding.message.includes("usage is unproved")));
  }
  const cyclic = specimen({ $ref: "#/components/schemas/UseFirst" });
  cyclic.components.schemas.UseFirst = { $ref: "#/components/schemas/UseSecond" };
  cyclic.components.schemas.UseSecond = { $ref: "#/components/schemas/UseFirst" };
  assert.throws(() => compile(cyclic), /cyclic schema usage reference/);
  assert.equal(collectSchemaUsage(cyclic, { allowSchemaCycles: true }).request.has("UsageReceipt"), false);
  assert.deepEqual(coreLint(cyclic), []);
  cyclic.components.schemas.UseFirst.properties = { snapshot: ref };
  assert.ok(coreLint(cyclic).some((finding) => finding.message.includes("readOnly response members")));
  const siblings = specimen({ type: "string" });
  siblings.components.pathItems = { UsagePath: siblings.paths["/usage-proof"] };
  siblings.paths["/usage-proof"] = { $ref: "#/components/pathItems/UsagePath", post: { requestBody: { content: { "application/json": { schema: ref } } } } };
  assert.throws(() => compile(siblings), /ambiguous OpenAPI usage reference siblings/);
  const pathCycle = specimen({ type: "string" });
  pathCycle.components.pathItems = { A: { $ref: "#/components/pathItems/B" }, B: { $ref: "#/components/pathItems/A" } };
  pathCycle.paths["/usage-proof"] = { $ref: "#/components/pathItems/A" };
  assert.throws(() => compile(pathCycle), /cyclic schema usage reference/);
  const wrongOperation = specimen({ type: "string" });
  wrongOperation.paths["/usage-proof"].post.$ref = "#/components/pathItems/Operation";
  assert.throws(() => compile(wrongOperation), /unsupported operation usage field/);
});

test("work, depth and cached-reference height budgets remain explicit refusals", () => {
  function nested(depth, leaf = { type: "string" }) {
    let result = leaf;
    for (let index = 0; index < depth; index++) result = { type: "array", items: result };
    return result;
  }
  assert.doesNotThrow(() => collectSchemaUsage(specimen(nested(58))));
  assert.throws(() => collectSchemaUsage(specimen(nested(65))), /schema usage traversal bound/);
  const broad = specimen({ type: "object", properties: Object.fromEntries(
    Array.from({ length: 16384 }, (_, index) => ["field" + index, { type: "string" }])) });
  assert.throws(() => collectSchemaUsage(broad), /schema usage traversal bound/);
  const cached = specimen({ type: "object", properties: {
    first: { $ref: "#/components/schemas/DepthLeaf" },
    later: nested(53, { $ref: "#/components/schemas/DepthLeaf" }),
  } });
  cached.components.schemas.DepthLeaf = nested(12);
  assert.throws(() => collectSchemaUsage(cached), /cached schema usage depth bound/);
  const refDepth = specimen({ $ref: "#/components/schemas/UseDepth0" });
  for (let index = 0; index < 70; index++) {
    refDepth.components.schemas["UseDepth" + index] = index === 69 ? { type: "string" } :
      { $ref: "#/components/schemas/UseDepth" + (index + 1) };
  }
  assert.throws(() => compile(refDepth), /schema usage traversal bound/);
});

test("shared physical schema edges preserve the old container/natural-name inventory exactly", () => {
  const child = { type: "string" };
  const node = { properties: { examples: child }, patternProperties: { default: child },
    dependentSchemas: { enum: child }, $defs: { schema: child },
    allOf: [child], anyOf: [child], oneOf: [child], prefixItems: [child],
    items: child, additionalProperties: child, unevaluatedProperties: child, unevaluatedItems: child,
    contains: child, propertyNames: child, not: child, if: child, then: child, else: child, contentSchema: child,
    example: { properties: { private: child } }, const: { $ref: "#/components/schemas/UsageReceipt" } };
  const locations = schemaChildren(node, []).map((entry) => entry.path.join("."));
  assert.equal(locations.length, 19);
  assert.ok(locations.includes("properties.examples"));
  assert.ok(locations.includes("patternProperties.default"));
  assert.ok(locations.includes("dependentSchemas.enum"));
  assert.ok(locations.includes("$defs.schema"));
  assert.ok(locations.includes("prefixItems.0"));
  assert.equal(locations.some((location) => location.startsWith("example.") || location.startsWith("const.")), false);
});
