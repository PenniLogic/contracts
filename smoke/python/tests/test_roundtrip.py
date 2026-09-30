"""CrossLanguageMoneyRoundTripTest (ADR-015 §7) — Python leg over spec/fixtures/money-roundtrip-generated.v1.json."""

from __future__ import annotations

import hashlib
import unittest

from fixtures import load
from pennilogic_contracts.models.money import MAX_MINOR_UNITS, Money


class CrossLanguageMoneyRoundTripTest(unittest.TestCase):
    fixture = load("money-roundtrip-generated.v1.json")

    def test_header_is_consistent(self) -> None:
        rows = self.fixture["values"]
        self.assertEqual(len(rows), self.fixture["count"])
        self.assertEqual(self.fixture["boundary_count"] + self.fixture["generated_count"], self.fixture["count"])
        self.assertGreaterEqual(self.fixture["generated_count"], 10_000)
        self.assertRegex(self.fixture["seed"], r"^0x[0-9A-F]{16}$")
        self.assertEqual({row["wire"]["currency"] for row in rows}, {"INR", "JPY", "KWD"})

    def test_every_value_round_trips_and_the_emitted_lines_hash_to_the_shared_digest(self) -> None:
        digest = hashlib.sha256()
        for row in self.fixture["values"]:
            money = Money.from_wire(row["wire"])
            wire = money.to_wire()
            self.assertEqual(wire, row["wire"], row.get("name", row["wire"]))
            self.assertEqual(str(money.minor_units), row["minor_units"], row.get("name", row["wire"]))
            self.assertEqual(Money(money.minor_units, money.currency), money)
            digest.update(f"{wire['amount']}|{wire['currency']}|{money.minor_units}\n".encode("ascii"))
        self.assertEqual(digest.hexdigest(), self.fixture["round_trip_sha256"])

    def test_boundary_rows_are_present_for_every_currency(self) -> None:
        named = {row["name"]: row for row in self.fixture["values"] if "name" in row}
        for code in ("INR", "JPY", "KWD"):
            self.assertEqual(int(named[f"maximum {code}"]["minor_units"]), MAX_MINOR_UNITS)
            self.assertEqual(int(named[f"minimum {code}"]["minor_units"]), -MAX_MINOR_UNITS)
            self.assertEqual(int(named[f"2^53+1 {code}"]["minor_units"]), 2**53 + 1)
            self.assertEqual(int(named[f"zero {code}"]["minor_units"]), 0)


if __name__ == "__main__":
    unittest.main()
