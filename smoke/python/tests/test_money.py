"""PythonMoneyWrapperTest and MoneyWireConformanceTest (ADR-015 §7) for the generated Python client."""

from __future__ import annotations

import decimal
import json
import unittest
from typing import Any

from pydantic import BaseModel, ValidationError

from fixtures import load
from pennilogic_contracts.models.currency_registry import REGISTRY
from pennilogic_contracts.models.money import MAX_MINOR_UNITS, MONEY_CONTEXT, REASONS, Money, MoneyWireError


class Holder(BaseModel):
    total: Money


class FloatRejectionTest(unittest.TestCase):
    """The acceptance criterion: money is never a float and float construction is rejected."""

    def test_constructor_rejects_float(self) -> None:
        with self.assertRaises(TypeError):
            Money(1.5, "INR")  # type: ignore[arg-type]

    def test_constructor_rejects_bool_decimal_and_str(self) -> None:
        for bad in (True, decimal.Decimal("1"), "100"):
            with self.subTest(value=type(bad).__name__), self.assertRaises(TypeError):
                Money(bad, "INR")  # type: ignore[arg-type]

    def test_parse_rejects_float_and_int(self) -> None:
        for bad in (1.5, 150, decimal.Decimal("1.50")):
            with self.subTest(value=type(bad).__name__), self.assertRaises(TypeError):
                Money.parse(bad, "INR")  # type: ignore[arg-type]

    def test_from_wire_rejects_json_numbers_with_reason(self) -> None:
        for bad in (1.5, 150):
            with self.subTest(value=bad), self.assertRaises(MoneyWireError) as caught:
                Money.from_wire({"amount": bad, "currency": "INR"})
            self.assertEqual(caught.exception.reason, "number_not_string")
            self.assertEqual(caught.exception.field, "amount")

    def test_wrapper_is_decimal_backed(self) -> None:
        money = Money(-123456, "INR")
        self.assertIsInstance(money.amount, decimal.Decimal)
        self.assertEqual(money.amount, decimal.Decimal("-1234.56"))
        self.assertEqual(money.minor_units, -123456)
        self.assertEqual(money.currency, "INR")
        self.assertNotIsInstance(money.minor_units, float)

    def test_float_operation_trap_in_context(self) -> None:
        with decimal.localcontext(MONEY_CONTEXT), self.assertRaises(decimal.FloatOperation):
            decimal.Decimal(1.1)

    def test_pydantic_model_rejects_json_number(self) -> None:
        with self.assertRaises(ValidationError):
            Holder.model_validate({"total": {"amount": 1234.56, "currency": "INR"}})
        with self.assertRaises(ValidationError):
            Holder.model_validate_json('{"total": {"amount": 1234.56, "currency": "INR"}}')


class WireConformanceTest(unittest.TestCase):
    fixtures = load("money-wire-fixtures.v1.json")

    def test_reason_order_matches_fixture(self) -> None:
        self.assertEqual(list(REASONS), self.fixtures["reason_order"])

    def test_valid_vectors_round_trip(self) -> None:
        for vector in self.fixtures["valid"]:
            with self.subTest(vector["name"]):
                money = Money.from_wire(vector["wire"])
                self.assertEqual(money.minor_units, int(vector["minor_units"]))
                self.assertEqual(money.to_wire(), vector["wire"])
                self.assertEqual(Money.parse(vector["wire"]["amount"], vector["wire"]["currency"]), money)
                self.assertEqual(Money(int(vector["minor_units"]), vector["wire"]["currency"]), money)
                self.assertEqual(hash(money), hash(Money.from_wire(vector["wire"])))

    def test_invalid_vectors_rejected_with_exact_reason(self) -> None:
        for vector in self.fixtures["invalid"]:
            with self.subTest(vector["name"]):
                with self.assertRaises(MoneyWireError) as caught:
                    Money.from_wire(vector["wire"])
                self.assertEqual(caught.exception.reason, vector["reason"])
                self.assertEqual(caught.exception.field, vector["field"])
                self.assertNotIn(json.dumps(vector["wire"]), str(caught.exception))

    def test_rejection_message_never_echoes_the_value(self) -> None:
        with self.assertRaises(MoneyWireError) as caught:
            Money.from_wire({"amount": "1234.567", "currency": "INR"})
        self.assertNotIn("1234", str(caught.exception))

    def test_range_boundaries(self) -> None:
        self.assertEqual(Money(MAX_MINOR_UNITS, "JPY").to_wire()["amount"], "9223372036854775807")
        self.assertEqual(Money(-MAX_MINOR_UNITS, "JPY").to_wire()["amount"], "-9223372036854775807")
        with self.assertRaises(MoneyWireError) as caught:
            Money(-(2**63), "JPY")
        self.assertEqual(caught.exception.reason, "out_of_range")
        with self.assertRaises(MoneyWireError):
            Money(2**63, "JPY")

    def test_format_is_never_scientific(self) -> None:
        for minor in (0, 1, 10, 100, 10**18, -(10**18)):
            for code, entry in REGISTRY.items():
                amount = Money(minor, code).to_wire()["amount"]
                self.assertNotRegex(amount, r"[eE]")
                fraction = amount.split(".")[1] if "." in amount else ""
                self.assertEqual(len(fraction), entry.exponent)

    def test_to_wire_members_are_str(self) -> None:
        wire = Money(150, "INR").to_wire()
        self.assertEqual(set(wire), {"amount", "currency"})
        self.assertTrue(all(isinstance(v, str) for v in wire.values()))


class ValueSemanticsTest(unittest.TestCase):
    def test_equality_with_float_is_false_and_never_raises(self) -> None:
        money = Money(150, "INR")
        self.assertFalse(money == 1.5)
        self.assertTrue(money != 1.5)
        self.assertFalse(money == "1.50")
        self.assertFalse(money == Money(150, "JPY"))
        self.assertEqual(money, Money(150, "INR"))

    def test_ordering_against_float_raises(self) -> None:
        money = Money(150, "INR")
        with self.assertRaises(TypeError):
            money < 1.5  # noqa: B015
        with self.assertRaises(TypeError):
            money >= 2  # noqa: B015

    def test_ordering_across_currencies_raises(self) -> None:
        with self.assertRaises(TypeError):
            Money(1, "INR") < Money(1, "JPY")  # noqa: B015

    def test_same_currency_ordering(self) -> None:
        self.assertLess(Money(-1, "INR"), Money(0, "INR"))
        self.assertGreaterEqual(Money(5, "KWD"), Money(5, "KWD"))
        self.assertEqual(sorted([Money(3, "INR"), Money(-2, "INR"), Money(0, "INR")]), [Money(-2, "INR"), Money(0, "INR"), Money(3, "INR")])

    def test_hash_over_minor_units_and_currency(self) -> None:
        self.assertEqual(len({Money(1, "INR"), Money(1, "INR"), Money(1, "JPY")}), 2)

    def test_no_arithmetic_in_python(self) -> None:
        money = Money(1, "INR")
        with self.assertRaises(TypeError):
            money + money  # type: ignore[operator]  # noqa: B018
        with self.assertRaises(TypeError):
            money * 2  # type: ignore[operator]  # noqa: B018


class PydanticSeamTest(unittest.TestCase):
    def test_round_trip_emits_no_json_number(self) -> None:
        holder = Holder.model_validate({"total": {"amount": "-1234.56", "currency": "INR"}})
        self.assertIsInstance(holder.total, Money)
        self.assertEqual(holder.total.minor_units, -123456)
        dumped: dict[str, Any] = holder.model_dump(mode="json")
        self.assertEqual(dumped, {"total": {"amount": "-1234.56", "currency": "INR"}})
        text = holder.model_dump_json()
        self.assertEqual(json.loads(text), {"total": {"amount": "-1234.56", "currency": "INR"}})
        self.assertIn('"amount":"-1234.56"', text)
        self.assertNotIn('"amount":-1234.56', text)  # never a bare JSON number
        self.assertEqual(Holder.model_validate_json(text), holder)

    def test_wire_error_becomes_validation_error(self) -> None:
        with self.assertRaises(ValidationError) as caught:
            Holder.model_validate({"total": {"amount": "12.5", "currency": "INR"}})
        self.assertIn("scale_mismatch", str(caught.exception))
        self.assertNotIn("12.5", str(caught.exception).split("input_value")[0])

    def test_instance_passes_through(self) -> None:
        money = Money(5, "KWD")
        self.assertIs(Holder(total=money).total, money)


if __name__ == "__main__":
    unittest.main()
