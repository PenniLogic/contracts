"""ADR-015 §3.1 instant wrapper for the generated Python client (hand-written seam, shipped with every version).

The wire form is exactly `YYYY-MM-DDTHH:MM:SS.sssZ` (24 characters, UTC, milliseconds). `Instant`
holds epoch milliseconds; parsing validates the grammar and the calendar and requires that the
re-formatted value equals the input, so `2026-02-30T00:00:00.000Z`, an offset, a different
fraction length or lowercase letters are rejected. Rejections carry a reason, never the value.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Final

__all__ = ["Instant", "InstantWireError", "REASONS"]

REASONS: Final = ("shape", "grammar", "calendar")
_GRAMMAR: Final = re.compile(
    r"^(000[1-9]|00[1-9][0-9]|0[1-9][0-9]{2}|[1-9][0-9]{3})-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])"
    r"T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]\.[0-9]{3}Z$",
    re.ASCII,
)
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=timezone.utc)


class InstantWireError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"instant rejected: {reason}")


class Instant:
    """A UTC instant with millisecond precision; the only instant type in application code."""

    __slots__ = ("_epoch_millis",)

    def __init__(self, epoch_millis: int) -> None:
        if type(epoch_millis) is not int:
            raise TypeError("Instant takes integer epoch milliseconds")
        # Years 0001-9999 only, matching the wire grammar.
        if not (-62135596800000 <= epoch_millis <= 253402300799999):
            raise InstantWireError("calendar")
        self._epoch_millis = epoch_millis

    @classmethod
    def parse(cls, text: str) -> Instant:
        if type(text) is not str:
            raise TypeError("Instant.parse takes the 24-character wire string")
        if not _GRAMMAR.fullmatch(text):
            raise InstantWireError("grammar")
        try:
            parsed = datetime(
                int(text[0:4]), int(text[5:7]), int(text[8:10]),
                int(text[11:13]), int(text[14:16]), int(text[17:19]),
                int(text[20:23]) * 1000, tzinfo=timezone.utc,
            )
        except ValueError as error:
            raise InstantWireError("calendar") from error
        instant = cls((parsed - _EPOCH) // timedelta(milliseconds=1))
        if instant.to_wire() != text:
            raise InstantWireError("calendar")
        return instant

    @classmethod
    def from_wire(cls, wire: object) -> Instant:
        if not isinstance(wire, str):
            raise InstantWireError("shape")
        return cls.parse(wire)

    @classmethod
    def from_dict(cls, wire: object) -> Instant:
        """Name the generated Pydantic models call; identical to from_wire."""
        return cls.from_wire(wire)

    @classmethod
    def from_datetime(cls, value: datetime) -> Instant:
        """Convert an aware datetime already truncated to milliseconds (ADR-015 §3.1)."""
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise TypeError("Instant.from_datetime takes a timezone-aware datetime")
        if value.microsecond % 1000 != 0:
            raise ValueError("instants are truncated to milliseconds by the application before they reach the seam")
        return cls((value.astimezone(timezone.utc) - _EPOCH) // timedelta(milliseconds=1))

    def to_wire(self) -> str:
        value = _EPOCH + timedelta(milliseconds=self._epoch_millis)
        text = f"{value.year:04d}-{value.month:02d}-{value.day:02d}T{value.hour:02d}:{value.minute:02d}:{value.second:02d}.{value.microsecond // 1000:03d}Z"
        if not _GRAMMAR.fullmatch(text):
            raise AssertionError("instant destruction seam produced a non-canonical value")
        return text

    def to_dict(self) -> str:
        """Name the generated Pydantic models call; identical to to_wire."""
        return self.to_wire()

    def to_datetime(self) -> datetime:
        return _EPOCH + timedelta(milliseconds=self._epoch_millis)

    @property
    def epoch_millis(self) -> int:
        return self._epoch_millis

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Instant):
            return NotImplemented
        return self._epoch_millis == other._epoch_millis

    def __hash__(self) -> int:
        return hash(self._epoch_millis)

    def _other(self, other: object) -> Instant:
        if not isinstance(other, Instant):
            raise TypeError("Instant orders only against Instant")
        return other

    def __lt__(self, other: object) -> bool:
        return self._epoch_millis < self._other(other)._epoch_millis

    def __le__(self, other: object) -> bool:
        return self._epoch_millis <= self._other(other)._epoch_millis

    def __gt__(self, other: object) -> bool:
        return self._epoch_millis > self._other(other)._epoch_millis

    def __ge__(self, other: object) -> bool:
        return self._epoch_millis >= self._other(other)._epoch_millis

    def __repr__(self) -> str:
        return f"Instant({self.to_wire()!r})"

    def __str__(self) -> str:
        return self.to_wire()

    @classmethod
    def __get_pydantic_core_schema__(cls, _source: Any, _handler: Any) -> Any:
        from pydantic_core import core_schema

        def validate(value: object) -> Instant:
            if isinstance(value, Instant):
                return value
            return cls.from_wire(value)

        return core_schema.no_info_plain_validator_function(
            validate,
            serialization=core_schema.plain_serializer_function_ser_schema(Instant.to_wire, when_used="always"),
        )

    @classmethod
    def __get_pydantic_json_schema__(cls, _core_schema: Any, _handler: Any) -> dict[str, Any]:
        return {"type": "string", "format": "date-time", "pattern": _GRAMMAR.pattern, "minLength": 24, "maxLength": 24}
