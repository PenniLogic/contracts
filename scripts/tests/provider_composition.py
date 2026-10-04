"""Synthetic controls on freshly generated closed compositions, not product DTOs."""

from __future__ import annotations

import copy
import json
import os
import unittest
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, TypeAdapter

from pennilogic_contracts import ApiClient
from pennilogic_contracts.models.instant import Instant
from pennilogic_contracts.models.local_date import LocalDate
from pennilogic_contracts.models.money import Money
from pennilogic_contracts.models.problem_code import ProblemCode
from pennilogic_contracts.models.synthetic_legacy_envelope import SyntheticLegacyEnvelope
from pennilogic_contracts.models.synthetic_provider_envelope import SyntheticProviderEnvelope
from pennilogic_contracts.models.synthetic_provider_record import SyntheticProviderRecord


ROOT = Path(os.environ["PL_CONTRACTS_ROOT"])


def fixture(name: str) -> dict[str, Any]:
    value: dict[str, Any] = json.loads((ROOT / "spec" / "fixtures" / name).read_text(encoding="utf-8"))
    return value


def control() -> dict[str, Any]:
    return {
        "money": {"amount": "90071992547409.93", "currency": "INR"},
        "recorded_at": "2026-09-30T04:52:08.439Z", "booked_on": "2026-09-30",
        "currency_code": "INR", "zone": "Asia/Kolkata",
        "public_id": "cor_00000000-0000-4000-8000-000000000001",
        "values": [0, 10], "flags": [False, True], "codes": ["request_failed"],
        "preview": fixture("import-provider.v1.json")["preview"],
        "refusal": fixture("error-provider.v1.json")["refusal"],
    }


def sanitize(value: object) -> object:
    writer: Callable[[object], object] = ApiClient().sanitize_for_serialization
    return writer(value)


class ProviderCompositionTest(unittest.TestCase):
    def safe_rejection(self, operation: Callable[[], object]) -> None:
        with self.assertRaises(ValueError) as caught:
            operation()
        self.assertNotIn("PRIVATE_SYNTHETIC_CANARY", str(caught.exception))
        self.assertNotIn("90071992547409.93", str(caught.exception))

    def test_money_and_all_primitive_refs_on_each_normal_writer(self) -> None:
        wire = control()
        value = SyntheticProviderRecord.from_dict(wire)
        self.assertIsInstance(value.money, Money)
        self.assertIsInstance(value.recorded_at, Instant)
        self.assertIsInstance(value.booked_on, LocalDate)
        self.assertIsInstance(value.codes[0], ProblemCode)
        self.assertEqual(value.money.minor_units, 9007199254740993)
        writers: dict[str, Callable[[], object]] = {
            "to_dict": value.to_dict,
            "model_dump_json": lambda: json.loads(value.model_dump_json()),
            "to_json": lambda: json.loads(value.to_json()),
            "typeadapter_dump_json": lambda: json.loads(TypeAdapter(SyntheticProviderRecord).dump_json(value)),
            "api_client_sanitize": lambda: sanitize(value),
        }
        outcomes = {}
        for name, write in writers.items():
            try:
                outcomes[name] = write() == wire
            except ValueError:
                outcomes[name] = False
        self.assertEqual(outcomes, {name: True for name in writers})

    def test_native_money_time_enum_construction_and_generic_nested_serialization(self) -> None:
        wire = control()
        value = SyntheticProviderRecord.model_validate({
            **wire, "money": Money(9007199254740993, "INR"),
            "recorded_at": Instant.from_wire(wire["recorded_at"]), "booked_on": LocalDate.from_wire(wire["booked_on"]),
            "codes": [ProblemCode.REQUEST_FAILED],
        })
        envelope_wire = {"record": wire, "records": [wire]}
        envelope = SyntheticProviderEnvelope(record=value, records=[value])
        self.assertEqual(envelope.to_dict(), envelope_wire)
        self.assertEqual(json.loads(TypeAdapter(SyntheticProviderEnvelope).dump_json(envelope)), envelope_wire)
        class GenericEnvelope(BaseModel):
            record: SyntheticProviderRecord
        self.assertEqual(json.loads(GenericEnvelope(record=value).model_dump_json()), {"record": wire})
        self.assertEqual(sanitize(envelope), envelope_wire)
        parsed = ApiClient().deserialize(json.dumps(envelope_wire), "SyntheticProviderEnvelope", "application/json")
        self.assertEqual(parsed.to_dict(), envelope_wire)
        self.assertEqual(sanitize(parsed), envelope_wire)

    def test_optional_strict_ref_omits_absence_and_rejects_null_unknown_or_private_nested_state(self) -> None:
        wire = control()
        problem = fixture("import-provider.v1.json")["override_rejection"]
        self.assertEqual(SyntheticProviderRecord.from_dict({**wire, "problem": problem}).to_dict(), {**wire, "problem": problem})
        self.safe_rejection(lambda: SyntheticProviderRecord.from_dict({**wire, "problem": None}))
        self.safe_rejection(lambda: SyntheticProviderRecord.from_dict({
            **wire, "problem": {**problem, "provider_detail": "PRIVATE_SYNTHETIC_CANARY"},
        }))
        self.safe_rejection(lambda: SyntheticProviderRecord.from_dict({
            **wire, "refusal": {**wire["refusal"], "message": "PRIVATE_SYNTHETIC_CANARY"},
        }))
        self.safe_rejection(lambda: SyntheticProviderRecord.from_dict({**wire, "provider_detail": "PRIVATE_SYNTHETIC_CANARY"}))
        value = SyntheticProviderRecord.from_dict(wire)
        self.assertNotIn("problem", value.to_dict())
        bad = value.model_copy(update={"provider_detail": "PRIVATE_SYNTHETIC_CANARY"})
        self.safe_rejection(lambda: sanitize(bad))
        self.safe_rejection(lambda: TypeAdapter(SyntheticProviderRecord).dump_json(bad))
        legacy = SyntheticLegacyEnvelope.from_dict({"future_member": True})
        legacy_wire = sanitize(legacy)
        if not isinstance(legacy_wire, dict):
            self.fail("legacy serializer did not return an object")
        self.assertNotIn("problem", legacy_wire)

    def test_model_and_primitive_array_items_are_strict_on_native_read_and_write(self) -> None:
        wire = control()
        for field in ("values", "flags", "codes"):
            self.safe_rejection(lambda: SyntheticProviderRecord.from_dict({**wire, field: [None]}))
            value = SyntheticProviderRecord.from_dict(wire).model_copy(update={field: [None]})
            self.safe_rejection(lambda: sanitize(value))
        invalid = copy.deepcopy(wire)
        invalid["preview"]["rows"].append(None)
        self.safe_rejection(lambda: SyntheticProviderRecord.from_dict(invalid))
        value = SyntheticProviderRecord.from_dict(wire)
        envelope = SyntheticProviderEnvelope(record=value, records=[value])
        bad = envelope.model_copy(update={"records": [None]})
        self.safe_rejection(lambda: TypeAdapter(SyntheticProviderEnvelope).dump_json(bad))
        self.safe_rejection(lambda: sanitize(bad))

    def test_original_money_codec_currency_scale_range_and_time_rejections_are_not_normalized(self) -> None:
        for money in (
            {"amount": "-0.001", "currency": "KWD"}, {"amount": "0", "currency": "JPY"},
            {"amount": "-92233720368547758.07", "currency": "INR"},
        ):
            wire = {**control(), "money": money}
            self.assertEqual(sanitize(SyntheticProviderRecord.from_dict(wire)), wire)
        for invalid_money in (
            {"amount": 12.34, "currency": "INR"}, {"amount": "12.3", "currency": "INR"},
            {"amount": "-92233720368547758.08", "currency": "INR"},
            {"amount": "PRIVATE_SYNTHETIC_CANARY", "currency": "INR"},
            {"amount": "0.00", "currency": "INR", "provider_detail": "PRIVATE_SYNTHETIC_CANARY"},
        ):
            self.safe_rejection(lambda: SyntheticProviderRecord.from_dict({**control(), "money": invalid_money}))
        self.safe_rejection(lambda: SyntheticProviderRecord.from_dict({**control(), "recorded_at": "2026-02-30T00:00:00.000Z"}))
        self.safe_rejection(lambda: SyntheticProviderRecord.from_dict({**control(), "booked_on": "2026-02-30"}))
        for field, invalid in (("currency_code", "inr"), ("zone", "1invalid"),
                               ("public_id", "rec_00000000-0000-4000-8000-000000000001")):
            self.safe_rejection(lambda: SyntheticProviderRecord.from_dict({**control(), field: invalid}))


if __name__ == "__main__":
    unittest.main()
