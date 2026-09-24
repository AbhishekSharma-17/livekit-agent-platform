"""A Twirp-JSON client for LiveKit's ``PhoneNumberService`` (PHONE-NUMBERS.md D-V4-16, R-V4-12).

``livekit-api`` 1.2.1 (the newest on PyPI on 2026-09-25) has no phone-number
bindings, and its ``TwirpClient`` only carries protobuf messages it knows.
LiveKit's Twirp endpoint also speaks proto3-JSON (verified in the V4-05 live
check, step 0: ``lk number list`` itself posts ``Content-Type:
application/json``), so this module posts JSON to
``<https origin>/twirp/livekit.PhoneNumberService/<Method>`` through the
network-guarded aiohttp session :meth:`ConnectionClientFactory.phone_numbers`
opens, signed with the connection's key and ``SIPGrants(admin=True)``.

Three methods only: :meth:`PhoneNumberClient.list`, :meth:`~PhoneNumberClient.get`
and :meth:`~PhoneNumberClient.update`. LKAP never searches for, buys or gives
back a number (D-V4-15, D-V4-22); there is deliberately no method for it.

Responses are parsed in both key styles: LiveKit Cloud answers with the proto
field names (``e164_format``, ``sip_dispatch_rule_ids``), and standard
proto3-JSON emitters use lowerCamelCase (``e164Format``). Enum values arrive as
their full proto names (``PHONE_NUMBER_STATUS_OFFLINE``) and are lowered to the
contract literals (``offline``); anything unknown becomes ``unknown``.

**Removal condition:** when ``livekit-api`` ships the service (a
``phone_number`` attribute on :class:`livekit.api.LiveKitAPI`), replace this
module with SDK calls and delete the JSON codec. ``tests/test_phone_numbers.py``
carries a sentinel test that fails on that day.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from typing import Any, Final, Literal

import aiohttp
from livekit.api import AccessToken, SIPGrants
from lkap_contracts.api_models import LkInboundStatus, LkNumberStatus
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from pydantic.alias_generators import to_camel

from lkap_api.errors import ApiError, NotFoundError, UnprocessableEntityError
from lkap_api.logging import get_logger
from lkap_api.telephony.common import LiveKitUpstreamError

log = get_logger(__name__)

#: The Twirp service path segment.
SERVICE: Final = "livekit.PhoneNumberService"

#: Page size of ``ListPhoneNumbers``.
PAGE_LIMIT: Final = 50

#: Hard stop on paging, so a server that keeps answering with a token cannot loop forever.
MAX_PAGES: Final = 100

#: The grant the requests are signed with (the live check records whether it sufficed).
GRANT_DESCRIPTION: Final = "SIPGrants(admin=True)"

NumberType = Literal["mobile", "local", "toll_free", "unknown"]
LkOutboundStatus = Literal["active", "unavailable", "unknown"]

_STATUS: Final[dict[str, LkNumberStatus]] = {
    "PHONE_NUMBER_STATUS_ACTIVE": "active",
    "PHONE_NUMBER_STATUS_PENDING": "pending",
    "PHONE_NUMBER_STATUS_RELEASED": "released",
    "PHONE_NUMBER_STATUS_OFFLINE": "offline",
}
_INBOUND: Final[dict[str, LkInboundStatus]] = {
    "PHONE_NUMBER_IN_STATUS_ACTIVE": "active",
    "PHONE_NUMBER_IN_STATUS_UNAVAILABLE": "unavailable",
    "PHONE_NUMBER_IN_STATUS_DETACHED": "detached",
}
_OUTBOUND: Final[dict[str, LkOutboundStatus]] = {
    "PHONE_NUMBER_OUT_STATUS_ACTIVE": "active",
    "PHONE_NUMBER_OUT_STATUS_UNAVAILABLE": "unavailable",
}
_TYPE: Final[dict[str, NumberType]] = {
    "PHONE_NUMBER_TYPE_MOBILE": "mobile",
    "PHONE_NUMBER_TYPE_LOCAL": "local",
    "PHONE_NUMBER_TYPE_TOLL_FREE": "toll_free",
}

#: Request filter values of ``ListPhoneNumbersRequest.statuses``.
_STATUS_NAME: Final[dict[str, str]] = {value: key for key, value in _STATUS.items()}


_ENUM_PREFIXES: Final = (
    "PHONE_NUMBER_IN_STATUS_",
    "PHONE_NUMBER_OUT_STATUS_",
    "PHONE_NUMBER_STATUS_",
    "PHONE_NUMBER_TYPE_",
)


def _lower(value: object, table: dict[str, Any]) -> Any:
    """Map ``PHONE_NUMBER_STATUS_OFFLINE`` (or ``offline``) to the contract literal; else ``unknown``."""
    if not isinstance(value, str):
        return "unknown"
    name = value.strip().upper()
    if name in table:
        return table[name]
    for key, literal in table.items():
        short = key
        for prefix in _ENUM_PREFIXES:
            short = short.removeprefix(prefix)
        if short == name:
            return literal
    return "unknown"


class PhoneNumbersUnavailableError(ApiError):
    """409: LiveKit refused the phone-number service for this connection's key."""

    status_code = 409
    code = "phone_numbers_unavailable"


class LkPhoneNumber(BaseModel):
    """A LiveKit ``PhoneNumber`` (the fields LKAP reads), enums lowered to the contract literals."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore", frozen=True)

    id: str
    name: str = ""
    e164_format: str = ""
    country_code: str = ""
    area_code: str = ""
    number_type: NumberType = "unknown"
    locality: str = ""
    region: str = ""
    capabilities: list[str] = []
    status: LkNumberStatus = "unknown"
    inbound_status: LkInboundStatus = "unknown"
    outbound_status: LkOutboundStatus = "unknown"
    sip_dispatch_rule_id: str = ""
    sip_dispatch_rule_ids: list[str] = []
    assigned_at: dt.datetime | None = None
    released_at: dt.datetime | None = None
    created_at: dt.datetime | None = None
    updated_at: dt.datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _drop_nulls(cls, data: Any) -> Any:
        """proto3-JSON writes ``null`` for unset messages; let the defaults apply instead."""
        if isinstance(data, dict):
            return {key: value for key, value in data.items() if value is not None}
        return data

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: object) -> object:
        return _lower(value, _STATUS)

    @field_validator("inbound_status", mode="before")
    @classmethod
    def _inbound(cls, value: object) -> object:
        return _lower(value, _INBOUND)

    @field_validator("outbound_status", mode="before")
    @classmethod
    def _outbound(cls, value: object) -> object:
        return _lower(value, _OUTBOUND)

    @field_validator("number_type", mode="before")
    @classmethod
    def _number_type(cls, value: object) -> object:
        return _lower(value, _TYPE)

    @property
    def rule_ids(self) -> list[str]:
        """Every dispatch rule the number is attached to (the repeated field, else the deprecated one)."""
        if self.sip_dispatch_rule_ids:
            return list(dict.fromkeys(self.sip_dispatch_rule_ids))
        return [self.sip_dispatch_rule_id] if self.sip_dispatch_rule_id else []

    @property
    def display_region(self) -> str:
        """``"San Francisco, CA"`` for the console's subtitle (LiveKit sends the locality in capitals)."""
        locality = self.locality.strip().title()
        parts = [part for part in (locality, self.region.strip()) if part]
        return ", ".join(parts)[:200]


def _raise_for(status: int, body: dict[str, Any], *, action: str) -> None:
    """Map a Twirp error response to an api error (never returns for a non-200)."""
    code = str(body.get("code") or "")
    message = str(body.get("msg") or "") or f"HTTP {status}"
    details = {"livekit_code": code or None, "http_status": status}
    if status in (401, 403) or code in ("unauthenticated", "permission_denied"):
        raise PhoneNumbersUnavailableError(
            f"LiveKit refused {action}: {message}. The request was signed with {GRANT_DESCRIPTION}; "
            "check that this project has LiveKit Phone Numbers and that the connection's key may manage SIP.",
            details={**details, "grant": GRANT_DESCRIPTION},
        )
    if code == "not_found" or status == 404:
        raise NotFoundError(f"LiveKit: {message}", details=details)
    if 400 <= status < 500:
        raise UnprocessableEntityError(f"LiveKit rejected {action}: {message}", details=details)
    raise LiveKitUpstreamError(f"{action} failed: {message}", details=details)


class PhoneNumberClient:
    """``ListPhoneNumbers`` / ``GetPhoneNumber`` / ``UpdatePhoneNumber`` over Twirp-JSON."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: Callable[[], str]) -> None:
        """Build a client.

        Args:
            session: A network-guarded aiohttp session (the factory owns and closes it).
            url: The connection's LiveKit url (``wss://…`` is posted to as ``https://…``).
            token: Returns a fresh signed JWT for each request. **Never logged.**
        """
        self._session = session
        self._origin = http_origin(url)
        self._token = token

    async def _call(self, method: str, body: dict[str, Any], *, action: str) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._token()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        url = f"{self._origin}/twirp/{SERVICE}/{method}"
        try:
            async with self._session.post(url, json=body, headers=headers) as resp:
                try:
                    payload = await resp.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    payload = None
                data: dict[str, Any] = payload if isinstance(payload, dict) else {}
                if resp.status != 200:
                    _raise_for(resp.status, data, action=action)
                return data
        except (TimeoutError, aiohttp.ClientError, OSError) as exc:
            raise LiveKitUpstreamError(f"{action} failed: {type(exc).__name__}: {exc}") from exc

    async def list(self, statuses: Sequence[str] = ("active", "pending", "offline")) -> list[LkPhoneNumber]:
        """Every number of the project in ``statuses``, following ``next_page_token`` to the end.

        Raises:
            PhoneNumbersUnavailableError: The key may not use the service (409).
            LiveKitUpstreamError: Transport failure or a LiveKit 5xx (502).
        """
        base: dict[str, Any] = {"statuses": [_STATUS_NAME[s] for s in statuses if s in _STATUS_NAME]}
        body: dict[str, Any] = {**base, "limit": PAGE_LIMIT}
        out: list[LkPhoneNumber] = []
        for _ in range(MAX_PAGES):
            data = await self._call("ListPhoneNumbers", body, action="listing the project's phone numbers")
            out.extend(LkPhoneNumber.model_validate(item) for item in data.get("items") or [])
            token = data.get("next_page_token", data.get("nextPageToken"))
            if not token or (isinstance(token, dict) and not token.get("token")):
                return out
            body = {**base, "pageToken": token}
        log.warning("phone_numbers_paging_stopped", pages=MAX_PAGES, seen=len(out))
        return out

    async def get(self, number_id: str) -> LkPhoneNumber:
        """One number by its LiveKit id.

        Raises:
            NotFoundError: LiveKit does not know the id.
        """
        data = await self._call("GetPhoneNumber", {"id": number_id}, action="reading the phone number")
        return LkPhoneNumber.model_validate(data.get("phone_number") or data.get("phoneNumber") or {})

    async def update(self, number_id: str, *, sip_dispatch_rule_id: str | None) -> LkPhoneNumber:
        """Attach the number to one dispatch rule; ``None`` sends ``""`` (the detach attempt).

        Raises:
            UnprocessableEntityError: LiveKit rejected the change (e.g. an unknown rule id).
        """
        body = {"id": number_id, "sipDispatchRuleId": sip_dispatch_rule_id or ""}
        data = await self._call("UpdatePhoneNumber", body, action="attaching the phone number")
        found = data.get("phone_number") or data.get("phoneNumber") or {"id": number_id}
        return LkPhoneNumber.model_validate(found)


def http_origin(url: str) -> str:
    """``wss://host[:port]/path`` → ``https://host[:port]`` (the SDK's own conversion)."""
    scheme, sep, rest = url.partition("://")
    if not sep:
        return url.rstrip("/")
    if scheme.startswith("ws"):
        scheme = "http" + scheme[2:]
    host = rest.split("/", 1)[0]
    return f"{scheme}://{host}"


def sign(api_key: str, api_secret: str) -> str:
    """A JWT for one request with the SIP admin grant. **The result is a credential.**"""
    return AccessToken(api_key, api_secret).with_sip_grants(SIPGrants(admin=True)).to_jwt()
