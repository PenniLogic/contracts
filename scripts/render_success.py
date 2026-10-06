"""Typed HTTP success carriers generated from declared status/schema bindings, not wire envelopes."""

from __future__ import annotations

import json

from pl_contracts import PipelineError


def render_success(language: str, operations: list[dict]) -> tuple[str, str]:
    models = sorted({case["model"] for operation in operations for case in operation["cases"] if not case["empty"]})
    if language == "typescript":
        imports = "".join(
            f"import {{ type {name}, {name}FromJSON }} from './models/{name}.js';\n" for name in models
        )
        declarations = ""
        for operation in operations:
            cases = operation["cases"]
            declarations += f"export type {operation['type']} =\n" + " |\n".join(
                f"    {{ readonly status: {case['status']};" +
                ("" if case["empty"] else f" readonly body: {case['model']};") + " }" for case in cases
            ) + ";\n"
            declarations += (
                f"export async function decode{operation['type']}(response: Response): Promise<{operation['type']}> {{\n"
                "    switch (response.status) {\n"
            )
            for case in cases:
                declarations += f"        case {case['status']}: {{\n"
                if case["empty"]:
                    declarations += (
                        "            if ((await response.text()).length !== 0) throw new ProviderWireError();\n"
                        f"            return {{ status: {case['status']} }};\n"
                    )
                else:
                    declarations += (
                        "            if ((response.headers.get('content-type') ?? '').split(';')[0]?.trim().toLowerCase() !== 'application/json') throw new ProviderWireError();\n"
                        "            let wire: unknown;\n"
                        "            try { wire = await response.json(); }\n"
                        "            catch (error: unknown) { if (error instanceof SyntaxError) throw new ProviderWireError(); throw error; }\n"
                        f"            validateProvider('{case['validator']}', wire, false);\n"
                        f"            return {{ status: {case['status']}, body: {case['model']}FromJSON(wire) }};\n"
                    )
                declarations += "        }\n"
            declarations += "        default: throw new ProviderWireError();\n    }\n}\n\n"
        return "src/successResponses.ts", (
            "// Generated HTTP success carriers; never serialized as a new API wire envelope.\n" +
            imports + "import { ProviderWireError } from './providerGuard.js';\n"
            "import { validateProvider } from './providerConstraints.js';\n\n" + declarations
        )
    if language == "python":
        imports = "".join(
            f"from pennilogic_contracts.models.{next(case['module'] for operation in operations for case in operation['cases'] if case.get('model') == name)} import {name}\n"
            for name in models
        )
        declarations = ""
        for operation in operations:
            for case in operation["cases"]:
                declarations += f"@dataclass(frozen=True)\nclass {case['caseType']}:\n"
                if not case["empty"]:
                    declarations += f"    body: {case['model']}\n"
                declarations += f"    status: Literal[{case['status']}] = field(init=False, default={case['status']})\n\n"
            declarations += f"{operation['type']} = Union[" + ", ".join(case["caseType"] for case in operation["cases"]) + "]\n\n"
            declarations += (
                f"def decode_{operation['pythonOperation']}(response: RESTResponse) -> {operation['type']}:\n"
                "    data = response.read()\n"
            )
            for case in operation["cases"]:
                declarations += f"    if response.status == {case['status']}:\n"
                if case["empty"]:
                    declarations += f"        if data != b'':\n            raise ProviderWireError()\n        return {case['caseType']}()\n"
                else:
                    declarations += (
                        "        if str(response.getheader('Content-Type', '')).split(';')[0].strip().lower() != 'application/json':\n"
                        "            raise ProviderWireError()\n"
                        f"        try:\n            wire_{case['status']}: object = json.loads(data)\n"
                        "        except (ValueError, UnicodeError):\n            raise ProviderWireError() from None\n"
                        f"        if not isinstance(wire_{case['status']}, dict):\n            raise ProviderWireError()\n"
                        f"        validate_provider('{case['validator']}', wire_{case['status']})\n"
                        f"        return {case['caseType']}({case['model']}.from_dict(wire_{case['status']}))\n"
                    )
            declarations += "    raise ProviderWireError()\n\n"
        return "pennilogic_contracts/success_responses.py", (
            '"""Generated typed HTTP success carriers; not new wire envelopes."""\n'
            "from __future__ import annotations\nimport json\nfrom dataclasses import dataclass, field\n"
            "from typing import Literal, Union\nfrom pennilogic_contracts.rest import RESTResponse\n"
            "from pennilogic_contracts.provider_model import ProviderWireError\n"
            "from pennilogic_contracts.provider_constraints import validate_provider\n" + imports + "\n" + declarations
        )
    if language == "kotlin":
        imports = "".join(f"import com.pennilogic.contracts.models.{name}\n" for name in models)
        declarations = ""
        for operation in operations:
            declarations += f"sealed interface {operation['type']} {{\n    val status: Int\n}}\n"
            for case in operation["cases"]:
                declarations += (
                    f"data class {case['caseType']}(" +
                    ("" if case["empty"] else f"val body: {case['model']}") +
                    f") : {operation['type']} {{\n    override val status: Int get() = {case['status']}\n}}\n"
                ) if not case["empty"] else (
                    f"data object {case['caseType']} : {operation['type']} {{\n    override val status: Int get() = {case['status']}\n}}\n"
                )
            declarations += f"fun decode{operation['type']}(status: Int, wire: JsonElement): {operation['type']} = when (status) {{\n"
            for case in operation["cases"]:
                if case["empty"]:
                    declarations += f"    {case['status']} -> {{ if (wire != JsonNull) throw ProviderWireException(); {case['caseType']} }}\n"
                else:
                    declarations += (
                        f"    {case['status']} -> {{\n        ProviderConstraints.validate(\"{case['validator']}\", wire)\n"
                        f"        {case['caseType']}(PennilogicJson.json.decodeFromJsonElement<{case['model']}>(wire))\n    }}\n"
                    )
            declarations += "    else -> throw ProviderWireException()\n}\n\n"
        return "src/main/kotlin/com/pennilogic/contracts/success/SuccessResponses.kt", (
            "// Generated typed HTTP success carriers; never a new API wire envelope.\n"
            "package com.pennilogic.contracts.success\n\n" + imports +
            "import com.pennilogic.contracts.serialization.*\nimport kotlinx.serialization.json.*\n\n" + declarations
        )
    raise PipelineError("unknown success response target")
