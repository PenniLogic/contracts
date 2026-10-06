"""Typed UUID wire conversion; the owning schema supplies version and case constraints."""

from __future__ import annotations

import re
from uuid import UUID

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", re.ASCII)


def uuid_from_wire(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str) and _UUID.fullmatch(value):
        return UUID(value)
    raise ValueError("UUID value rejected")
