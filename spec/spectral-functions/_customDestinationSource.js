"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { createHash } = require("node:crypto");

const SOURCE = Object.freeze({
  repository: "PenniLogic/docs",
  commit: "9e394961032b50eafb68b37ff3f7c209243127cf",
  artifact: "adr-022-ai-egress-consequences@1.1.0",
  policyVersion: "2026-10-05.2",
  consequences: "adr022/ai-egress-consequences.json",
  consequencesSha256: "44c3b40b8caa01c26c46a6b8103667501bc9e1be2a2cf714dcb84d8a358f476f",
  schema: "adr022/ai-egress-consequences.schema.json",
  schemaSha256: "61f625d370840e9a40e1d7a05355c5504cc8a9d98aafac4814d641d21a021d73",
});

function loadSource(directory = path.resolve("spec", "adr022")) {
  function read(name, digest) {
    const bytes = fs.readFileSync(path.join(directory, name));
    if (createHash("sha256").update(bytes).digest("hex") !== digest) {
      throw new Error(`ADR-022 ${name}: canonical byte digest mismatch; restore the exact file from ${SOURCE.repository}@${SOURCE.commit}/adr, never rewrite the policy`);
    }
    // Check bytes before parsing: duplicate keys and whitespace edits cannot hide behind JSON.parse.
    return JSON.parse(bytes.toString("utf8"));
  }
  return {
    consequence: read("ai-egress-consequences.json", SOURCE.consequencesSha256),
    schema: read("ai-egress-consequences.schema.json", SOURCE.schemaSha256),
  };
}

module.exports = { SOURCE, loadSource };
