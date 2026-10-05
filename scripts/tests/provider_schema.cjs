"use strict";
// Schema-only fixture probe, using the already integrity-pinned Spectral dependencies.
const fs = require("node:fs");
const { Yaml } = require("@stoplight/spectral-parsers");
const Ajv = require("ajv/dist/2020").default;
const addFormats = require("ajv-formats");

const parsed = Yaml.parse(fs.readFileSync(process.argv[2], "utf8"));
if (parsed.diagnostics.length) throw new Error("specification parse failed");
const document = parsed.data;
const ajv = new Ajv({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema({ components: document.components }, "providers");
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const results = cases.map((entry) => {
  const validate = ajv.getSchema(`providers#/components/schemas/${entry.schema}`);
  if (!validate) throw new Error("provider schema is missing");
  const valid = validate(entry.wire);
  return { name: entry.name, valid, keywords: valid ? [] : validate.errors.map((error) => error.keyword) };
});
process.stdout.write(JSON.stringify(results));
