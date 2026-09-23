"""Request/response models of the telephony routes (CONTRACTS-V2 §1.6, §3.4).

``CallCreate`` and ``CallOut`` live in ``lkap_contracts.api_models`` (exported
to TypeScript). The trunk, dispatch-rule and number shapes are not in the
contracts package yet, so they are defined here and mirrored by hand in
``web/src/components/console/telephony/types.ts``; ``docs/v2/_asks.md``
(V2-17-1) asks the contracts owner to promote them.

Secrets are write-only: a trunk's SIP password and a dispatch rule's PIN are
accepted on create/update and never returned (``has_password`` / ``has_pin``).
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator

#: E.164: a ``+``, a non-zero country digit, 7–15 digits in total.
E164_PATTERN = r"^\+[1-9]\d{6,14}$"
E164 = Annotated[str, StringConstraints(pattern=E164_PATTERN)]

#: A transfer target: an E.164 number, or a ``tel:`` / ``sip:`` / ``sips:`` URI.
_TRANSFER_TARGET = re.compile(r"^(\+[1-9]\d{6,14}|tel:\+?[0-9]{3,20}|sips?:[^\s@]+@[^\s]+)$")


def check_transfer_target(value: str) -> str:
    """Validate and strip a transfer target (E.164 or ``tel:``/``sip:`` URI).

    Raises:
        ValueError: When the value is neither.
    """
    value = value.strip()
    if not _TRANSFER_TARGET.match(value):
        raise ValueError("must be an E.164 number (+15551234567) or a tel:/sip: URI")
    return value


#: DTMF digits LiveKit can publish (RFC 4733 events 0–15).
DTMF_PATTERN = r"^[0-9*#A-D]{1,32}$"

TrunkDirection = Literal["inbound", "outbound"]
ProviderHint = Literal["twilio", "telnyx", "other"]


# --------------------------------------------------------------------------- trunks
class TrunkCreate(BaseModel):
    """``POST /v1/telephony/trunks``.

    ``inbound`` trunks accept calls to ``numbers`` (optionally only from
    ``address``, an IP/CIDR/host allow-list entry); ``outbound`` trunks dial
    through ``address`` (the carrier's SIP host, e.g. ``example.pstn.twilio.com``)
    and present ``numbers[0]`` as the caller id.
    """

    connection_id: str | None = Field(
        default=None, description="LiveKit connection; defaults to the workspace's default connection"
    )
    direction: TrunkDirection
    name: str = Field(min_length=1, max_length=200)
    numbers: list[E164] = Field(default_factory=list, max_length=100)
    provider_hint: ProviderHint = "other"
    address: str | None = Field(default=None, max_length=512)
    auth_username: str | None = Field(default=None, max_length=200)
    auth_password: str | None = Field(default=None, max_length=500, description="Write-only")


class TrunkUpdate(BaseModel):
    """``PUT /v1/telephony/trunks/{id}``: omitted fields keep their value.

    ``auth_password`` omitted keeps the stored password, ``""`` clears it.
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    numbers: list[E164] | None = Field(default=None, max_length=100)
    provider_hint: ProviderHint | None = None
    address: str | None = Field(default=None, max_length=512)
    auth_username: str | None = Field(default=None, max_length=200)
    auth_password: str | None = Field(default=None, max_length=500, description="Write-only")


class TrunkOut(BaseModel):
    """A SIP trunk as stored by the platform, with its LiveKit id."""

    id: str
    connection_id: str
    direction: TrunkDirection
    name: str
    lk_trunk_id: str | None = None
    numbers: list[str] = []
    provider_hint: ProviderHint = "other"
    address: str | None = None
    auth_username: str | None = None
    has_password: bool = False
    created_at: dt.datetime
    updated_at: dt.datetime


# ------------------------------------------------------------------- dispatch rules
class DispatchRuleCreate(BaseModel):
    """``POST /v1/telephony/dispatch-rules``: route calls on an inbound trunk to an agent.

    ``numbers`` empty means every number of the trunk. Each call gets its own
    room named ``<room_prefix><caller>_<random>`` (LiveKit's individual rule).
    """

    trunk_id: str
    agent_id: str
    numbers: list[E164] = Field(default_factory=list, max_length=100)
    room_prefix: str = Field(default="call-", max_length=64, pattern=r"^[A-Za-z0-9_-]*$")
    pin: str | None = Field(default=None, pattern=r"^[0-9]{4,12}$", description="Write-only")


class DispatchRuleOut(BaseModel):
    """A dispatch rule; ``managed_by_number`` marks the rule a number's inbound agent owns."""

    id: str
    connection_id: str
    lk_rule_id: str | None = None
    trunk_id: str
    agent_id: str
    numbers: list[str] = []
    room_prefix: str = ""
    has_pin: bool = False
    managed_by_number: str | None = None
    created_at: dt.datetime


# -------------------------------------------------------------------------- numbers
class PhoneNumberCreate(BaseModel):
    """``POST /v1/telephony/numbers``.

    Binding a number to a trunk adds it to the trunk's numbers. Setting
    ``inbound_agent_id`` (inbound trunks only) creates a dispatch rule that
    sends calls to this number to that agent.
    """

    e164: E164
    trunk_id: str | None = None
    inbound_agent_id: str | None = None
    label: str = Field(default="", max_length=200)


class PhoneNumberUpdate(BaseModel):
    """``PUT /v1/telephony/numbers/{id}``: only the fields present in the body change.

    ``inbound_agent_id: null`` stops inbound routing for the number.
    """

    trunk_id: str | None = None
    inbound_agent_id: str | None = None
    label: str | None = Field(default=None, max_length=200)


class PhoneNumberOut(BaseModel):
    """A number owned by the workspace and the agent its inbound calls reach."""

    id: str
    e164: str
    trunk_id: str | None = None
    inbound_agent_id: str | None = None
    label: str = ""
    dispatch_rule_id: str | None = None


# ---------------------------------------------------------------------------- calls
class CallTransferIn(BaseModel):
    """``POST /v1/calls/{id}/transfer``: a cold (SIP REFER) transfer."""

    to: str = Field(min_length=3, max_length=256, description="E.164 number or tel:/sip: URI")

    @field_validator("to")
    @classmethod
    def _valid_target(cls, value: str) -> str:
        return check_transfer_target(value)


class CallDtmfIn(BaseModel):
    """``POST /v1/calls/{id}/dtmf``: digits the agent plays into the call."""

    digits: str = Field(pattern=DTMF_PATTERN, description="0-9, *, #, A-D; up to 32")


class CallDtmfOut(BaseModel):
    """What was queued to the worker."""

    call_id: str
    digits: str
    queued: bool = True


# ------------------------------------------------------------------ internal (worker)
class CallReportIn(BaseModel):
    """``POST /internal/v1/telephony/calls/report`` — the worker's view of a SIP leg.

    Webhooks are the primary source of call status, but a dev install often
    has no public webhook url; the worker reports what it saw so the calls log
    is right either way. Transitions only ever move forward.
    """

    session_id: str
    status: Literal["answered", "completed", "failed"]
    direction: Literal["inbound", "outbound"] | None = None
    from_e164: str | None = Field(default=None, max_length=32)
    to_e164: str | None = Field(default=None, max_length=32)
    sip_call_id: str | None = Field(default=None, max_length=128)
    participant_identity: str | None = Field(default=None, max_length=200)
    reason: str | None = Field(default=None, max_length=128)


class InternalTransferIn(BaseModel):
    """``POST /internal/v1/telephony/sessions/{id}/transfer`` (the ``transfer_call`` tool)."""

    to: str = Field(min_length=3, max_length=256)
    participant_identity: str | None = Field(default=None, max_length=200)

    @field_validator("to")
    @classmethod
    def _valid_target(cls, value: str) -> str:
        return check_transfer_target(value)


class InternalTransferOut(BaseModel):
    """The transfer outcome as the tool reports it to the model."""

    ok: bool
    status: str
    call_id: str | None = None
    reason: str | None = None
