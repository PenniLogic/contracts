"use strict";

const { HTTP_METHODS, resolveLocalRef, SCHEMA_ANNOTATIONS } = require("../spec/spectral-functions/_shared");

function reject() { throw new Error("provider constraint generation rejected: unsupported success response binding"); }
function resolve(value, document, active = new Set()) {
  if (!value || typeof value !== "object") reject();
  if (!value.$ref) return value;
  if (active.has(value.$ref)) reject();
  active.add(value.$ref);
  return resolve(resolveLocalRef(value.$ref, document), document, active);
}
function bodyModel(schema, document) {
  const names = [];
  function references(value) {
    if (!value || typeof value !== "object") reject();
    if (value.$ref) {
      const name = value.$ref.match(/^#\/components\/schemas\/([A-Za-z][A-Za-z0-9_]*)$/)?.[1];
      if (!name) reject();
      names.push(name);
    }
    for (const branch of value.allOf || []) references(branch);
  }
  references(schema);
  const distinct = [...new Set(names)];
  if (distinct.length !== 1) reject();
  const name = distinct[0], model = document.components?.schemas?.[name];
  if (model?.type !== "object" || model.additionalProperties !== false ||
      model["x-pennilogic-strict-provider"] !== true) reject();
  return name;
}
function snake(name) {
  return name.replace(/([A-Z]+)([A-Z][a-z])/g, "$1_$2").replace(/([a-z0-9])([A-Z])/g, "$1_$2").toLowerCase();
}

function successResponses(document) {
  const result = [];
  for (const [path, item] of Object.entries(document.paths || {})) for (const method of HTTP_METHODS) {
    const operation = item[method];
    if (!operation) continue;
    const successes = Object.entries(operation.responses || {}).filter(([status]) => /^2[0-9]{2}$/.test(status));
    if (successes.length < 2) continue;
    const schemas = successes.map(([, response]) => resolve(response, document).content?.["application/json"]?.schema);
    if (new Set(schemas.map((schema) => JSON.stringify(schema ?? null))).size < 2) continue;
    if (!/^[A-Za-z][A-Za-z0-9_]*$/.test(operation.operationId)) reject();
    const type = operation.operationId[0].toUpperCase() + operation.operationId.slice(1) + "Success";
    const cases = successes.map(([status, response]) => {
      const resolved = resolve(response, document);
      const content = resolved.content || {};
      if (!Object.keys(content).length) return {status: Number(status), empty: true,
        caseType: `${type}Status${status}`, validator: `Success_${operation.operationId}_${status}`};
      if (JSON.stringify(Object.keys(content)) !== '["application/json"]') reject();
      const schema = content["application/json"].schema;
      const model = bodyModel(schema, document);
      return {status: Number(status), empty: false, model, module: snake(model), schema,
        caseType: `${type}Status${status}`, validator: `Success_${operation.operationId}_${status}`};
    });
    result.push({path, method, operation: operation.operationId, pythonOperation: snake(operation.operationId),
      type, cases: cases.sort((left, right) => left.status - right.status)});
  }
  return result;
}

module.exports = { successResponses };
