"""Executed only against the real scratch-generated package."""

import json
from io import BytesIO
from unittest.mock import patch
from urllib3 import HTTPResponse

from pennilogic_contracts import ApiClient
from pennilogic_contracts.api.probe_api import ProbeApi
from pennilogic_contracts.models.nullable_probe import NullableProbe
from pennilogic_contracts.models.nullable_choice import NullableChoice
from pennilogic_contracts.success_responses import ProbeNullableSuccessStatus200, ProbeNullableSuccessStatus204

wire = {"only_null": None, "number": None, "label": None, "values": [None, 1], "nested": [None, [None, "ok"]],
        "choice": None, "choice_alias": None, "choices": [None, "alpha"],
        "choices_nested": [None, [None, "beta"]], "nonnull_choice": "alpha"}
value = NullableProbe.from_dict(wire)
assert value.to_dict() == wire
assert isinstance(value.choices[1], NullableChoice)
assert NullableProbe.from_dict({**wire, "choice": "beta", "choice_alias": "alpha"}).to_dict() == {
    **wire, "choice": "beta", "choice_alias": "alpha"}
assert ApiClient().sanitize_for_serialization(value) == wire
assert NullableProbe.from_dict({**wire, "optional_flag": None}).to_dict() == {**wire, "optional_flag": None}
assert NullableProbe.from_dict({**wire, "optional_flag": True}).to_dict() == {**wire, "optional_flag": True}
assert NullableProbe.from_dict({**wire, "optional_choice": None}).to_dict() == {**wire, "optional_choice": None}
assert NullableProbe.from_dict({**wire, "optional_choice": "beta"}).to_dict() == {**wire, "optional_choice": "beta"}
for field in wire:
    try:
        NullableProbe.from_dict({key: item for key, item in wire.items() if key != field})
    except ValueError:
        pass
    else:
        raise AssertionError("required nullable field omitted")
for changes in ({"only_null": "wrong"}, {"number": 1.5}, {"number": True}, {"label": ""},
                {"values": ["1"]}, {"nested": [[1]]}, {"optional_flag": "true"}, {"choice": "unknown"},
                {"choice_alias": 1}, {"choices": ["unknown"]}, {"optional_choice": "unknown"},
                {"choices_nested": [[None, None]]}, {"choices_nested": [["unknown"]]}, {"nonnull_choice": None},
                {"private": "CANARY"}):
    try:
        NullableProbe.from_dict({**wire, **changes})
    except ValueError as error:
        assert "CANARY" not in str(error)
    else:
        raise AssertionError("invalid nullable value accepted")
client = ApiClient()
api = ProbeApi(client)
responses = [
    HTTPResponse(body=json.dumps(wire).encode(), status=200, headers={"Content-Type": "application/json"}),
    HTTPResponse(body=BytesIO(b""), status=204, preload_content=False),
    HTTPResponse(body=BytesIO(b"null"), status=204, preload_content=False),
    HTTPResponse(body=BytesIO(b""), status=200, preload_content=False, headers={"Content-Type": "application/json"}),
    HTTPResponse(body=json.dumps(wire).encode(), status=200, headers={"Content-Type": "text/plain"}),
    HTTPResponse(body=json.dumps(wire).encode(), status=206, headers={"Content-Type": "application/json"}),
]
with patch.object(client.rest_client.pool_manager, "request", side_effect=responses):
    body = api.probe_nullable()
    assert isinstance(body, ProbeNullableSuccessStatus200) and body.body.to_dict() == wire
    empty = api.probe_nullable_with_http_info()
    assert empty.status_code == 204 and isinstance(empty.data, ProbeNullableSuccessStatus204)
    for _ in range(4):
        try:
            api.probe_nullable()
        except ValueError:
            pass
        else:
            raise AssertionError("undeclared status/media/body accepted")
print("actual Python null/presence/ref-alias/enum/nested-array and typed-empty transport controls")
