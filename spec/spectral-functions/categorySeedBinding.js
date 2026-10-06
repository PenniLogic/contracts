"use strict";

const path = require("node:path");
const { BINDING, loadSeed, derivedEnums } = require("../../scripts/category_seed.cjs");

module.exports = function categorySeedBinding(document, _options, context) {
  const source = context.document.source;
  const findings = [];
  const add = (message, at) => findings.push({ message, path: at });
  if (typeof source !== "string") throw new Error("category source requires a document path");
  let seed;
  try { seed = loadSeed(path.dirname(source)); }
  catch (error) {
    if (!(error instanceof Error)) throw error;
    add("The exact protected-accepted category seed byte/SHA/size/Gitblob binding failed",
      ["x-category-seed-source"]);
    return findings;
  }
  if (JSON.stringify(document["x-category-seed-source"]) !== JSON.stringify(BINDING)) {
    add("Category source must retain the exact accepted API commit and immutable byte binding",
      ["x-category-seed-source"]);
  }
  const schemas = document.components?.schemas || {};
  for (const [name, values] of Object.entries(derivedEnums(seed))) {
    if (schemas[name]?.type !== "string" || JSON.stringify(schemas[name]?.enum) !== JSON.stringify(values)) {
      add("Category key/icon/colour vocabulary must be derived from the one accepted seed",
        ["components", "schemas", name]);
    }
  }
  for (const name of ["CreateCategoryRequest", "UpdateCategoryRequest", "Category"]) {
    for (const [field, type] of [["icon", "CategoryIcon"], ["colour", "CategoryColour"]]) {
      if (schemas[name]?.properties?.[field]?.$ref !== `#/components/schemas/${type}`) {
        add("Category presentation fields must reference the accepted registry types",
          ["components", "schemas", name, "properties", field]);
      }
    }
  }
  return findings;
};
