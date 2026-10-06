"use strict";

const fs = require("node:fs");
const crypto = require("node:crypto");
const path = require("node:path");

const BINDING = Object.freeze({
  repository: "PenniLogic/api",
  commit: "fd58da679672a4de7aabee2562644bed3799e614",
  path: "data/categories/category-seed.v1.json",
  gitBlob: "16b70af7b527bfd617539d9bd413ba78738f8f05",
  sha256: "3fcf568abcfc7da7409e463ea6bbda9d952af739cfa9ef42a8e9fe90b993390c",
  size: 14237,
  localFile: "category-seed.v1.json",
});

function loadSeed(directory) {
  const bytes = fs.readFileSync(path.join(directory, BINDING.localFile));
  const digest = crypto.createHash("sha256").update(bytes).digest("hex");
  const blob = crypto.createHash("sha1").update(`blob ${bytes.length}\0`).update(bytes).digest("hex");
  if (bytes.length !== BINDING.size || digest !== BINDING.sha256 || blob !== BINDING.gitBlob) {
    throw new Error("accepted category seed byte binding rejected");
  }
  return JSON.parse(bytes.toString("utf8"));
}

function derivedEnums(seed) {
  return {
    CategorySystemKey: seed.categories.map((row) => row.key),
    CategoryIcon: seed.icons.map((row) => row.id),
    CategoryColour: seed.colours.map((row) => row.id),
  };
}

function writeEnums(specification) {
  const enums = derivedEnums(loadSeed(path.dirname(specification)));
  let text = fs.readFileSync(specification, "utf8");
  for (const [name, values] of Object.entries(enums)) {
    const pattern = new RegExp(`(^    ${name}:\\r?\\n)([\\s\\S]*?)(?=^    [A-Za-z][A-Za-z0-9_]*:|^  [A-Za-z])`, "m");
    const match = pattern.exec(text);
    if (!match || match[2].split("\n").filter((line) => /^      type: string\r?$/.test(line)).length !== 1) {
      throw new Error("category enum component derivation target rejected");
    }
    const body = match[2].replace(/^      enum:.*\r?\n/m, "");
    const updated = body.replace(/^      type: string\r?\n/m,
      `      type: string\n      enum: ${JSON.stringify(values)}\n`);
    text = text.slice(0, match.index) + match[1] + updated + text.slice(match.index + match[0].length);
  }
  fs.writeFileSync(specification, text, "utf8");
}

if (require.main === module) {
  try {
    if (process.argv.length !== 4 || process.argv[2] !== "--write-enums") {
      throw new Error("usage: node scripts\\category_seed.cjs --write-enums spec\\openapi.yaml");
    }
    writeEnums(path.resolve(process.argv[3]));
    console.log("category enum components derived from exact accepted seed bytes");
  } catch (error) {
    console.error(error instanceof Error ? error.message : "category derivation failed");
    process.exitCode = 1;
  }
}

module.exports = { BINDING, loadSeed, derivedEnums };
