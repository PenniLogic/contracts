"""Published data files: the currency registry and the money/instant fixtures are internally consistent and synthetic."""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from datetime import datetime, timezone

from support import REGISTRY, ROOT, run_script

FIXTURES = ROOT / "spec" / "fixtures"
GRAMMAR = re.compile(r"^(0(\.[0-9]+)?|-?[1-9][0-9]*(\.[0-9]+)?|-0\.[0-9]*[1-9][0-9]*)$")
INSTANT = re.compile(r"^(000[1-9]|00[1-9][0-9]|0[1-9][0-9]{2}|[1-9][0-9]{3})-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]\.[0-9]{3}Z$")


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class RegistryTest(unittest.TestCase):
    def test_registry_entries(self) -> None:
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        codes = [entry["code"] for entry in registry["currencies"]]
        self.assertEqual(codes, sorted(codes))
        self.assertEqual(len(codes), len(set(codes)))
        self.assertIn("INR", codes)
        for entry in registry["currencies"]:
            self.assertRegex(entry["code"], r"^[A-Z]{3}$")
            self.assertIsInstance(entry["exponent"], int)
            self.assertTrue(0 <= entry["exponent"] <= 6)
            self.assertTrue(entry["minor_unit_name"])
        exponents = {entry["code"]: entry["exponent"] for entry in registry["currencies"]}
        self.assertEqual(exponents["INR"], 2)
        self.assertEqual(exponents["JPY"], 0)
        self.assertEqual(exponents["KWD"], 3)


class MoneyFixtureTest(unittest.TestCase):
    fixture = load("money-wire-fixtures.v1.json")
    exponents = {e["code"]: e["exponent"] for e in json.loads(REGISTRY.read_text(encoding="utf-8"))["currencies"]}

    def test_valid_vectors_are_canonical_and_agree_with_minor_units(self) -> None:
        names = set()
        for vector in self.fixture["valid"]:
            self.assertNotIn(vector["name"], names)
            names.add(vector["name"])
            amount, currency = vector["wire"]["amount"], vector["wire"]["currency"]
            self.assertIsNotNone(GRAMMAR.fullmatch(amount), vector["name"])
            exponent = self.exponents[currency]
            fraction = amount.split(".")[1] if "." in amount else ""
            self.assertEqual(len(fraction), exponent, vector["name"])
            minor = int(amount.replace(".", "").replace("-", "")) * (-1 if amount.startswith("-") else 1)
            self.assertEqual(str(minor), vector["minor_units"], vector["name"])
            self.assertLessEqual(abs(minor), 2**63 - 1)
            self.assertLessEqual(len(amount), 21)

    def test_invalid_vectors_carry_one_known_reason_and_field(self) -> None:
        reasons = set(self.fixture["reason_order"])
        self.assertEqual(self.fixture["reason_order"], ["shape", "number_not_string", "grammar", "currency_unknown", "scale_mismatch", "out_of_range"])
        for vector in self.fixture["invalid"]:
            self.assertIn(vector["reason"], reasons, vector["name"])
            self.assertIn(vector["field"], ("amount", "currency", "scale", ""), vector["name"])
        self.assertEqual(reasons, {v["reason"] for v in self.fixture["invalid"]}, "every reason has at least one vector")

    def test_boundary_vectors_present(self) -> None:
        amounts = {(v["wire"]["currency"], v["wire"]["amount"]) for v in self.fixture["valid"]}
        for expected in [("INR", "92233720368547758.07"), ("JPY", "9223372036854775807"), ("KWD", "9223372036854775.807"), ("INR", "-92233720368547758.07"), ("JPY", "9007199254740993")]:
            self.assertIn(expected, amounts)
        invalid = {(v["wire"].get("currency") if isinstance(v["wire"], dict) else None, v["wire"].get("amount") if isinstance(v["wire"], dict) else None) for v in self.fixture["invalid"]}
        self.assertIn(("JPY", "-9223372036854775808"), invalid)
        self.assertIn(("INR", "-0.00"), invalid)


class RoundTripFixtureTest(unittest.TestCase):
    fixture = load("money-roundtrip-generated.v1.json")

    def test_committed_file_equals_the_generator_output(self) -> None:
        completed = run_script("generate_money_fixtures.py", "--check")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_header_and_rows_are_consistent(self) -> None:
        rows = self.fixture["values"]
        self.assertEqual(len(rows), self.fixture["count"])
        self.assertGreaterEqual(self.fixture["generated_count"], 10_000)
        self.assertEqual(self.fixture["boundary_count"], len([r for r in rows if "name" in r]))
        exponents = {e["code"]: e["exponent"] for e in json.loads(REGISTRY.read_text(encoding="utf-8"))["currencies"]}
        digest = hashlib.sha256()
        for row in rows:
            amount, currency = row["wire"]["amount"], row["wire"]["currency"]
            self.assertIsNotNone(GRAMMAR.fullmatch(amount))
            fraction = amount.split(".")[1] if "." in amount else ""
            self.assertEqual(len(fraction), exponents[currency])
            minor = int(amount.replace(".", "").replace("-", "")) * (-1 if amount.startswith("-") else 1)
            self.assertEqual(str(minor), row["minor_units"])
            self.assertLessEqual(abs(minor), 2**63 - 1)
            digest.update(f"{amount}|{currency}|{row['minor_units']}\n".encode("ascii"))
        self.assertEqual(digest.hexdigest(), self.fixture["round_trip_sha256"])
        self.assertEqual({r["wire"]["currency"] for r in rows}, set(exponents))

    def test_generator_is_deterministic_and_seed_sensitive(self) -> None:
        import generate_money_fixtures as gmf
        self.assertEqual(gmf.render(), gmf.render())
        self.assertNotEqual(gmf.render(seed=gmf.SEED + 1), gmf.render())
        # Reference vectors of the published SplitMix64 algorithm (seed 0): any language can reproduce the stream.
        rng = gmf.SplitMix64(0)
        self.assertEqual([rng.next() for _ in range(2)], [0xE220A8397B1DCDAF, 0x6E789E6AA1B965F4])

class InstantFixtureTest(unittest.TestCase):
    fixture = load("instant-wire-fixtures.v1.json")

    def test_valid_vectors_match_the_grammar_and_epoch(self) -> None:
        for vector in self.fixture["valid"]:
            self.assertIsNotNone(INSTANT.fullmatch(vector["wire"]), vector["name"])
            parsed = datetime.strptime(vector["wire"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
            millis = round((parsed - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds() * 1000)
            self.assertEqual(str(millis), vector["epoch_millis"], vector["name"])

    def test_invalid_vectors_carry_known_reasons(self) -> None:
        self.assertEqual(self.fixture["reason_order"], ["shape", "grammar", "calendar"])
        for vector in self.fixture["invalid"]:
            self.assertIn(vector["reason"], self.fixture["reason_order"], vector["name"])
            if vector["reason"] == "grammar":
                self.assertIsInstance(vector["wire"], str)
                self.assertIsNone(INSTANT.fullmatch(vector["wire"]), vector["name"])
            if vector["reason"] == "calendar":
                self.assertIsNotNone(INSTANT.fullmatch(vector["wire"]), vector["name"])
                with self.assertRaises(ValueError, msg=vector["name"]):
                    datetime.strptime(vector["wire"], "%Y-%m-%dT%H:%M:%S.%fZ")


if __name__ == "__main__":
    unittest.main()
