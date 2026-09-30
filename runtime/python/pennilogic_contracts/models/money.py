"""ADR-015 money wrapper for the generated Python client (hand-written seam, shipped with every version).

`Money` is the only representation of money in application code: a `decimal.Decimal` at the
currency's registry scale plus the currency code, constructed only from integer minor units
(`Money(minor_units, currency)`), a validated canonical string (`Money.parse`) or the wire object
(`Money.from_wire`). Floats are rejected everywhere: a `float` given to the constructor or to
`parse` raises `TypeError`; a JSON number on the wire is rejected with reason `number_not_string`.

The wire shape `{"amount": "<canonical decimal string>", "currency": "<ISO 4217>"}` is parsed and
rendered only here (ADR-015 §1, §2.2). Rejections carry the reason and the field name, never the
offending value.
"""

from __future__ import annotations

import decimal
import re
from typing import Any, Final, Mapping

from pennilogic_contracts.models.currency_registry import REGISTRY

__all__ = ["MONEY_CONTEXT", "MAX_MINOR_UNITS", "REASONS", "Money", "MoneyWireError"]

MONEY_CONTEXT: Final = decimal.Context(
    prec=28,
    rounding=decimal.ROUND_HALF_EVEN,
    traps=[
        decimal.InvalidOperation,
        decimal.DivisionByZero,
        decimal.Overflow,
        decimal.Inexact,
        decimal.Rounded,
        decimal.FloatOperation,
    ],
)
MAX_MINOR_UNITS: Final = 2**63 - 1
REASONS: Final = ("shape", "number_not_string", "grammar", "currency_unknown", "scale_mismatch", "out_of_range")
_GRAMMAR: Final = re.compile(r"^(0(\.[0-9]+)?|-?[1-9][0-9]*(\.[0-9]+)?|-0\.[0-9]*[1-9][0-9]*)$", re.ASCII)
_MEMBERS: Final = ("amount", "currency")


class MoneyWireError(ValueError):
    """A wire value was rejected; `reason` is one of REASONS and `field` names the member ("" for the whole value)."""

    def __init__(self, reason: str, field: str) -> None:
        self.reason = reason
        self.field = field
        super().__init__(f"money rejected: {reason} at {field or '<value>'}")


def _exponent(currency: object, field: str = "currency") -> int:
    if not isinstance(currency, str):
        raise TypeError("currency must be a str ISO 4217 code")
    entry = REGISTRY.get(currency)
    if entry is None:
        raise MoneyWireError("currency_unknown", field)
    return entry.exponent


def _minor_units_from_canonical(amount: str, exponent: int) -> int:
    """Validate grammar, scale and range of a canonical string and return the signed minor units."""
    if not _GRAMMAR.fullmatch(amount):
        raise MoneyWireError("grammar", "amount")
    negative = amount.startswith("-")
    unsigned = amount[1:] if negative else amount
    integer_part, _, fraction = unsigned.partition(".")
    if len(fraction) != exponent:
        raise MoneyWireError("scale_mismatch", "amount")
    magnitude = int(integer_part + fraction)
    if magnitude > MAX_MINOR_UNITS:
        raise MoneyWireError("out_of_range", "amount")
    return -magnitude if negative else magnitude


class Money:
    """Integer minor units with currency; Decimal-backed, never a float."""

    __slots__ = ("_minor_units", "_currency", "_amount")

    def __init__(self, minor_units: int, currency: str) -> None:
        if type(minor_units) is not int:  # bool, float, Decimal and str are all refused
            raise TypeError("Money takes integer minor units; floats, Decimals and strings are refused (ADR-015)")
        exponent = _exponent(currency)
        if abs(minor_units) > MAX_MINOR_UNITS:
            raise MoneyWireError("out_of_range", "amount")
        self._minor_units = minor_units
        self._currency = currency
        with decimal.localcontext(MONEY_CONTEXT):
            self._amount = decimal.Decimal(minor_units).scaleb(-exponent)

    # -- construction seams -------------------------------------------------------------------

    @classmethod
    def of_minor_units(cls, minor_units: int, currency: str) -> Money:
        return cls(minor_units, currency)

    @classmethod
    def parse(cls, amount: str, currency: str) -> Money:
        """Construct from a canonical amount string; non-str input is a programming error (TypeError)."""
        if type(amount) is not str:
            raise TypeError("Money.parse takes the canonical decimal string; floats and numbers are refused (ADR-015)")
        exponent = _exponent(currency)
        return cls(_minor_units_from_canonical(amount, exponent), currency)

    @classmethod
    def from_wire(cls, wire: object) -> Money:
        """Construct from the wire object, checking in the fixed ADR-015 §1.5 order."""
        if not isinstance(wire, Mapping):
            raise MoneyWireError("shape", "")
        for member in _MEMBERS:
            if member not in wire:
                raise MoneyWireError("shape", member)
        for key in wire:
            if key not in _MEMBERS:
                raise MoneyWireError("shape", str(key))
        for member in _MEMBERS:
            value = wire[member]
            if isinstance(value, bool) or not isinstance(value, (str, int, float, decimal.Decimal)):
                raise MoneyWireError("shape", member)
        for member in _MEMBERS:
            if not isinstance(wire[member], str):
                raise MoneyWireError("number_not_string", member)
        amount, currency = wire["amount"], wire["currency"]
        if not _GRAMMAR.fullmatch(amount):
            raise MoneyWireError("grammar", "amount")
        exponent = _exponent(currency)
        return cls(_minor_units_from_canonical(amount, exponent), currency)

    @classmethod
    def from_dict(cls, wire: object) -> Money:
        """Name the generated Pydantic models call; identical to from_wire."""
        return cls.from_wire(wire)

    # -- destruction seam ------------------------------------------------------------------------

    def to_wire(self) -> dict[str, str]:
        """Render the wire object; the result is self-checked against the grammar and re-parsed."""
        with decimal.localcontext(MONEY_CONTEXT):
            amount = format(self._amount, "f")
        exponent = REGISTRY[self._currency].exponent
        if exponent > 0 and "." not in amount:
            amount = f"{amount}.{'0' * exponent}"
        if _minor_units_from_canonical(amount, exponent) != self._minor_units:
            raise AssertionError("money destruction seam produced a non-canonical amount")
        return {"amount": amount, "currency": self._currency}

    def to_dict(self) -> dict[str, str]:
        """Name the generated Pydantic models call; identical to to_wire."""
        return self.to_wire()

    # -- read-only views ---------------------------------------------------------------------------

    @property
    def minor_units(self) -> int:
        return self._minor_units

    @property
    def currency(self) -> str:
        return self._currency

    @property
    def amount(self) -> decimal.Decimal:
        return self._amount

    # -- value semantics: equality and same-currency ordering only ---------------------------------

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self._currency == other._currency and self._minor_units == other._minor_units

    def __hash__(self) -> int:
        return hash((self._minor_units, self._currency))

    def _same_currency(self, other: object) -> Money:
        if not isinstance(other, Money):
            raise TypeError("Money orders only against Money")
        if other._currency != self._currency:
            raise TypeError("Money orders only within one currency")
        return other

    def __lt__(self, other: object) -> bool:
        return self._minor_units < self._same_currency(other)._minor_units

    def __le__(self, other: object) -> bool:
        return self._minor_units <= self._same_currency(other)._minor_units

    def __gt__(self, other: object) -> bool:
        return self._minor_units > self._same_currency(other)._minor_units

    def __ge__(self, other: object) -> bool:
        return self._minor_units >= self._same_currency(other)._minor_units

    def __repr__(self) -> str:
        return f"Money({self._minor_units}, {self._currency!r})"

    def __str__(self) -> str:
        wire = self.to_wire()
        return f"{wire['amount']} {wire['currency']}"

    # -- Pydantic v2 integration: the wrapper is the field type of every generated model -----------

    @classmethod
    def __get_pydantic_core_schema__(cls, _source: Any, _handler: Any) -> Any:
        from pydantic_core import core_schema

        def validate(value: object) -> Money:
            if isinstance(value, Money):
                return value
            return cls.from_wire(value)

        return core_schema.no_info_plain_validator_function(
            validate,
            serialization=core_schema.plain_serializer_function_ser_schema(Money.to_wire, when_used="always"),
        )

    @classmethod
    def __get_pydantic_json_schema__(cls, _core_schema: Any, _handler: Any) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["amount", "currency"],
            "properties": {
                "amount": {"type": "string", "pattern": _GRAMMAR.pattern, "maxLength": 21},
                "currency": {"type": "string", "pattern": "^[A-Z]{3}$"},
            },
        }
