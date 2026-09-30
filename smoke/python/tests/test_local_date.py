"""Date-only seam tests (ADR-015 §3.1) for the generated Python client's LocalDate."""

from __future__ import annotations

import unittest
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, ValidationError

from pennilogic_contracts.models.local_date import REASONS, LocalDate, LocalDateWireError


class Dated(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)

    booked_on: LocalDate


class LocalDateTest(unittest.TestCase):
    def test_reasons(self) -> None:
        self.assertEqual(REASONS, ("shape", "grammar", "calendar"))

    def test_valid_dates_round_trip(self) -> None:
        for text in ("2026-09-30", "0001-01-01", "9999-12-31", "2024-02-29", "2000-02-29"):
            with self.subTest(text):
                value = LocalDate.from_wire(text)
                self.assertEqual(value.to_wire(), text)
                self.assertEqual(LocalDate(value.to_date()), value)
                self.assertEqual(LocalDate.parse(text), value)

    def test_invalid_dates(self) -> None:
        for text, reason in (
            ("2026-9-30", "grammar"), ("2026-09-30T00:00:00.000Z", "grammar"), ("0000-01-01", "grammar"), ("2026-13-01", "grammar"),
            ("2026-09-30 ", "grammar"), ("2026-09-30\n", "grammar"), ("", "grammar"),
            ("2026-02-30", "calendar"), ("2023-02-29", "calendar"), ("1900-02-29", "calendar"), ("2026-04-31", "calendar"),
        ):
            with self.subTest(text):
                with self.assertRaises(LocalDateWireError) as caught:
                    LocalDate.from_wire(text)
                self.assertEqual(caught.exception.reason, reason)
        with self.assertRaises(LocalDateWireError) as shape:
            LocalDate.from_wire(20260930)
        self.assertEqual(shape.exception.reason, "shape")

    def test_constructor_refuses_datetime_and_non_dates(self) -> None:
        with self.assertRaises(TypeError):
            LocalDate(datetime(2026, 9, 30))  # a datetime would smuggle a time of day
        with self.assertRaises(TypeError):
            LocalDate("2026-09-30")  # type: ignore[arg-type]
        self.assertEqual(LocalDate.of(2026, 9, 30).to_date(), date(2026, 9, 30))
        with self.assertRaises(LocalDateWireError):
            LocalDate.of(2026, 2, 30)

    def test_ordering_and_hash(self) -> None:
        self.assertLess(LocalDate.parse("2026-09-29"), LocalDate.parse("2026-09-30"))
        self.assertEqual(len({LocalDate.parse("2026-09-30"), LocalDate.parse("2026-09-30")}), 1)
        with self.assertRaises(TypeError):
            LocalDate.parse("2026-09-30") < "2026-10-01"  # noqa: B015
        self.assertFalse(LocalDate.parse("2026-09-30") == "2026-09-30")

    def test_pydantic_seam_under_strict_config(self) -> None:
        # strict=True on the generated models must still accept the date-only wire string through the seam.
        dated = Dated.model_validate({"booked_on": "2026-09-30"})
        self.assertEqual(dated.booked_on, LocalDate.parse("2026-09-30"))
        self.assertEqual(dated.model_dump(mode="json"), {"booked_on": "2026-09-30"})
        self.assertEqual(Dated.model_validate_json('{"booked_on": "2026-09-30"}'), dated)
        with self.assertRaises(ValidationError) as caught:
            Dated.model_validate({"booked_on": "2026-02-30"})
        self.assertIn("calendar", str(caught.exception))
        self.assertNotIn("2026-02-30", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
