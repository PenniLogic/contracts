"""InstantWireConformanceTest (ADR-015 §3.1) for the generated Python client's instant seam."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ValidationError

from fixtures import load
from pennilogic_contracts.models.instant import REASONS, Instant, InstantWireError


class Stamped(BaseModel):
    at: Instant


class InstantConformanceTest(unittest.TestCase):
    fixtures = load("instant-wire-fixtures.v1.json")

    def test_reason_order_matches_fixture(self) -> None:
        self.assertEqual(list(REASONS), self.fixtures["reason_order"])

    def test_valid_vectors_round_trip(self) -> None:
        for vector in self.fixtures["valid"]:
            with self.subTest(vector["name"]):
                instant = Instant.from_wire(vector["wire"])
                self.assertEqual(instant.epoch_millis, int(vector["epoch_millis"]))
                self.assertEqual(instant.to_wire(), vector["wire"])
                self.assertEqual(Instant(int(vector["epoch_millis"])), instant)
                self.assertEqual(Instant.from_datetime(instant.to_datetime()), instant)

    def test_invalid_vectors_rejected_with_exact_reason(self) -> None:
        for vector in self.fixtures["invalid"]:
            with self.subTest(vector["name"]):
                with self.assertRaises(InstantWireError) as caught:
                    Instant.from_wire(vector["wire"])
                self.assertEqual(caught.exception.reason, vector["reason"])

    def test_parse_rejects_non_string_as_type_error(self) -> None:
        with self.assertRaises(TypeError):
            Instant.parse(1790743928439)  # type: ignore[arg-type]

    def test_from_datetime_requires_aware_millisecond_value(self) -> None:
        with self.assertRaises(TypeError):
            Instant.from_datetime(datetime(2026, 9, 30, 4, 52, 8, 439000))
        with self.assertRaises(ValueError):
            Instant.from_datetime(datetime(2026, 9, 30, 4, 52, 8, 439001, tzinfo=timezone.utc))
        offset = timezone(timedelta(hours=5, minutes=30))
        local = datetime(2026, 9, 30, 10, 22, 8, 439000, tzinfo=offset)
        self.assertEqual(Instant.from_datetime(local).to_wire(), "2026-09-30T04:52:08.439Z")

    def test_year_bounds(self) -> None:
        with self.assertRaises(InstantWireError):
            Instant(-62135596800001)
        with self.assertRaises(InstantWireError):
            Instant(253402300800000)

    def test_ordering_and_hash(self) -> None:
        earlier, later = Instant(0), Instant(1)
        self.assertLess(earlier, later)
        self.assertEqual(len({earlier, Instant(0)}), 1)
        with self.assertRaises(TypeError):
            earlier < 5  # noqa: B015
        self.assertFalse(earlier == 0)

    def test_pydantic_seam_round_trip(self) -> None:
        stamped = Stamped.model_validate_json('{"at": "2026-09-30T04:52:08.439Z"}')
        self.assertEqual(stamped.at.epoch_millis, 1790743928439)
        self.assertEqual(stamped.model_dump(mode="json"), {"at": "2026-09-30T04:52:08.439Z"})
        with self.assertRaises(ValidationError):
            Stamped.model_validate({"at": "2026-09-30T04:52:08.439+05:30"})
        with self.assertRaises(ValidationError):
            Stamped.model_validate({"at": 1790743928439})


if __name__ == "__main__":
    unittest.main()
