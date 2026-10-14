"""Warn/block gate state for contract compatibility (PenniLogic/contracts#3, T-QA-02).

contracts#3 rolls breaking-change blocking out as a gate whose rollback is "downgrading the gate to
warning with a recorded expiry". The gate state is one committed file, scripts/verification/compat-gate.json,
closed by scripts/verification/compat-gate.schema.v1.json:

    {"schema": "pennilogic-compat-gate@1", "mode": "block", "warn_until": null}

`mode` is `block` or `warn`. A warning must carry `warn_until`, the fixed 24-character UTC instant
YYYY-MM-DDTHH:MM:SS.mmmZ at and after which the gate blocks again; `block` carries null. The file is meant
to change only through a reviewed pull request, so that its history records every downgrade (that relies
on branch protection, which this module cannot check).

Every reader decides with the same function and an injected clock, and nothing defaults to warn:

- `block` blocks (gate.block);
- `warn` warns while now < warn_until (gate.warn_active) and blocks from warn_until on (gate.warn_expired);
- a missing, unreadable or malformed state, a warning without expiry or with an invalid one, and a
  block that names an expiry all block, with the refusal rule as the reason.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from contract_schema import (  # noqa: E402
    InputMissing, InputUnavailable, StrictJsonError, first_violation, load_schema, parse_strict_json, read_bounded,
)
from pl_contracts import ROOT, sha256_bytes  # noqa: E402

VERIFICATION = ROOT / "scripts" / "verification"
GATE_STATE = VERIFICATION / "compat-gate.json"
GATE_SCHEMA = VERIFICATION / "compat-gate.schema.v1.json"
SCHEMA_ID = "pennilogic-compat-gate@1"
MODES = ("block", "warn")
MAX_STATE_BYTES = 64 * 1024
INSTANT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z")

RULES = (
    "gate.missing", "gate.unreadable", "gate.malformed", "gate.warn_until_missing", "gate.warn_until_invalid",
    "gate.warn_until_unexpected",
)
# Every reason a decision can carry: the three regular outcomes, then the refusals (all of which block).
DECISIONS = ("gate.block", "gate.warn_active", "gate.warn_expired") + RULES
# The reasons of a decision made without reading the state's bytes, the only ones without state_sha256.
UNREAD_REASONS = ("gate.missing", "gate.unreadable")


class GateRefused(Exception):
    """The gate state failed one rule. Readers then block; `sha256` names the refused bytes when they were read."""

    def __init__(self, rule: str, detail: str, sha256: str | None = None) -> None:
        if rule not in RULES:
            raise ValueError(f"unknown gate rule {rule!r}")
        super().__init__(f"{rule}: {detail}")
        self.rule = rule
        self.detail = detail
        self.sha256 = sha256


@dataclass(frozen=True)
class GateState:
    mode: str
    warn_until: datetime | None
    warn_until_text: str | None
    sha256: str


@dataclass(frozen=True)
class GateDecision:
    """The effective gate at one instant. `mode` is warn only for an unexpired, valid warning."""

    mode: str
    reason: str
    warn_until: str | None
    state_sha256: str | None

    def as_dict(self) -> dict:
        return {"mode": self.mode, "reason": self.reason, "warn_until": self.warn_until, "state_sha256": self.state_sha256}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def require_utc(now: object) -> datetime:
    """The injected clock must return a timezone-aware UTC datetime; anything else is a programming error."""
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError("the gate clock must return a timezone-aware UTC datetime")
    return now


def parse_instant(text: object) -> datetime:
    """Parse the fixed 24-character UTC instant; raise ValueError for any other spelling or an impossible date."""
    if not isinstance(text, str) or INSTANT.fullmatch(text) is None:
        raise ValueError("not a YYYY-MM-DDTHH:MM:SS.mmmZ instant")
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def format_instant(moment: datetime) -> str:
    moment = require_utc(moment)
    return (f"{moment.year:04d}-{moment.month:02d}-{moment.day:02d}T{moment.hour:02d}:{moment.minute:02d}:"
            f"{moment.second:02d}.{moment.microsecond // 1000:03d}Z")


def parse_gate_state(data: bytes) -> GateState:
    """Validate gate-state bytes; raise GateRefused with one static rule ID."""
    digest = sha256_bytes(data)
    try:
        state = parse_strict_json(data)
    except StrictJsonError as error:
        raise GateRefused("gate.malformed", f"the gate state {error}", digest) from None
    if isinstance(state, dict):
        mode, until = state.get("mode"), state.get("warn_until")
        if mode == "warn" and until is None:
            raise GateRefused("gate.warn_until_missing", "a warning gate needs warn_until; without an expiry it blocks", digest)
        if mode == "warn":
            try:
                parse_instant(until)
            except ValueError:
                raise GateRefused("gate.warn_until_invalid",
                                  "warn_until is not a real YYYY-MM-DDTHH:MM:SS.mmmZ UTC instant", digest) from None
        if mode == "block" and until is not None:
            raise GateRefused("gate.warn_until_unexpected", "a blocking gate carries warn_until null", digest)
    violation = first_violation(state, load_schema(GATE_SCHEMA))
    if violation is not None:
        raise GateRefused("gate.malformed", f"the gate state is outside {GATE_SCHEMA.name}: {violation}", digest)
    assert isinstance(state, dict)
    if state["mode"] == "block":
        return GateState("block", None, None, digest)
    return GateState("warn", parse_instant(state["warn_until"]), state["warn_until"], digest)


def load_gate_state(path: Path | None) -> GateState:
    if path is None:
        raise GateRefused("gate.missing", "no gate state was given")
    try:
        data = read_bounded(Path(path), MAX_STATE_BYTES)
    except InputMissing:
        raise GateRefused("gate.missing", "the gate state file does not exist") from None
    except InputUnavailable as error:
        raise GateRefused("gate.unreadable", f"the gate state {error}") from None
    return parse_gate_state(data)


def decide(state: GateState, now: datetime) -> GateDecision:
    now = require_utc(now)
    if state.mode == "block":
        return GateDecision("block", "gate.block", None, state.sha256)
    assert state.warn_until is not None
    if now < state.warn_until:
        return GateDecision("warn", "gate.warn_active", state.warn_until_text, state.sha256)
    return GateDecision("block", "gate.warn_expired", state.warn_until_text, state.sha256)


def resolve_gate(path: Path | None, now: datetime) -> GateDecision:
    """The effective gate for `path` at `now`. Never raises for a bad state and never falls back to warn."""
    now = require_utc(now)
    try:
        state = load_gate_state(path)
    except GateRefused as refusal:
        return GateDecision("block", refusal.rule, None, refusal.sha256)
    return decide(state, now)
