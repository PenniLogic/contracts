"use strict";

const fs = require("node:fs");
const { Yaml } = require("@stoplight/spectral-parsers");
const { resolveLocalRef, SCHEMA_ANNOTATIONS, HTTP_METHODS } = require("../spec/spectral-functions/_shared.js");
const Ajv = require("ajv/dist/2020").default;
const addFormats = require("ajv-formats");
const { successResponses } = require("./success_responses.cjs");
const { BINDING, loadSeed, derivedEnums } = require("./category_seed.cjs");
const path = require("node:path");
const { isDeepStrictEqual } = require("node:util");

const EXAMPLE_BUDGET = 4096;

const KEYS = new Set(["$ref", "type", "properties", "required", "additionalProperties", "items",
  "enum", "const", "pattern", "minLength", "maxLength", "minimum", "maximum",
  "exclusiveMinimum", "exclusiveMaximum", "minItems", "maxItems", "uniqueItems",
  "format", "allOf", "anyOf", "oneOf", "not", "if", "then", "else", "readOnly"]);
const TYPES = new Set(["object", "array", "string", "integer", "boolean", "null"]);

function reject(reason) { throw new Error(`provider constraint generation rejected: ${reason}`); }
function object(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) reject("malformed schema");
  return value;
}
function scalar(value) {
  return value === null || typeof value === "string" || typeof value === "boolean" || Number.isSafeInteger(value);
}

function nullablePair(schema) {
  const branches = schema.anyOf || schema.oneOf;
  if (!Array.isArray(branches) || branches.length !== 2) return undefined;
  const empty = branches.find((branch) => branch.type === "null" &&
    Object.keys(branch).every((key) => key === "type" || SCHEMA_ANNOTATIONS.has(key)));
  return empty && branches.find((branch) => branch !== empty);
}

function usableGeneratorExample(value) {
  // The pinned Python generator ignores Java-whitespace-only examples and the literal "null".
  return typeof value === "string" && value !== "null" &&
    /[^\t\n\v\f\r\u001c-\u0020\u1680\u2000-\u2006\u2008-\u200a\u2028\u2029\u205f\u3000]/u.test(value);
}

function validatePattern(pattern) {
  if (typeof pattern !== "string" || pattern[0] !== "^" || pattern.at(-1) !== "$" ||
      /[^\x20-\x7e]/.test(pattern)) reject("unsupported pattern");
  let index = 1;
  let depth = 0;
  const end = pattern.length - 1;
  const peek = () => pattern[index];
  const bounded = (count) => Math.min(EXAMPLE_BUDGET + 1, count);
  const join = (left, right) => left === null || right === null || left.length + right.length > EXAMPLE_BUDGET ? null : left + right;
  const repeat = (value, count) => count === 0 ? "" : value === null || value.length * count > EXAMPLE_BUDGET ? null : value.repeat(count);
  const shortest = (values) => values.filter((value) => value !== null)
    .sort((left, right) => left.length - right.length || (left < right ? -1 : left > right ? 1 : 0))[0] ?? null;
  const literal = (value) => ({ shortest: value, nonblank: value.trim() ? value : null, work: 1 });
  function escaped(inClass) {
    index += 1;
    const character = peek();
    if (index >= end || !(inClass ? "\\.^$*+?()[]{}|/-" : "\\.^$*+?()[]{}|/").includes(character)) {
      reject("unsupported pattern escape");
    }
    index += 1;
    return character;
  }
  function characterClass() {
    index += 1;
    const negated = peek() === "^";
    if (negated) index += 1;
    const characters = [];
    while (index < end && peek() !== "]") {
      const character = peek();
      if (character === "[" || ["&&", "||", "~~", "--"].includes(pattern.slice(index, index + 2))) {
        reject("unsupported character class");
      }
      if (character === "\\" && pattern[index + 1] === "s") {
        characters.push({ character: " ", range: false, whitespace: true });
        index += 2;
      } else if (character === "\\") characters.push({ character: escaped(true), range: false });
      else { characters.push({ character, range: character === "-" }); index += 1; }
    }
    if (!characters.length || index >= end || peek() !== "]") reject("malformed character class");
    let previousRangeEnd = -1;
    for (let position = 1; position + 1 < characters.length; position += 1) {
      if (characters[position].range && (characters[position - 1].range || characters[position + 1].range ||
          characters[position - 1].whitespace || characters[position + 1].whitespace ||
          position - 1 <= previousRangeEnd ||
          characters[position - 1].character.charCodeAt(0) > characters[position + 1].character.charCodeAt(0))) {
        reject("malformed character range");
      }
      if (characters[position].range) previousRangeEnd = position + 1;
    }
    index += 1;
    const allowed = new Set(characters.filter((value) => !value.range).map((value) => value.character.charCodeAt(0)));
    for (let position = 1; position + 1 < characters.length; position += 1) {
      if (characters[position].range) {
        for (let code = characters[position - 1].character.charCodeAt(0); code <= characters[position + 1].character.charCodeAt(0); code += 1) allowed.add(code);
      }
    }
    if (characters[0].range || characters.at(-1).range) allowed.add(45);
    const values = Array.from({ length: 95 }, (_, position) => position + 32)
      .filter((code) => negated ? !allowed.has(code) : allowed.has(code)).map((code) => String.fromCharCode(code));
    if (!values.length && negated) values.push("\u00a1");
    return { shortest: values[0] ?? null, nonblank: values.find((value) => value.trim()) ?? null, work: 1 };
  }
  function repetition(value) {
    let lower, upper;
    if (["*", "+", "?"].includes(peek())) {
      const operator = pattern[index++];
      lower = operator === "+" ? 1 : 0;
      upper = operator === "?" ? 1 : 100;
    } else if (peek() === "{") {
      const start = ++index;
      while (index < end && /[0-9,]/.test(peek())) index += 1;
      const text = pattern.slice(start, index);
      const match = /^(0|[1-9][0-9]*)(?:,(0|[1-9][0-9]*)?)?$/.exec(text);
      if (!match || peek() !== "}") reject("malformed repetition");
      lower = Number(match[1]);
      upper = text.endsWith(",") ? Math.max(lower, 100) : match[2] === undefined ? lower : Number(match[2]);
      if (lower > 2147483647 || upper > 2147483647 || lower > upper) reject("unsupported repetition");
      index += 1;
    } else return value;
    if (lower > EXAMPLE_BUDGET && value.shortest === "") reject("unsupported example construction");
    const minimum = repeat(value.shortest, lower);
    return {
      shortest: minimum,
      nonblank: minimum?.trim() ? minimum : upper > 0 ? join(repeat(value.shortest, Math.max(0, lower - 1)), value.nonblank) : null,
      work: bounded(1 + upper * value.work),
    };
  }
  function sequence(inGroup) {
    let count = 0;
    let value = { shortest: "", nonblank: null, work: 0 };
    while (index < end && (!inGroup || (peek() !== ")" && peek() !== "|"))) {
      const character = peek();
      let atom;
      if (character === "\\") atom = literal(escaped(false));
      else if (character === "[") atom = characterClass();
      else if (character === "(") {
        if (++depth > 64) reject("unsupported pattern depth");
        index += 1;
        const alternatives = [sequence(true)];
        if (!alternatives[0].count) reject("empty pattern group");
        while (peek() === "|") {
          index += 1;
          const alternative = sequence(true);
          if (!alternative.count) reject("empty pattern alternative");
          alternatives.push(alternative);
        }
        if (peek() !== ")") reject("malformed pattern group");
        index += 1;
        depth -= 1;
        atom = {
          shortest: shortest(alternatives.map((item) => item.shortest)),
          nonblank: shortest(alternatives.map((item) => item.nonblank)),
          work: bounded(1 + Math.max(...alternatives.map((item) => item.work))),
        };
      } else if ("^$|)*+?{}]".includes(character)) reject("unsupported pattern syntax");
      else { atom = literal(character === "." ? "a" : character); index += 1; }
      atom = repetition(atom);
      const minimum = join(value.shortest, atom.shortest);
      value = {
        shortest: minimum,
        nonblank: shortest([join(value.nonblank, atom.shortest), join(value.shortest, atom.nonblank)]),
        work: bounded(value.work + atom.work),
      };
      count += 1;
    }
    return { ...value, count };
  }
  const value = sequence(false);
  if (index !== end) reject("malformed pattern");
  try { new RegExp(pattern, "u"); } catch { reject("malformed pattern"); }
  return value;
}

function compile(document) {
  const schemas = object(document.components?.schemas);
  const compiled = {};
  const active = new Set();
  const generationExamples = [];
  const ajv = new Ajv({ strict: false });
  addFormats(ajv);
  ajv.addSchema({ components: document.components }, "generation");
  const roots = Object.keys(schemas).filter((name) => schemas[name]["x-pennilogic-strict-provider"] === true).sort();

  function component(name) {
    if (Object.hasOwn(compiled, name)) return;
    if (active.has(name)) reject("cyclic reference");
    if (!/^[A-Za-z][A-Za-z0-9_]*$/.test(name) || !Object.hasOwn(schemas, name)) reject("unresolved reference");
    active.add(name);
    compiled[name] = node(schemas[name], true, false, roots.includes(name));
    active.delete(name);
  }

  function node(input, valueSchema = true, readOnlyAllowed = false, ownsProperties = false) {
    const source = object(input), result = {};
    for (const key of Object.keys(source)) {
      if (!KEYS.has(key) && !SCHEMA_ANNOTATIONS.has(key)) reject("unsupported keyword");
    }
    if (source.default !== undefined && source.default !== null) reject("unsupported default");
    if (source.readOnly !== undefined && typeof source.readOnly !== "boolean") reject("malformed readOnly annotation");
    if (source.readOnly === true && !readOnlyAllowed) reject("unsupported readOnly placement");
    if (valueSchema && source.type === undefined && source.$ref === undefined && !nullablePair(source)) reject("untyped value");
    if (source.$ref !== undefined) {
      if (typeof source.$ref !== "string" || !/^#\/components\/schemas\/[A-Za-z][A-Za-z0-9_]*$/.test(source.$ref) ||
          !resolveLocalRef(source.$ref, document)) reject("unresolved reference");
      const name = source.$ref.slice("#/components/schemas/".length);
      component(name);
      result.ref = name;
    }
    if (source.type !== undefined) {
      if (Array.isArray(source.type)) {
        if (source.type.length !== 2 || new Set(source.type).size !== 2 ||
            !source.type.includes("null") ||
            !source.type.every((kind) => ["string", "integer", "boolean", "array", "null"].includes(kind))) reject("unsupported type");
      } else if (!TYPES.has(source.type)) reject("unsupported type");
      result.type = source.type;
    }
    if (source.format !== undefined) {
      const kind = Array.isArray(source.type) ? source.type.find((type) => type !== "null") : source.type;
      if ((kind === "string" && !["date", "date-time", "uri-reference", "uuid"].includes(source.format)) ||
          (kind === "integer" && !["int32", "int64"].includes(source.format)) ||
          !["string", "integer"].includes(kind)) reject("unsupported format");
      result.format = source.format;
    }
    for (const key of ["minLength", "maxLength", "minItems", "maxItems"]) {
      if (source[key] !== undefined) {
        if (!Number.isSafeInteger(source[key]) || source[key] < 0 || source[key] > 2147483647) reject("malformed bound");
        result[key] = source[key];
      }
    }
    for (const [lower, upper] of [["minLength", "maxLength"], ["minItems", "maxItems"]]) {
      if (result[lower] !== undefined && result[upper] !== undefined && result[lower] > result[upper]) reject("contradictory bounds");
    }
    for (const key of ["minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"]) {
      if (source[key] !== undefined) {
        if (!Number.isSafeInteger(source[key])) reject("unsupported numeric bound");
        result[key] = source[key];
      }
    }
    if (result.minimum !== undefined && result.maximum !== undefined && result.minimum > result.maximum) reject("contradictory bounds");
    if ((result.exclusiveMinimum !== undefined && result.maximum !== undefined && result.exclusiveMinimum >= result.maximum) ||
        (result.minimum !== undefined && result.exclusiveMaximum !== undefined && result.minimum >= result.exclusiveMaximum) ||
        (result.exclusiveMinimum !== undefined && result.exclusiveMaximum !== undefined && result.exclusiveMinimum >= result.exclusiveMaximum)) reject("contradictory bounds");
    if (source.pattern !== undefined) {
      const construction = validatePattern(source.pattern);
      if (construction.work > EXAMPLE_BUDGET || (source.minLength ?? 0) > EXAMPLE_BUDGET) {
        const validate = ajv.compile({ ...source, components: document.components });
        if (source.example !== undefined && !validate(source.example)) reject("unsupported example construction");
        const firstEnum = source.enum?.[0];
        const boundedEnum = typeof firstEnum === "string" && firstEnum.length <= EXAMPLE_BUDGET && validate(firstEnum);
        const candidates = [source.example, source.const, ...(source.enum ?? []), construction.nonblank];
        const candidate = candidates.find((value) => usableGeneratorExample(value) &&
          value.length <= EXAMPLE_BUDGET && validate(value));
        if (candidate === undefined && !boundedEnum) reject("unsupported example construction");
        if (!boundedEnum && source.example !== candidate) {
          source.example = candidate;
          generationExamples.push({ pattern: source.pattern, example: candidate });
        }
      }
      result.pattern = source.pattern;
    }
    if (source.pattern === undefined && (source.minLength ?? 0) > EXAMPLE_BUDGET) reject("unsupported example construction");
    if (source.enum !== undefined) {
      if (!Array.isArray(source.enum) || !source.enum.length || !source.enum.every(scalar) ||
          new Set(source.enum.map((value) => JSON.stringify(value))).size !== source.enum.length) reject("malformed enum");
      const kinds = Array.isArray(source.type) ? source.type : [source.type];
      if (valueSchema && !kinds.every((kind) => kind === "string" || kind === "null")) reject("unsupported enum representation");
      if (kinds.includes("string") && !source.enum.every((value) =>
          typeof value === "string" || (value === null && kinds.includes("null")))) reject("ambiguous enum");
      result.enum = source.enum;
    }
    if (source.const !== undefined) {
      if (!scalar(source.const)) reject("unsupported constant");
      result.const = source.const;
    }
    if (source.required !== undefined) {
      if (!Array.isArray(source.required) || !source.required.every((key) => typeof key === "string" && key.length > 0) ||
          new Set(source.required).size !== source.required.length) reject("malformed required members");
      result.required = source.required;
    }
    if (source.properties !== undefined) {
      result.properties = Object.fromEntries(Object.entries(object(source.properties)).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)
        .map(([name, property]) => [name, node(property, valueSchema, ownsProperties)]));
    }
    if (source.additionalProperties !== undefined) {
      if (typeof source.additionalProperties !== "boolean") reject("unsupported object map");
      result.additionalProperties = source.additionalProperties;
    }
    if (source.items !== undefined) result.items = node(source.items);
    if ((source.type === "array" || source.type?.includes?.("array")) && source.items === undefined) reject("unbound array items");
    if (source.uniqueItems !== undefined) {
      if (typeof source.uniqueItems !== "boolean") reject("malformed uniqueness");
      result.uniqueItems = source.uniqueItems;
    }
    for (const key of ["allOf", "anyOf", "oneOf"]) {
      if (source[key] !== undefined) {
        if (!Array.isArray(source[key]) || !source[key].length) reject("malformed composition");
        result[key] = source[key].map((child) => node(child, false));
      }
    }
    for (const key of ["not", "if", "then", "else"]) if (source[key] !== undefined) result[key] = node(source[key], false);
    if ((source.then !== undefined || source.else !== undefined) && source.if === undefined) reject("unbound conditional");
    return result;
  }

  for (const name of roots) {
    if (schemas[name].type !== "object" || schemas[name].additionalProperties !== false) reject("open marked provider");
    component(name);
  }
  const success = successResponses(document);
  for (const operation of success) for (const response of operation.cases) {
    if (!response.empty) compiled[response.validator] = node(response.schema, false);
  }
  const result = { roots, schemas: Object.fromEntries(Object.entries(compiled).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)) };
  const projection = JSON.parse(JSON.stringify(document));
  const readOnlyModels = roots.filter((name) =>
    Object.values(schemas[name].properties || {}).some((property) => property.readOnly === true));
  if (readOnlyModels.length) {
    function usedSchemas(response) {
      const names = new Set(), visited = new Set();
      let work = 0;
      function visit(value, depth = 0) {
        if (value === undefined) return;
        if (++work > 16384 || depth > 64) reject("readOnly usage traversal bound");
        if (!value || typeof value !== "object" || Array.isArray(value)) reject("malformed readOnly usage");
        if (value.$ref !== undefined) {
          if (typeof value.$ref !== "string" || !value.$ref.startsWith("#/")) reject("unsupported readOnly usage reference");
          const target = resolveLocalRef(value.$ref, document);
          if (!target) reject("unresolved readOnly usage reference");
          if (value.$ref.startsWith("#/components/schemas/")) names.add(value.$ref.slice("#/components/schemas/".length));
          if (!visited.has(value.$ref)) {
            visited.add(value.$ref);
            visit(target, depth + 1);
          }
        }
        for (const child of Object.values(value.properties || {})) visit(child, depth + 1);
        for (const keyword of ["schema", "items", "not", "if", "then", "else"]) visit(value[keyword], depth + 1);
        for (const keyword of ["allOf", "anyOf", "oneOf"]) for (const child of value[keyword] || []) visit(child, depth + 1);
        for (const child of Object.values(value.content || {})) visit(child.schema, depth + 1);
      }
      function pathItem(item, active = new Set()) {
        if (item.$ref) {
          if (active.has(item.$ref)) reject("cyclic readOnly path reference");
          const target = resolveLocalRef(item.$ref, document);
          if (!target) reject("unresolved readOnly path reference");
          pathItem(target, new Set(active).add(item.$ref));
        }
        for (const method of HTTP_METHODS) {
          const operation = item[method];
          if (!operation) continue;
          if (response) {
            for (const child of Object.values(operation.responses || {})) visit(child);
          } else {
            visit(operation.requestBody);
            for (const child of [...(item.parameters || []), ...(operation.parameters || [])]) visit(child);
          }
        }
      }
      for (const item of Object.values(document.paths || {})) pathItem(item);
      return names;
    }
    const requests = usedSchemas(false), responses = usedSchemas(true);
    for (const name of readOnlyModels) {
      if (requests.has(name) || !responses.has(name)) reject("unsupported keyword: readOnly model must be response-only");
      const model = projection.components.schemas[name];
      model["x-pennilogic-read-only-response"] = true;
      for (const property of Object.values(model.properties)) {
        if (property.readOnly === true) property["x-pennilogic-read-only-response"] = true;
      }
    }
  }
  for (const operation of success) {
    const {cases, ...metadata} = operation;
    projection.paths[operation.path][operation.method]["x-pennilogic-success"] = {
      ...metadata, cases: cases.map(({schema, ...response}) => response),
    };
  }
  const projectedModels = [];
  function allowsNull(source, active = new Set()) {
    if (source.type === "null" || (Array.isArray(source.type) && source.type.includes("null")) || nullablePair(source)) return true;
    if (!source.$ref || active.has(source.$ref)) return false;
    active.add(source.$ref);
    return allowsNull(resolveLocalRef(source.$ref, document), active);
  }
  function projectNull(source, required = true) {
    let changed = false;
    if (source.type === "null") {
      source.type = ["string", "null"];
      source.nullable = true;
      changed = true;
    } else if (Array.isArray(source.type)) {
      source.nullable = true;
      changed = true;
    } else {
      const nonnull = nullablePair(source);
      if (nonnull) {
        delete source.anyOf;
        delete source.oneOf;
        let target = nonnull;
        const aliases = new Set();
        let scalarReference;
        while (target?.$ref) {
          if (aliases.has(target.$ref) ||
              Object.keys(target).some((key) => key !== "$ref" && !SCHEMA_ANNOTATIONS.has(key))) {
            reject("unsupported nullable alias refinement");
          }
          aliases.add(target.$ref);
          scalarReference = target.$ref;
          target = resolveLocalRef(target.$ref, document);
        }
        if (!target || !["string", "integer", "boolean"].includes(target.type)) reject("unsupported nullable reference");
        Object.assign(source, JSON.parse(JSON.stringify(target)), { type: [target.type, "null"], nullable: true });
        if (scalarReference && target.enum) {
          source.$ref = scalarReference;
          source.type = target.type;
          source["x-pennilogic-nullable-enum"] = true;
          delete source.enum;
        }
        changed = true;
      } else if (source.$ref && allowsNull(source)) {
        source.nullable = true;
        changed = true;
      }
    }
    if (source.nullable === true && !required) {
      source["x-pennilogic-null-presence"] = true;
    }
    for (const [name, property] of Object.entries(source.properties || {})) {
      changed = projectNull(property, source.required?.includes(name) === true) || changed;
    }
    if (source.items) {
      changed = projectNull(source.items) || changed;
      if (source.items["x-pennilogic-nullable-enum"] || source.items["x-pennilogic-nullable-enum-items"]) {
        // Homogeneous array chains selected here end only in a source-declared nullable enum.
        source["x-pennilogic-nullable-enum-items"] = true;
      }
    }
    return changed;
  }
  function layoutPropertyNames(model) {
    const inherited = model.$ref === undefined ? [] : layoutPropertyNames(resolveLocalRef(model.$ref, document));
    return [...new Set([
      ...inherited, ...(model.allOf || []).flatMap(layoutPropertyNames), ...Object.keys(model.properties || {}),
    ])];
  }
  function projectLayout(model) {
    let changed = false;
    const objectRefinements = (model.allOf || []).filter((branch) =>
      Object.keys(branch).length === 1 && branch.properties &&
      Object.keys(branch.properties).length > 0 && Object.entries(branch.properties).every(([name, refinement]) => {
        const field = model.properties?.[name];
        const target = field?.$ref && resolveLocalRef(field.$ref, document);
        return target?.type === "object" && Object.keys(refinement).length === 1 &&
          refinement.properties && Object.keys(refinement.properties).length > 0 &&
          Object.entries(refinement.properties).every(([property, constraint]) => {
            const declared = target.properties?.[property];
            const scalar = declared?.$ref ? resolveLocalRef(declared.$ref, document) : declared;
            return scalar?.type === "string" && Object.keys(constraint).length === 1 &&
              Array.isArray(constraint.enum) && constraint.enum.length > 0 &&
              constraint.enum.every((value) => typeof value === "string");
          });
      }));
    if (model.properties) {
      // The pinned generator visits unconditional allOf fields before local fields. Preserve that
      // positional API order without importing branch-only fields, types or requiredness.
      const names = layoutPropertyNames(model).filter((name) => Object.hasOwn(model.properties, name));
      changed = names.some((name, index) => name !== Object.keys(model.properties)[index]);
      model.properties = Object.fromEntries(names.map((name) => [name, model.properties[name]]));
    }
    if (model.$ref !== undefined && Object.hasOwn(model, "enum")) {
      delete model.enum;
      changed = true;
    }
    for (const key of ["allOf", "anyOf", "oneOf", "if", "then", "else", "not", "const"]) {
      if (!Object.hasOwn(model, key)) continue;
      if (key === "allOf" && objectRefinements.length) {
        // Scalar enum-only referenced-object refinements also publish inline helper models.
        // Deeper constraints stay exclusively in the full validator, never the layout projection.
        if (objectRefinements.length !== model.allOf.length) changed = true;
        model.allOf = objectRefinements;
        continue;
      }
      delete model[key];
      changed = true;
    }
    for (const property of Object.values(model.properties || {})) changed = projectLayout(property) || changed;
    if (model.items) changed = projectLayout(model.items) || changed;
    return changed;
  }
  for (const name of roots) {
    const model = projection.components.schemas[name];
    // Closed marked objects declare their exact fields above. Keep branch requiredness and inherited
    // constraints in the registered validator, not lossy field flattening or synthetic scalar enums.
    const nullable = projectNull(model);
    if (projectLayout(model) || nullable) projectedModels.push(name);
  }
  // Scalar references also need a generator-only nullable layout; runtime declarations retain the source.
  for (const [name, model] of Object.entries(projection.components.schemas)) {
    if (!roots.includes(name) && Object.hasOwn(compiled, name)) projectNull(model);
  }
  return generationExamples.length || projectedModels.length || success.length ? { ...result, success,
    generation_budget: EXAMPLE_BUDGET,
    generation_examples: generationExamples, generation_model_projection: projectedModels,
    generation_input: projection } : result;
}

module.exports = { compile };
if (require.main === module) {
  try {
    const parsed = Yaml.parse(fs.readFileSync(process.argv[2], "utf8"));
    if (parsed.diagnostics.length) reject("invalid source document");
    const seed = loadSeed(path.dirname(process.argv[2]));
    if (!isDeepStrictEqual(parsed.data["x-category-seed-source"], BINDING)) reject("category source binding");
    for (const [name, values] of Object.entries(derivedEnums(seed))) {
      const schema = parsed.data.components?.schemas?.[name];
      if (schema?.type !== "string" || !isDeepStrictEqual(schema.enum, values)) reject("category vocabulary binding");
    }
    process.stdout.write(JSON.stringify({ ...compile(parsed.data), category_seed: seed }));
  } catch (error) {
    process.stderr.write(error instanceof Error && error.message.startsWith("provider constraint generation rejected:")
      ? error.message + "\n" : "provider constraint generation rejected: invalid source document\n");
    process.exitCode = 1;
  }
}
