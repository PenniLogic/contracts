"use strict";
const { isDeepStrictEqual } = require("node:util");
const { schema: validateSchema } = require("@stoplight/spectral-functions");
const { SOURCE, loadSource } = require("./_customDestinationSource");

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
  function enums(value, at) {
    if (!value || typeof value !== "object") return;
    if (Array.isArray(value.enum)) {
      for (const [name, values] of Object.entries(consequence.enums)) {
        const canonicalPath = ["components", "schemas", name];
        if (!isDeepStrictEqual(at, canonicalPath) && value.enum.length === values.length &&
            values.every((member) => value.enum.includes(member))) {
          report(`duplicate ${name} enumeration; use $ref: '#/components/schemas/${name}'`, [...at, "enum"]);
        }
      }
    }
    for (const [key, child] of Object.entries(value)) {
      if (!["example", "examples", "default"].includes(key)) enums(child, [...at, key]);
    }
  }
  enums(document, []);
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
