"use strict";
const { isDeepStrictEqual } = require("node:util");
const { schema: validateSchema } = require("@stoplight/spectral-functions");
const { SOURCE, loadSource } = require("./_customDestinationSource");
const { HTTP_METHODS } = require("./_shared");

module.exports = function customDestinationContract(document, _options, context) {
  const results = [];
  const sourcePath = ["x-custom-destination-source"];
  function report(message, path) {
    results.push({ message: `ADR-022: ${message}`, path });
  }
  if (!isDeepStrictEqual(document["x-custom-destination-source"], SOURCE)) {
    report("x-custom-destination-source must bind the exact accepted 1.1.0 artifact, policy, source commit and SHA-256 digests", sourcePath);
  }
  const { consequence, schema } = loadSource();
  for (const finding of validateSchema(consequence, { schema, dialect: "draft7", allErrors: true }, context)) {
    report(`canonical consequence does not satisfy its published closed schema: ${finding.message}`, sourcePath);
  }
  const schemas = document.components?.schemas || {};
  for (const [name, values] of Object.entries(consequence.enums)) {
    const component = schemas[name];
    if (component?.type !== "string" || !isDeepStrictEqual(component.enum, values)) {
      report(`${name} must declare exactly the canonical ordered values once; reference #/components/schemas/${name} instead of copying or widening it`, ["components", "schemas", name]);
    }
  }
  function entries(value, at, visit) {
    if (value && typeof value === "object") {
      for (const [key, child] of Object.entries(value)) visit(child, [...at, key]);
    }
  }
  function enums(value, at) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return;
    if (Array.isArray(value.enum)) {
      for (const [name, values] of Object.entries(consequence.enums)) {
        const canonicalPath = ["components", "schemas", name];
        if (!isDeepStrictEqual(at, canonicalPath) && values.every((member) => value.enum.includes(member))) {
          report(`duplicate ${name} enumeration; use $ref: '#/components/schemas/${name}'`, [...at, "enum"]);
        }
      }
    }
    // Map keys are schema names; enum/const/example/default values are data, not schemas.
    for (const key of ["properties", "patternProperties", "dependentSchemas", "$defs"]) {
      entries(value[key], [...at, key], enums);
    }
    for (const key of ["allOf", "anyOf", "oneOf", "prefixItems"]) {
      if (Array.isArray(value[key])) entries(value[key], [...at, key], enums);
    }
    for (const key of ["items", "additionalProperties", "unevaluatedProperties", "unevaluatedItems",
      "contains", "propertyNames", "not", "if", "then", "else", "contentSchema"]) {
      enums(value[key], [...at, key]);
    }
  }
  function headers(value, at) {
    entries(value, at, message);
  }
  function message(value, at) {
    if (!value || typeof value !== "object") return;
    enums(value.schema, [...at, "schema"]);
    headers(value.headers, [...at, "headers"]);
    entries(value.content, [...at, "content"], (media, mediaAt) => {
      if (!media || typeof media !== "object") return;
      enums(media.schema, [...mediaAt, "schema"]);
      entries(media.encoding, [...mediaAt, "encoding"], (encoding, encodingAt) => {
        headers(encoding?.headers, [...encodingAt, "headers"]);
      });
    });
  }
  function callback(value, at) {
    entries(value, at, (item, itemAt) => {
      if (!itemAt.at(-1).startsWith("x-")) pathItem(item, itemAt);
    });
  }
  function pathItem(value, at) {
    if (!value || typeof value !== "object") return;
    if (Array.isArray(value.parameters)) entries(value.parameters, [...at, "parameters"], message);
    for (const method of HTTP_METHODS) {
      const operation = value[method];
      if (!operation || typeof operation !== "object") continue;
      const operationAt = [...at, method];
      if (Array.isArray(operation.parameters)) entries(operation.parameters, [...operationAt, "parameters"], message);
      message(operation.requestBody, [...operationAt, "requestBody"]);
      entries(operation.responses, [...operationAt, "responses"], (response, responseAt) => {
        if (!responseAt.at(-1).startsWith("x-")) message(response, responseAt);
      });
      entries(operation.callbacks, [...operationAt, "callbacks"], callback);
    }
  }
  // Visit physical declarations once; a $ref to a canonical definition is not a copy.
  entries(schemas, ["components", "schemas"], enums);
  for (const key of ["parameters", "headers", "requestBodies", "responses"]) {
    entries(document.components?.[key], ["components", key], message);
  }
  entries(document.components?.pathItems, ["components", "pathItems"], pathItem);
  entries(document.components?.callbacks, ["components", "callbacks"], callback);
  entries(document.paths, ["paths"], (item, at) => {
    if (at.at(-1).startsWith("/")) pathItem(item, at);
  });
  entries(document.webhooks, ["webhooks"], pathItem);
  const registration = schemas.CustomDestinationRegistrationRequest;
  if (!registration || registration.type !== "object" || registration.additionalProperties !== false ||
      !isDeepStrictEqual(Object.keys(registration.properties || {}).sort(), [...consequence.wire.registration_fields].sort())) {
    report("CustomDestinationRegistrationRequest must be closed and contain only the canonical host/pathPrefix/credentialHeader/providerKeyRef/models enrollment fields", ["components", "schemas", "CustomDestinationRegistrationRequest"]);
  }
  if (registration?.properties?.credentialHeader?.$ref !== "#/components/schemas/CredentialHeader") {
    report("registration credentialHeader must reference the shared CredentialHeader enum, not a free string or inline copy", ["components", "schemas", "CustomDestinationRegistrationRequest", "properties", "credentialHeader"]);
  }
  for (const [name, component] of Object.entries(schemas)) {
    if (name.startsWith("CustomDestination") && component.type === "object") {
      if (component.additionalProperties !== false) {
        report(`${name} must use additionalProperties: false; schema closure still needs generated-runtime conformance`, ["components", "schemas", name, "additionalProperties"]);
      }
      if (component["x-pennilogic-strict-provider"] !== true) {
        report(`${name} must select x-pennilogic-strict-provider for the shared generated read/write boundary`, ["components", "schemas", name, "x-pennilogic-strict-provider"]);
      }
    }
  }
  if (!isDeepStrictEqual(schemas.CustomDestination?.["x-state-denials"], consequence.state_denials)) {
    report("CustomDestination x-state-denials must match the canonical state-to-denial map; active has no state denial", ["components", "schemas", "CustomDestination", "x-state-denials"]);
  }
  return results;
};
