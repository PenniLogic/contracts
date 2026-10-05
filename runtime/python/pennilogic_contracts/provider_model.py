"""Closed provider models only; legacy generated DTO compatibility is unchanged."""

from __future__ import annotations

import json
import pprint
from enum import Enum
from typing import Any, ClassVar, Mapping, Self

from pydantic import BaseModel, ConfigDict, ModelWrapValidatorHandler, ValidationError, model_serializer, model_validator

from pennilogic_contracts.models.instant import Instant
from pennilogic_contracts.models.local_date import LocalDate
from pennilogic_contracts.models.money import Money
from pennilogic_contracts.provider_constraints import ProviderConstraintError, validate_provider


class ProviderWireError(ValueError):
    def __init__(self) -> None:
        super().__init__("provider value rejected")


def _wire_value(value: Any) -> Any:
    if isinstance(value, ProviderModel):
        return value._provider_payload()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (Money, Instant, LocalDate)):
        return value.to_wire()
    if isinstance(value, list):
        return [_wire_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _wire_value(item) for key, item in value.items()}
    return value


class ProviderModel(BaseModel):
    _provider_schema_name: ClassVar[str] = ""
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, hide_input_in_errors=True,
                              revalidate_instances="always", validate_by_alias=True, validate_by_name=True)

    @classmethod
    def _guard_fields(cls, value: object, *, wire: bool) -> Mapping[str, Any]:
        if not isinstance(value, dict):
            raise ProviderWireError()
        fields = cls.model_fields
        aliases = {field.alias or name: name for name, field in fields.items()}
        accepted = set(aliases) if wire else set(aliases) | set(fields)
        if not value.keys() <= accepted or any(item is None for item in value.values()):
            raise ProviderWireError()
        if any(field.is_required() and name not in value and (field.alias or name) not in value for name, field in fields.items()):
            raise ProviderWireError()
        if any(field.alias and field.alias != name and name in value and field.alias in value for name, field in fields.items()):
            raise ProviderWireError()
        return value

    @model_validator(mode="wrap")
    @classmethod
    def _validate_provider(cls, value: Any, handler: ModelWrapValidatorHandler[Self]) -> Self:
        source = value._provider_payload() if isinstance(value, ProviderModel) else value
        fields = cls._guard_fields(source, wire=False)
        normalized = {key: _wire_value(item) for key, item in fields.items()}
        try:
            aliases = {name: field.alias or name for name, field in cls.model_fields.items()}
            validate_provider(cls._provider_schema_name, {aliases.get(key, key): item for key, item in normalized.items()})
            return handler(normalized)
        except (ValidationError, ProviderConstraintError):
            # Nested type failures must not expose raw values or unknown input keys.
            raise ProviderWireError() from None

    def _provider_payload(self) -> dict[str, Any]:
        fields = type(self).model_fields
        if not self.__dict__.keys() <= fields.keys() or self.__pydantic_extra__:
            raise ProviderWireError()
        return {
            field.alias or name: _wire_value(self.__dict__[name])
            for name, field in fields.items() if name in self.__dict__ and self.__dict__[name] is not None
        }

    @model_serializer(mode="plain")
    def _serialize_provider(self) -> dict[str, Any]:
        payload = self._provider_payload()
        type(self).model_validate(payload)
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        cls._guard_fields(value, wire=True)
        return cls.model_validate(value)

    @classmethod
    def from_json(cls, text: str) -> Self:
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            raise ProviderWireError() from None
        return cls.from_dict(value)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)

    def to_json(self) -> str:
        return self.model_dump_json(by_alias=True, exclude_none=True)

    def to_str(self) -> str:
        return pprint.pformat(self.to_dict())
