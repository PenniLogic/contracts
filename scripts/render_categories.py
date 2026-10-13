"""Typed immutable presentation defaults derived only from the byte-verified category provider."""

from __future__ import annotations

import json

from pl_contracts import PipelineError


def render_categories(language: str, seed: dict) -> tuple[str, str]:
    rows = seed["categories"]
    header = "Generated from exact accepted spec/category-seed.v1.json; do not edit. No category UUID or seed execution."
    if language == "python":
        entries = "".join(
            "    CategorySystemKey(" + json.dumps(row["key"]) + "): CategoryDefaults(" +
            "CategorySystemKey(" + json.dumps(row["key"]) + "), " +
            ("None" if row["parent_key"] is None else "CategorySystemKey(" + json.dumps(row["parent_key"]) + ")") +
            ", CategoryNature(" + json.dumps(row["nature"]) + "), CategoryIcon(" + json.dumps(row["icon"]) +
            "), CategoryColour(" + json.dumps(row["colour"]) + "), MappingProxyType(" +
            json.dumps(row["labels"], ensure_ascii=True) + "), " + str(row["introduced_in"]) + ", " +
            ("None" if row["retired_in"] is None else str(row["retired_in"])) + ", " + str(row["sort_order"]) + "),\n"
            for row in rows
        )
        return "pennilogic_contracts/categories.py", (
            f'"""{header}"""\nfrom __future__ import annotations\n'
            "from dataclasses import dataclass\nfrom types import MappingProxyType\nfrom typing import Final, Mapping\n"
            "from pennilogic_contracts.models.category_system_key import CategorySystemKey\n"
            "from pennilogic_contracts.models.category_nature import CategoryNature\n"
            "from pennilogic_contracts.models.category_icon import CategoryIcon\n"
            "from pennilogic_contracts.models.category_colour import CategoryColour\n\n"
            "@dataclass(frozen=True)\nclass CategoryDefaults:\n"
            "    key: CategorySystemKey\n    parent_key: CategorySystemKey | None\n    nature: CategoryNature\n"
            "    icon: CategoryIcon\n    colour: CategoryColour\n    labels: Mapping[str, str]\n"
            "    introduced_in: int\n    retired_in: int | None\n    sort_order: int\n\n"
            f"SEED_VERSION: Final[int] = {seed['seed_version']}\n"
            f"DEFAULT_LOCALE: Final[str] = {json.dumps(seed['default_locale'])}\n"
            "CATEGORY_DEFAULTS: Final[Mapping[CategorySystemKey, CategoryDefaults]] = MappingProxyType({\n" + entries + "})\n"
            "ICON_CODE_POINTS: Final[Mapping[CategoryIcon, str]] = MappingProxyType({\n" +
            "".join(f"    CategoryIcon({json.dumps(row['id'])}): {json.dumps(row['code_point'])},\n" for row in seed["icons"]) + "})\n"
            "COLOUR_RGB: Final[Mapping[CategoryColour, tuple[int, int, int]]] = MappingProxyType({\n" +
            "".join(f"    CategoryColour({json.dumps(row['id'])}): {tuple(row['rgb'])},\n" for row in seed["colours"]) + "})\n\n"
            "def category_label(key: CategorySystemKey, locale: str) -> str:\n"
            "    labels = CATEGORY_DEFAULTS[key].labels\n"
            "    return labels[locale] if locale in labels else labels[DEFAULT_LOCALE]\n"
        )
    if language == "typescript":
        entries = "".join(
            "    [" + json.dumps(row["key"]) + ", Object.freeze({ key: CategorySystemKeyFromJSON(" + json.dumps(row["key"]) +
            "), parentKey: " + ("null" if row["parent_key"] is None else "CategorySystemKeyFromJSON(" + json.dumps(row["parent_key"]) + ")") +
            ", nature: CategoryNatureFromJSON(" + json.dumps(row["nature"]) + "), icon: CategoryIconFromJSON(" +
            json.dumps(row["icon"]) + "), colour: CategoryColourFromJSON(" + json.dumps(row["colour"]) +
            "), labels: Object.freeze(" + json.dumps(row["labels"], ensure_ascii=True) + "), introducedIn: " +
            str(row["introduced_in"]) + ", retiredIn: " + json.dumps(row["retired_in"]) + ", sortOrder: " + str(row["sort_order"]) + " })],\n"
            for row in rows
        )
        return "src/categoryRegistry.ts", (
            f"// {header}\n"
            "import { type CategorySystemKey, CategorySystemKeyFromJSON } from './models/CategorySystemKey.js';\n"
            "import { type CategoryNature, CategoryNatureFromJSON } from './models/CategoryNature.js';\n"
            "import { type CategoryIcon, CategoryIconFromJSON } from './models/CategoryIcon.js';\n"
            "import { type CategoryColour, CategoryColourFromJSON } from './models/CategoryColour.js';\n\n"
            "export interface CategoryDefaults {\n    readonly key: CategorySystemKey;\n"
            "    readonly parentKey: CategorySystemKey | null;\n    readonly nature: CategoryNature;\n"
            "    readonly icon: CategoryIcon;\n    readonly colour: CategoryColour;\n"
            "    readonly labels: Readonly<Record<string, string>>;\n    readonly introducedIn: number;\n"
            "    readonly retiredIn: number | null;\n    readonly sortOrder: number;\n}\n"
            f"export const SEED_VERSION = {seed['seed_version']};\n"
            f"export const DEFAULT_LOCALE = {json.dumps(seed['default_locale'])};\n"
            "const defaults: ReadonlyMap<string, CategoryDefaults> = new Map<string, CategoryDefaults>([\n" + entries + "]);\n"
            "export function categoryDefaults(key: CategorySystemKey): CategoryDefaults {\n"
            "    const entry = defaults.get(key);\n    if (entry === undefined) throw new TypeError('category key rejected');\n"
            "    return entry;\n}\n"
            "export function categoryLabel(key: CategorySystemKey, locale: string): string {\n"
            "    const labels = categoryDefaults(key).labels;\n"
            "    const value = Object.prototype.hasOwnProperty.call(labels, locale) ? labels[locale] : labels[DEFAULT_LOCALE];\n"
            "    if (value === undefined) throw new TypeError('category label source rejected');\n    return value;\n}\n"
            "export const ICON_CODE_POINTS: Readonly<Record<string, string>> = Object.freeze(" +
            json.dumps({row["id"]: row["code_point"] for row in seed["icons"]}, ensure_ascii=True) + ");\n"
            "export const COLOUR_RGB: Readonly<Record<string, readonly [number, number, number]>> = Object.freeze(" +
            "{" + ",".join(json.dumps(row["id"]) + ": Object.freeze(" + json.dumps(row["rgb"]) +
                          ") as readonly [number, number, number]" for row in seed["colours"]) + "});\n"
        )
    if language == "kotlin":
        def literal(value: str) -> str:
            return json.dumps(value, ensure_ascii=True).replace("$", r"\$")
        entries = "".join(
            "        key(" + literal(row["key"]) + ") to CategoryDefaults(key(" + literal(row["key"]) + "), " +
            ("null" if row["parent_key"] is None else "key(" + literal(row["parent_key"]) + ")") +
            ", CategoryNature.entries.single { it.value == " + literal(row["nature"]) +
            " }, CategoryIcon.entries.single { it.value == " + literal(row["icon"]) +
            " }, CategoryColour.entries.single { it.value == " + literal(row["colour"]) + " }, Collections.unmodifiableMap(mapOf(" +
            ", ".join(literal(locale) + " to " + literal(label) for locale, label in row["labels"].items()) + ")), " +
            str(row["introduced_in"]) + ", " + ("null" if row["retired_in"] is None else str(row["retired_in"])) +
            ", " + str(row["sort_order"]) + "),\n" for row in rows
        )
        return "src/main/kotlin/com/pennilogic/contracts/categories/CategoryRegistry.kt", (
            f"// {header}\npackage com.pennilogic.contracts.categories\n\n"
            "import com.pennilogic.contracts.models.*\nimport java.util.Collections\n\n"
            "data class CategoryDefaults(val key: CategorySystemKey, val parentKey: CategorySystemKey?, "
            "val nature: CategoryNature, val icon: CategoryIcon, val colour: CategoryColour, val labels: Map<String, String>, "
            "val introducedIn: Int, val retiredIn: Int?, val sortOrder: Int)\n\n"
            "object CategoryRegistry {\n"
            f"    const val seedVersion: Int = {seed['seed_version']}\n"
            f"    const val defaultLocale: String = {literal(seed['default_locale'])}\n"
            "    private fun key(value: String): CategorySystemKey = CategorySystemKey.entries.single { it.value == value }\n"
            "    val entries: Map<CategorySystemKey, CategoryDefaults> = Collections.unmodifiableMap(mapOf(\n" + entries + "    ))\n"
            "    val iconCodePoints: Map<CategoryIcon, String> = Collections.unmodifiableMap(mapOf(\n" +
            "".join("        CategoryIcon.entries.single { it.value == " + literal(row["id"]) + " } to " +
                    literal(row["code_point"]) + ",\n" for row in seed["icons"]) + "    ))\n"
            "    val colourRgb: Map<CategoryColour, List<Int>> = Collections.unmodifiableMap(mapOf(\n" +
            "".join("        CategoryColour.entries.single { it.value == " + literal(row["id"]) + " } to Collections.unmodifiableList(listOf(" +
                    ", ".join(str(value) for value in row["rgb"]) + ")),\n" for row in seed["colours"]) + "    ))\n"
            "    fun label(key: CategorySystemKey, locale: String): String {\n"
            "        val labels = entries.getValue(key).labels\n"
            "        return labels[locale] ?: labels.getValue(defaultLocale)\n    }\n}\n"
        )
    raise PipelineError("unknown category registry target")
