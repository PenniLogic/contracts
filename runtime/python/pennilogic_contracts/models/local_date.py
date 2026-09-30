"""ADR-015 §3.1 date-only seam for the generated Python client (hand-written, shipped with every version).

A date-only fact travels as exactly `YYYY-MM-DD` (10 characters, years 0001-9999, valid proleptic
Gregorian date) and never carries a time of day. `LocalDate` wraps `datetime.date`; parsing validates
the grammar and the calendar and requires the re-formatted value to equal the input. Rejections carry
a reason, never the value.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Final

__all__ = ["LocalDate", "LocalDateWireError", "REASONS"]

REASONS: Final = ("shape", "grammar", "calendar")
_GRAMMAR: Final = re.compile(r"^(000[1-9]|00[1-9][0-9]|0[1-9][0-9]{2}|[1-9][0-9]{3})-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$", re.ASCII)


class LocalDateWireError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"date rejected: {reason}")


class LocalDate:
    """A calendar date without time zone or time of day; the only date type in application code."""

    __slots__ = ("_value",)

    def __init__(self, value: date) -> None:
        if type(value) is not date:  # datetime is a date subclass and would smuggle a time of day
            raise TypeError("LocalDate takes a datetime.date (not a datetime)")
        if not 1 <= value.year <= 9999:
            raise LocalDateWireError("calendar")
        self._value = value

    @classmethod
    def of(cls, year: int, month: int, day: int) -> LocalDate:
        try:
            return cls(date(year, month, day))
        except ValueError as error:
            raise LocalDateWireError("calendar") from error

    @classmethod
    def parse(cls, text: str) -> LocalDate:
        if type(text) is not str:
            raise TypeError("LocalDate.parse takes the 10-character wire string")
        if not _GRAMMAR.fullmatch(text):
            raise LocalDateWireError("grammar")
        value = cls.of(int(text[0:4]), int(text[5:7]), int(text[8:10]))
        if value.to_wire() != text:
            raise LocalDateWireError("calendar")
        return value

    @classmethod
    def from_wire(cls, wire: object) -> LocalDate:
        if not isinstance(wire, str):
            raise LocalDateWireError("shape")
        return cls.parse(wire)

    @classmethod
    def from_dict(cls, wire: object) -> LocalDate:
        """Name the generated Pydantic models call; identical to from_wire."""
        return cls.from_wire(wire)

    def to_wire(self) -> str:
        text = f"{self._value.year:04d}-{self._value.month:02d}-{self._value.day:02d}"
        if not _GRAMMAR.fullmatch(text):
            raise AssertionError("date destruction seam produced a non-canonical value")
        return text

    def to_dict(self) -> str:
        """Name the generated Pydantic models call; identical to to_wire."""
        return self.to_wire()

    def to_date(self) -> date:
        return self._value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, LocalDate):
            return NotImplemented
        return self._value == other._value

    def __hash__(self) -> int:
        return hash(self._value)

    def _other(self, other: object) -> LocalDate:
        if not isinstance(other, LocalDate):
            raise TypeError("LocalDate orders only against LocalDate")
        return other

    def __lt__(self, other: object) -> bool:
        return self._value < self._other(other)._value

    def __le__(self, other: object) -> bool:
        return self._value <= self._other(other)._value

    def __gt__(self, other: object) -> bool:
        return self._value > self._other(other)._value

    def __ge__(self, other: object) -> bool:
        return self._value >= self._other(other)._value

    def __repr__(self) -> str:
        return f"LocalDate({self.to_wire()!r})"

    def __str__(self) -> str:
        return self.to_wire()

    @classmethod
    def __get_pydantic_core_schema__(cls, _source: Any, _handler: Any) -> Any:
        from pydantic_core import core_schema

        def validate(value: object) -> LocalDate:
            if isinstance(value, LocalDate):
                return value
            return cls.from_wire(value)

        return core_schema.no_info_plain_validator_function(
            validate,
            serialization=core_schema.plain_serializer_function_ser_schema(LocalDate.to_wire, when_used="always"),
        )

    @classmethod
    def __get_pydantic_json_schema__(cls, _core_schema: Any, _handler: Any) -> dict[str, Any]:
        return {"type": "string", "format": "date", "pattern": _GRAMMAR.pattern, "minLength": 10, "maxLength": 10}
