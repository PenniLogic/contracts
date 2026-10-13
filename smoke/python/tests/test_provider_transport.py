"""Provider-only safety on ordinary generated conversion, construction and transport."""

from __future__ import annotations

import copy
from io import BytesIO
import json
import unittest
from typing import Any, Callable, Iterator
import pennilogic_contracts.models as generated_models

from fixtures import load
from pennilogic_contracts import ApiClient
from pennilogic_contracts.models import AiRefusal, ImportPreview, ServiceProblemDetail
from pennilogic_contracts.provider_model import ProviderModel
from pennilogic_contracts.rest import RESTResponse
from pydantic import BaseModel
from urllib3 import HTTPResponse


def transport_write(value: object) -> object:
    serialize: Callable[[object], object] = ApiClient().sanitize_for_serialization
    return serialize(value)


def specimens(*, arrays: bool = False) -> Iterator[tuple[str, dict[str, Any]]]:
    inventory = load("provider-transport.v1.json")
    for entry in inventory["models"] + (inventory["array_controls"] if arrays else []):
        value = load(entry["fixture"])
        for key in entry["path"]:
            value = value[key]
        yield entry["schema"], {**value, **entry.get("set", {})}


def quoted_primitives(value: Any) -> Iterator[Any]:
    if isinstance(value, dict):
        for key, child in value.items():
            for mutated in quoted_primitives(child):
                yield {**value, key: mutated}
    elif isinstance(value, list):
        for index, child in enumerate(value):
            for mutated in quoted_primitives(child):
                result = copy.deepcopy(value)
                result[index] = mutated
                yield result
    elif type(value) in (int, bool):
        yield json.dumps(value)

def null_array_entries(value: Any) -> Iterator[Any]:
    if isinstance(value, dict):
        for key, child in value.items():
            for mutated in null_array_entries(child):
                yield {**value, key: mutated}
    elif isinstance(value, list):
        yield [*value, None]
        for index, child in enumerate(value):
            for mutated in null_array_entries(child):
                result = copy.deepcopy(value)
                result[index] = mutated
                yield result


class ProviderTransportTest(unittest.TestCase):
    def test_marked_null_roots_fail_before_transport_conversion_but_explicit_optional_and_legacy_remain(self) -> None:
        client = ApiClient()
        count = 0
        for name, _wire in specimens():
            model = getattr(generated_models, name)
            for raw, response_type in (
                ("null", name), ("null", model), ("[null]", f"List[{name}]"),
                ('{"value":null}', f"Dict[str, {name}]"),
            ):
                with self.subTest(name=name, response_type=response_type), self.assertRaises(ValueError):
                    client.deserialize(raw, response_type, "application/json")
            self.assertIsNone(client.deserialize("null", f"Optional[{name}]", "application/json"))
            self.assertEqual(client.deserialize("[null]", f"List[Optional[{name}]]", "application/json"), [None])
            count += 1
        self.assertEqual(len(load("provider-transport.v1.json")["models"][:29]), 29)
        self.assertEqual(count, 112)
        for legacy in ("ProblemDetail", "object"):
            self.assertIsNone(client.deserialize("null", legacy, "application/json"))
        response = RESTResponse(HTTPResponse(body=BytesIO(b""), status=204, preload_content=False))
        response.read()
        self.assertIsNone(client.response_deserialize(response, {"204": None}).data)

    def test_every_nested_model_and_primitive_array_rejects_null_before_transport(self) -> None:
        cases = 0
        legacy_cases = 0
        legacy = {row["schema"] for row in load("provider-transport.v1.json")["models"][:29]}
        for name, wire in specimens(arrays=True):
            model = getattr(generated_models, name)
            for invalid in null_array_entries(wire):
                for read in (model.from_dict, model.model_validate,
                             lambda data: ApiClient().deserialize(json.dumps(data), name, "application/json")):
                    with self.assertRaises(ValueError):
                        read(invalid)
                cases += 1
                if name in legacy:
                    legacy_cases += 1
        self.assertEqual(legacy_cases, 15)
        self.assertEqual(cases, 43)

    def test_successful_refusal_rejects_unknown_content_in_ordinary_conversion_and_transport(self) -> None:
        valid = load("error-provider.v1.json")["refusal"]
        invalid = {**valid, "provider_detail": "PRIVATE_SYNTHETIC_CANARY"}
        with self.assertRaises(ValueError):
            AiRefusal.from_dict(invalid)
        with self.assertRaises(ValueError):
            ApiClient().deserialize(json.dumps(invalid), "AiRefusal", "application/json")

    def test_successful_refusal_rejects_bad_correlation_and_unsafe_message_with_safe_diagnostics(self) -> None:
        valid = load("error-provider.v1.json")["refusal"]
        for invalid in (
            {**valid, "correlation_id": "PRIVATE_SYNTHETIC_CANARY"},
            {**valid, "message": "PRIVATE_SYNTHETIC_CANARY"},
            {**valid, "code": "PRIVATE_SYNTHETIC_CANARY"},
        ):
            with self.assertRaises(ValueError) as caught:
                AiRefusal.from_dict(invalid)
            self.assertNotIn("PRIVATE_SYNTHETIC_CANARY", str(caught.exception))

    def test_ordinary_import_model_rejects_original_nested_unknown_wire_members(self) -> None:
        invalid: dict[str, Any] = copy.deepcopy(load("import-provider.v1.json")["preview"])
        invalid["mapping"]["provider_detail"] = "PRIVATE_SYNTHETIC_CANARY"
        with self.assertRaises(ValueError):
            ImportPreview.from_dict(invalid)

    def test_service_problem_stays_strict_in_generated_transport_and_nested_import(self) -> None:
        preview: dict[str, Any] = copy.deepcopy(load("import-provider.v1.json")["preview"])
        problem = preview["rows"][2]["row_error"]["problem"]
        problem["provider_detail"] = "PRIVATE_SYNTHETIC_CANARY"
        with self.assertRaises(ValueError):
            ServiceProblemDetail.from_dict(problem)
        with self.assertRaises(ValueError):
            ApiClient().deserialize(json.dumps(problem), "ServiceProblemDetail", "application/problem+json")
        with self.assertRaises(ValueError):
            ApiClient().deserialize(json.dumps(preview), "ImportPreview", "application/json")

    def test_every_closed_provider_uses_ordinary_strict_construction_read_write_and_transport(self) -> None:
        count = 0
        for name, wire in specimens():
            model = getattr(generated_models, name)
            self.assertTrue(isinstance(model, type) and issubclass(model, ProviderModel), name)
            value = model.from_dict(wire)
            self.assertEqual(value.to_dict(), wire, name)
            self.assertEqual(json.loads(value.model_dump_json()), wire, name)
            self.assertEqual(transport_write(value), wire, name)
            self.assertEqual(ApiClient().deserialize(json.dumps(wire), name, "application/json").to_dict(), wire, name)
            self.assertNotIn("additional_properties", model.model_fields, name)
            for member, field in model.model_fields.items():
                if field.is_required():
                    invalid = {key: item for key, item in wire.items() if key != (field.alias or member)}
                    with self.assertRaises(ValueError):
                        model.from_dict(invalid)
                    with self.assertRaises(ValueError):
                        ApiClient().deserialize(json.dumps(invalid), name, "application/json")
            for invalid in ({**wire, "provider_detail": "PRIVATE_SYNTHETIC_CANARY"},
                            {**wire, "PRIVATE_SYNTHETIC_CANARY": "synthetic"}):
                for read in (model.from_dict, model.model_validate, lambda data: model(**data)):
                    with self.assertRaises(ValueError) as caught:
                        read(invalid)
                    self.assertNotIn("PRIVATE_SYNTHETIC_CANARY", str(caught.exception), name)
                with self.assertRaises(ValueError):
                    ApiClient().deserialize(json.dumps(invalid), name, "application/json")
                copied = value.model_copy(update={"provider_detail": "PRIVATE_SYNTHETIC_CANARY"})
                for write in (copied.to_dict, copied.to_json, copied.model_dump, copied.model_dump_json):
                    with self.assertRaises(ValueError) as caught:
                        write()
                    self.assertNotIn("PRIVATE_SYNTHETIC_CANARY", str(caught.exception), name)
            count += 1
        self.assertEqual(count, 112)

    def test_every_integer_and_boolean_leaf_rejects_quoted_values_before_model_conversion(self) -> None:
        cases = 0
        for name, wire in specimens():
            model = getattr(generated_models, name)
            for invalid in quoted_primitives(wire):
                with self.assertRaises(ValueError):
                    model.from_dict(invalid)
                with self.assertRaises(ValueError):
                    ApiClient().deserialize(json.dumps(invalid), name, "application/json")
                cases += 1
        self.assertGreaterEqual(cases, 50)

    def test_invalid_native_primitive_construction_and_outbound_state_is_rejected(self) -> None:
        for name, wire in specimens():
            model = getattr(generated_models, name)
            value = model.from_dict(wire)
            for key, item in wire.items():
                if type(item) not in (int, bool):
                    continue
                invalid = {**wire, key: json.dumps(item)}
                with self.assertRaises(ValueError):
                    model(**invalid)
                member = next(member for member, field in model.model_fields.items() if (field.alias or member) == key)
                copied = value.model_copy(update={member: json.dumps(item)})
                with self.assertRaises(ValueError):
                    transport_write(copied)
                with self.assertRaises(ValueError):
                    copied.model_dump_json()

    def test_actual_generated_column_bounds_accept_256_and_reject_257(self) -> None:
        wire: dict[str, Any] = copy.deepcopy(load("import-provider.v1.json")["preview"])
        wire["mapping"]["column_count"] = 256
        wire["mapping"]["unmapped_columns"] = list(range(4, 257))
        value = ImportPreview.from_dict(wire)
        self.assertEqual(value.mapping.column_count, 256)
        self.assertEqual(transport_write(value), wire)
        wire["mapping"]["column_count"] = 257
        with self.assertRaises(ValueError):
            ImportPreview.from_dict(wire)
        with self.assertRaises(ValueError):
            ApiClient().deserialize(json.dumps(wire), "ImportPreview", "application/json")

    def test_all_successful_refusal_failures_are_safe_in_read_write_and_nested_serialization(self) -> None:
        valid = load("error-provider.v1.json")["refusal"]
        for negative in load("provider-transport.v1.json")["refusal_negatives"]:
            invalid = {**valid, **negative.get("set", {})}
            for key in negative.get("remove", []):
                invalid.pop(key)
            for read in (AiRefusal.from_dict, AiRefusal.model_validate,
                         lambda wire: ApiClient().deserialize(json.dumps(wire), "AiRefusal", "application/json")):
                with self.assertRaises(ValueError) as caught:
                    read(invalid)
                self.assertNotIn("PRIVATE_SYNTHETIC_CANARY", str(caught.exception))
        class Envelope(BaseModel):
            refusal: AiRefusal
        normal = Envelope(refusal=AiRefusal.from_dict(valid))
        self.assertEqual(normal.model_dump(mode="json"), {"refusal": valid})
        corrupted = normal.refusal.model_copy(update={"correlation_id": "PRIVATE_SYNTHETIC_CANARY"})
        writers: tuple[Callable[[], object], ...] = (
            corrupted.to_dict, corrupted.model_dump_json,
            lambda: Envelope.model_construct(refusal=corrupted).model_dump_json(),
            lambda: transport_write(corrupted),
        )
        for write in writers:
            with self.assertRaises(ValueError) as caught:
                write()
            self.assertNotIn("PRIVATE_SYNTHETIC_CANARY", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
