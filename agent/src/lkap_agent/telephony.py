"""SIP call handling on the worker side (PLAN-V2 V2-17, ARCHITECTURE-V2 §2.6).

A phone call reaches the worker as an ordinary job whose dispatch metadata
says ``channel="sip_in"`` (a LiveKit dispatch rule created the room) or
``"sip_out"`` (the api dialed out with ``CreateSIPParticipant``). The session
itself runs exactly like a web session; this module adds what only a phone
leg has, through one object per job, :class:`TelephonySession`:

* **Caller and call status** (asks #55). The SIP participant's attributes
  (``sip.phoneNumber``, ``sip.trunkPhoneNumber``, ``sip.callID``,
  ``sip.trunkID``, ``sip.callStatus``) are only readable in a connected room,
  so once the leg is present (or its ``sip.callStatus`` turns ``active``) the
  worker reports ``answered`` with those values to
  ``POST /internal/v1/telephony/calls/report``. The api fills
  ``sessions.caller``, creates the inbound ``calls`` row (or links the one a
  webhook recorded first) and replaces the ``sip_in-caller`` placeholder
  identity. ``completed`` is reported when the job ends. Webhooks feed the same
  forward-only status machine, so the order never matters.
* **DTMF in**: ``room.on("sip_dtmf_received")`` digits are buffered
  (:class:`DtmfCollector`: flushed on ``#`` or after a pause), recorded as a
  ``dtmf`` session event, then handed to the pack's optional
  ``on_dtmf(ctx, digits) -> bool`` hook (:class:`DtmfPack`). When no hook
  consumes them and the agent has ``capabilities.dtmf``, they become a user
  turn (``generate_reply(user_input=…)``).
* **DTMF out**: the ``send_dtmf`` built-in tool, and ``POST /v1/calls/{id}/dtmf``
  from the console, which arrives as a reliable data packet on
  :data:`DTMF_TOPIC` (the server SDK has no participant RPC). Only packets the
  **server** sent (no participant) are honoured, so a room participant cannot
  make the agent dial tones. Digits go out with ``publish_dtmf`` (RFC 4733),
  0.3 s apart.
* **Cold transfer**: the ``transfer_call`` built-in tool asks the api
  (``POST /internal/v1/telephony/sessions/{id}/transfer``), which issues
  ``SipService.transfer_sip_participant`` (SIP REFER) and updates the call row.
  Destinations are an allowlist, never free text from the model: the tool is
  registered only when ``config.pack_settings["transfer_targets"]`` names at
  least one (``{"Sales": "+15551230000"}`` or a list of numbers/URIs).

Nothing here runs for web, test or text sessions: :func:`is_sip_channel`.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

import httpx
from livekit import rtc

from lkap_agent.logging import get_logger

logger = get_logger(__name__)

#: Data-packet topic the api uses for console DTMF (``lkap_api.telephony.common.DTMF_TOPIC``).
DTMF_TOPIC = "lkap.telephony.dtmf"

#: Session channels that are phone calls (CONTRACTS-V2 §4.6).
SIP_CHANNELS: frozenset[str] = frozenset({"sip_in", "sip_out"})

#: SIP participant attributes LiveKit sets.
ATTR_CALL_STATUS = "sip.callStatus"
ATTR_CALL_ID = "sip.callID"
ATTR_PHONE_NUMBER = "sip.phoneNumber"
ATTR_TRUNK_PHONE_NUMBER = "sip.trunkPhoneNumber"
ATTR_TRUNK_ID = "sip.trunkID"

#: Pause between published tones, as the SDK's own ``send_dtmf_events`` does.
DTMF_GAP_S = 0.3

#: Inter-digit pause after which buffered keypad input is handed on.
DTMF_FLUSH_AFTER_S = 2.5

#: Keys that end a keypad entry at once.
DTMF_TERMINATORS = "#"

#: Digits LiveKit can publish (RFC 4733 events 0–15).
DTMF_DIGITS = re.compile(r"^[0-9*#A-D]{1,32}$")

#: ``pack_settings`` key holding the transfer allowlist.
TRANSFER_TARGETS_KEY = "transfer_targets"

#: The built-in tool names ``AgentConfig.tools.builtin_disabled`` can switch off.
TELEPHONY_TOOL_NAMES: tuple[str, ...] = ("send_dtmf", "transfer_call")

_SERVICE_TOKEN_HEADER = "X-Service-Token"
_TRANSFER_TARGET = re.compile(r"^(\+[1-9]\d{6,14}|tel:\+?[0-9]{3,20}|sips?:[^\s@]+@[^\s]+)$")


def is_sip_channel(channel: str) -> bool:
    """Whether a session channel is a phone call."""
    return channel in SIP_CHANNELS


def dtmf_code(digit: str) -> int:
    """The RFC 4733 event code of one keypad digit (``*`` = 10, ``#`` = 11, ``A``–``D`` = 12–15).

    Raises:
        ValueError: Not a DTMF digit.
    """
    if len(digit) == 1 and digit.isdigit():
        return int(digit)
    codes = {"*": 10, "#": 11, "A": 12, "B": 13, "C": 14, "D": 15}
    if digit not in codes:
        raise ValueError(f"not a DTMF digit: {digit!r}")
    return codes[digit]


async def publish_digits(
    room: rtc.Room, digits: str, *, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
) -> None:
    """Play ``digits`` into the call as DTMF tones, :data:`DTMF_GAP_S` apart.

    Raises:
        ValueError: ``digits`` holds a non-DTMF character (nothing is sent).
    """
    if not DTMF_DIGITS.match(digits):
        raise ValueError("digits must be 0-9, *, # or A-D (at most 32)")
    for index, digit in enumerate(digits):
        if index:
            await sleep(DTMF_GAP_S)
        await room.local_participant.publish_dtmf(code=dtmf_code(digit), digit=digit)


def sip_participant(room: rtc.Room) -> rtc.RemoteParticipant | None:
    """The call's SIP participant, if it is in the room."""
    for participant in room.remote_participants.values():
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            return participant
    return None


async def wait_for_answer(room: rtc.Room, *, timeout_s: float) -> rtc.RemoteParticipant | None:
    """Wait until the call's SIP leg is answered (``sip.callStatus == "active"``).

    An outbound leg joins the room while it is still ringing and RoomIO links
    it at once, so an agent that speaks first would greet a ringing phone.
    Call this between ``ctx.connect()`` and ``session.start`` on ``sip_out``.
    A leg without the attribute (older SIP service) counts as answered.

    Returns:
        The answered participant, or ``None`` after ``timeout_s``.
    """

    def answered(participant: Any) -> bool:
        if getattr(participant, "kind", None) != rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            return False
        return bool(dict(participant.attributes).get(ATTR_CALL_STATUS, "active") == "active")

    for participant in room.remote_participants.values():
        if answered(participant):
            return participant
    loop = asyncio.get_running_loop()
    done: asyncio.Future[rtc.RemoteParticipant | None] = loop.create_future()

    def on_change(*args: Any) -> None:
        participant = args[-1]
        if not done.done() and answered(participant):
            done.set_result(participant)

    def on_disconnected(*_args: Any) -> None:
        if not done.done():  # the api deleted the room: the dial failed
            done.set_result(None)

    room.on("participant_connected", on_change)
    room.on("participant_attributes_changed", on_change)
    room.on("disconnected", on_disconnected)
    try:
        return await asyncio.wait_for(done, timeout=timeout_s)
    except TimeoutError:
        return None
    finally:
        room.off("participant_connected", on_change)
        room.off("participant_attributes_changed", on_change)
        room.off("disconnected", on_disconnected)


def caller_info(attributes: Mapping[str, str], *, channel: str) -> dict[str, str]:
    """``from``/``to``/``call_id``/``trunk_id`` of a leg, oriented by the call's direction."""
    remote = attributes.get(ATTR_PHONE_NUMBER, "")
    ours = attributes.get(ATTR_TRUNK_PHONE_NUMBER, "")
    inbound = channel != "sip_out"
    return {
        "from": remote if inbound else ours,
        "to": ours if inbound else remote,
        "call_id": attributes.get(ATTR_CALL_ID, ""),
        "trunk_id": attributes.get(ATTR_TRUNK_ID, ""),
    }


def transfer_targets(pack_settings: Mapping[str, Any]) -> dict[str, str]:
    """The agent's transfer allowlist as ``{label: target}`` (invalid entries dropped).

    Accepts ``{"Sales": "+15551230000"}`` or ``["+15551230000", "sip:desk@pbx"]``.
    """
    raw = pack_settings.get(TRANSFER_TARGETS_KEY)
    pairs: list[tuple[str, Any]]
    if isinstance(raw, Mapping):
        pairs = [(str(k), v) for k, v in raw.items()]
    elif isinstance(raw, list):
        pairs = [(str(v), v) for v in raw]
    else:
        return {}
    return {
        label.strip(): str(target).strip()
        for label, target in pairs
        if isinstance(target, str) and _TRANSFER_TARGET.match(target.strip()) and label.strip()
    }


# ------------------------------------------------------------------------ api client
@dataclass(frozen=True, slots=True)
class TransferResult:
    """What the api answered to a transfer request."""

    ok: bool
    status: str
    reason: str | None = None


class TelephonyApi(Protocol):
    """The two worker → api telephony calls (injectable for tests)."""

    async def report(self, body: dict[str, Any]) -> None:
        """``POST /internal/v1/telephony/calls/report`` (best effort, never raises)."""
        ...

    async def transfer(self, session_id: str, to: str, participant_identity: str | None) -> TransferResult:
        """``POST /internal/v1/telephony/sessions/{id}/transfer``."""
        ...

    async def aclose(self) -> None:
        """Release the HTTP pool."""
        ...


class HttpTelephonyApi:
    """``httpx`` implementation of :class:`TelephonyApi` over the service token.

    Kept here rather than on ``config_client.ConfigClient`` (V2-07's file);
    ``docs/v2/_asks.md`` V2-17-3 proposes folding it in.
    """

    def __init__(self, base_url: str, service_token: str, *, client: httpx.AsyncClient | None = None) -> None:
        """Create the client.

        Args:
            base_url: The api base url (``LKAP_API_BASE_URL``).
            service_token: ``LKAP_SERVICE_TOKEN``.
            client: An existing client (tests pass one with a mock transport).
        """
        self._owns = client is None
        self._client = client or httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=10.0)
        self._client.headers[_SERVICE_TOKEN_HEADER] = service_token

    async def report(self, body: dict[str, Any]) -> None:
        """Report a leg's status; failures are logged, never raised."""
        try:
            response = await self._client.post("/internal/v1/telephony/calls/report", json=body)
            if response.status_code >= 400:
                logger.warning(
                    "call report rejected", status_code=response.status_code, status=body.get("status")
                )
        except httpx.HTTPError as exc:
            logger.warning("call report failed", error=type(exc).__name__, status=body.get("status"))

    async def transfer(self, session_id: str, to: str, participant_identity: str | None) -> TransferResult:
        """Ask the api to REFER the caller to ``to``; transport failures become a failed result."""
        try:
            response = await self._client.post(
                f"/internal/v1/telephony/sessions/{session_id}/transfer",
                json={"to": to, "participant_identity": participant_identity},
                timeout=60.0,
            )
        except httpx.HTTPError as exc:
            return TransferResult(ok=False, status="failed", reason=f"api unreachable: {type(exc).__name__}")
        if response.status_code >= 400:
            return TransferResult(ok=False, status="failed", reason=f"api answered {response.status_code}")
        data = response.json()
        return TransferResult(
            ok=bool(data.get("ok")), status=str(data.get("status", "")), reason=data.get("reason")
        )

    async def aclose(self) -> None:
        """Close the HTTP pool if this client owns it."""
        if self._owns:
            await self._client.aclose()


# ----------------------------------------------------------------------------- DTMF in
class DtmfPack(Protocol):
    """The optional ``on_dtmf`` hook a pack may add (looked up with ``getattr``, like ``on_block_action``).

    Return ``True`` to consume the digits (the model never sees them).
    """

    async def on_dtmf(self, ctx: Any, digits: str) -> bool:
        """Handle a keypad entry."""
        ...


class DtmfCollector:
    """Buffers keypad digits into one entry: flushed on a terminator or after a pause."""

    def __init__(
        self,
        on_entry: Callable[[str], Awaitable[None]],
        *,
        flush_after_s: float = DTMF_FLUSH_AFTER_S,
        terminators: str = DTMF_TERMINATORS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Create a collector.

        Args:
            on_entry: Awaited with each complete entry (terminator included).
            flush_after_s: Inter-digit pause that completes an entry.
            terminators: Digits that complete an entry at once.
            sleep: Clock seam for tests.
        """
        self._on_entry = on_entry
        self._flush_after_s = flush_after_s
        self._terminators = terminators
        self._sleep = sleep
        self._buffer = ""
        self._timer: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def pending(self) -> str:
        """Digits received but not handed on yet."""
        return self._buffer

    def push(self, digit: str) -> None:
        """Add one digit (synchronous: called from a room event handler)."""
        self._buffer += digit
        self._cancel_timer()
        if digit in self._terminators:
            self._spawn(self.flush())
        else:
            self._timer = self._spawn(self._flush_later())

    async def _flush_later(self) -> None:
        await self._sleep(self._flush_after_s)
        self._timer = None
        await self.flush()

    async def flush(self) -> None:
        """Hand the buffered entry on now (no-op when empty)."""
        entry, self._buffer = self._buffer, ""
        if entry:
            try:
                await self._on_entry(entry)
            except Exception:  # noqa: BLE001 - a hook failure must not kill the room handler
                logger.warning("dtmf entry handler failed", exc_info=True)

    def _spawn(self, coro: Awaitable[None]) -> asyncio.Task[None]:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _cancel_timer(self) -> None:
        if self._timer is not None and not self._timer.done():
            self._timer.cancel()
        self._timer = None

    async def aclose(self) -> None:
        """Cancel the pending timer and wait for in-flight entries."""
        self._cancel_timer()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)


# --------------------------------------------------------------------------- session
RecordEvent = Callable[[str, dict[str, Any]], None]


class TelephonySession:
    """Per-job telephony wiring for a SIP session (see the module docstring)."""

    def __init__(
        self,
        *,
        room: rtc.Room,
        session_id: str,
        channel: str,
        pack_ctx: Any,
        pack: Any,
        api: TelephonyApi,
        record_event: RecordEvent,
        dtmf_to_model: bool,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        flush_after_s: float = DTMF_FLUSH_AFTER_S,
    ) -> None:
        """Create the wiring; nothing is registered until :meth:`start`.

        Args:
            room: The job's (connected) room.
            session_id: The platform session id.
            channel: ``sip_in`` or ``sip_out``.
            pack_ctx: The session's ``PackSessionContext`` (``session`` is used for DTMF turns).
            pack: The loaded pack (its optional ``on_dtmf`` hook).
            api: Worker → api telephony client.
            record_event: Session-event recorder (the observer's ``record``).
            dtmf_to_model: ``capabilities.dtmf``: unconsumed keypad entries become user turns.
            sleep: Clock seam for tests.
            flush_after_s: Keypad inter-digit pause.
        """
        self._room = room
        self._session_id = session_id
        self._channel = channel
        self._pack_ctx = pack_ctx
        self._pack = pack
        self._api = api
        self._record = record_event
        self._dtmf_to_model = dtmf_to_model
        self._sleep = sleep
        self._dtmf = DtmfCollector(self._on_dtmf_entry, flush_after_s=flush_after_s, sleep=sleep)
        self._answered = False
        self._closed = False
        self._identity: str | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._started = False

    @property
    def participant_identity(self) -> str | None:
        """The SIP leg's identity once seen."""
        return self._identity

    def start(self) -> None:
        """Register the room handlers and report a leg that is already present."""
        self._room.on("sip_dtmf_received", self._on_sip_dtmf)
        self._room.on("data_received", self._on_data)
        self._room.on("participant_connected", self._on_participant)
        self._room.on("participant_attributes_changed", self._on_attributes_changed)
        self._started = True
        logger.info("telephony started", channel=self._channel)
        participant = sip_participant(self._room)
        if participant is not None:
            self._on_participant(participant)

    async def aclose(self, *, reason: str = "call ended") -> None:
        """Unregister, flush pending keypad input, and report the leg ``completed``."""
        if self._closed:
            return
        self._closed = True
        if self._started:
            self._room.off("sip_dtmf_received", self._on_sip_dtmf)
            self._room.off("data_received", self._on_data)
            self._room.off("participant_connected", self._on_participant)
            self._room.off("participant_attributes_changed", self._on_attributes_changed)
        await self._dtmf.flush()
        await self._dtmf.aclose()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._api.report(
            {
                "session_id": self._session_id,
                "status": "completed" if self._answered else "failed",
                "participant_identity": self._identity,
                "reason": reason[:128],
            }
        )

    # --------------------------------------------------------------- leg status
    def _on_participant(self, participant: rtc.RemoteParticipant) -> None:
        if participant.kind != rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            return
        self._identity = participant.identity
        self._maybe_answered(participant)

    def _on_attributes_changed(self, changed: Mapping[str, str], participant: rtc.Participant) -> None:
        # The local participant is never a SIP leg, so the kind check in
        # `_on_participant` is the only filter needed.
        if ATTR_CALL_STATUS in changed:
            self._on_participant(cast(rtc.RemoteParticipant, participant))

    def _maybe_answered(self, participant: rtc.RemoteParticipant) -> None:
        attributes = dict(participant.attributes)
        status = attributes.get(ATTR_CALL_STATUS, "")
        if self._answered or status not in ("", "active"):
            return
        self._answered = True
        info = caller_info(attributes, channel=self._channel)
        self._record("sip_answered", {"participant_identity": participant.identity, **info})
        self._spawn(
            self._api.report(
                {
                    "session_id": self._session_id,
                    "status": "answered",
                    "direction": "outbound" if self._channel == "sip_out" else "inbound",
                    "participant_identity": participant.identity,
                    "sip_call_id": info["call_id"] or None,
                    "from_e164": info["from"] or None,
                    "to_e164": info["to"] or None,
                }
            )
        )

    # ------------------------------------------------------------------- DTMF in
    def _on_sip_dtmf(self, event: rtc.SipDTMF) -> None:
        if (
            event.participant is not None
            and event.participant.kind != rtc.ParticipantKind.PARTICIPANT_KIND_SIP
        ):
            return
        if event.digit:
            self._dtmf.push(event.digit)

    async def _on_dtmf_entry(self, digits: str) -> None:
        self._record("dtmf", {"direction": "received", "digits": digits})
        hook = getattr(self._pack, "on_dtmf", None)
        if callable(hook):
            consumed = hook(self._pack_ctx, digits)
            if inspect.isawaitable(consumed):
                consumed = await consumed
            if consumed is True:
                return
        if not self._dtmf_to_model:
            return
        spaced = " ".join(digits)
        result: Any = self._pack_ctx.session.generate_reply(
            user_input=f"[The caller pressed these keys on the phone keypad: {spaced}]"
        )
        if inspect.isawaitable(result):
            await result

    # ------------------------------------------------------------------- DTMF out
    def _on_data(self, packet: rtc.DataPacket) -> None:
        if packet.topic != DTMF_TOPIC or packet.participant is not None:
            return  # only the server (the api) may make the agent play tones
        try:
            body = json.loads(packet.data)
            digits = str(body.get("digits", ""))
        except (ValueError, AttributeError):
            logger.warning("malformed dtmf control packet")
            return
        if body.get("op") != "dtmf" or not DTMF_DIGITS.match(digits):
            logger.warning("ignored dtmf control packet", op=body.get("op"))
            return
        self._spawn(self.send_digits(digits, source="console"))

    async def send_digits(self, digits: str, *, source: str) -> None:
        """Publish tones and record a ``dtmf`` event (``source``: ``console`` or ``tool``)."""
        await publish_digits(self._room, digits, sleep=self._sleep)
        self._record("dtmf", {"direction": "sent", "digits": digits, "source": source})

    # -------------------------------------------------------------------- transfer
    async def transfer(self, to: str) -> TransferResult:
        """Cold-transfer the caller through the api and record a ``transfer`` event."""
        identity = self._identity
        if identity is None:
            participant = sip_participant(self._room)
            identity = participant.identity if participant else None
        result = await self._api.transfer(self._session_id, to, identity)
        self._record(
            "transfer", {"to": to, "ok": result.ok, "status": result.status, "reason": result.reason}
        )
        return result

    async def flow_transfer(self, node: Any, state: Any) -> bool:
        """``FlowServices.transfer`` for flow ``transfer`` nodes (asks V2-15-6).

        The node's ``to`` is author-configured, so it needs no allowlist. Warm
        transfer is Phase 2: a ``warm`` node is transferred cold and says so in
        the event. The flow runtime speaks ``announce`` itself.
        """
        if getattr(node, "mode", "cold") != "cold":
            self._record("info", {"message": "warm transfer is not available yet; transferring cold"})
        result = await self.transfer(str(node.to))
        return result.ok

    def tools(self, *, config: Any, shutdown: Callable[[str], None] | None = None) -> list[Any]:
        """``send_dtmf`` / ``transfer_call`` for this session, gated by the agent config."""
        return build_telephony_tools(
            self,
            self._pack_ctx,
            disabled=list(config.tools.builtin_disabled),
            dtmf_enabled=bool(config.capabilities.dtmf),
            targets=transfer_targets(config.pack_settings),
            shutdown=shutdown,
        )

    def _spawn(self, coro: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


# ------------------------------------------------------------------------ assembly
def build_telephony_tools(
    telephony: TelephonySession,
    pack_ctx: Any,
    *,
    disabled: list[str],
    dtmf_enabled: bool,
    targets: Mapping[str, str],
    shutdown: Callable[[str], None] | None = None,
) -> list[Any]:
    """The phone-only built-in tools this session gets.

    ``send_dtmf`` needs ``capabilities.dtmf``; ``transfer_call`` needs a
    non-empty transfer allowlist. Either can be switched off with
    ``tools.builtin_disabled``.
    """
    from lkap_agent.tools.builtin.send_dtmf import build_send_dtmf_tool  # noqa: PLC0415
    from lkap_agent.tools.builtin.transfer_call import build_transfer_call_tool  # noqa: PLC0415

    skip = set(disabled)
    tools: list[Any] = []
    if dtmf_enabled and "send_dtmf" not in skip:
        tools.append(
            build_send_dtmf_tool(pack_ctx, send=lambda digits: telephony.send_digits(digits, source="tool"))
        )
    if targets and "transfer_call" not in skip:
        tools.append(
            build_transfer_call_tool(
                pack_ctx, targets=dict(targets), transfer=telephony.transfer, shutdown=shutdown
            )
        )
    return tools


def session_for(
    *,
    room: rtc.Room,
    resolved: Any,
    pack_ctx: Any,
    pack: Any,
    record_event: RecordEvent,
    api: TelephonyApi,
) -> TelephonySession | None:
    """The telephony wiring of a job, or ``None`` when it is not a phone call.

    Built in ``main._assemble`` (before the agent, so :meth:`TelephonySession.tools`
    join the tool pool of prompt *and* flow agents and
    :meth:`TelephonySession.flow_transfer` can be handed to ``FlowServices``);
    :meth:`TelephonySession.start` runs once the session has started and
    :meth:`TelephonySession.aclose` in the shutdown callback. The exact wiring
    is ``docs/v2/_asks.md`` V2-17-2.

    Args:
        room: The job room (connected later).
        resolved: The ``ResolvedAgentConfig``.
        pack_ctx: The session's ``PackSessionContext``.
        pack: The session's pack (its optional ``on_dtmf`` hook).
        record_event: The observer's ``record``.
        api: Worker → api telephony client.
    """
    if not is_sip_channel(resolved.channel):
        return None
    return TelephonySession(
        room=room,
        session_id=resolved.session_id,
        channel=resolved.channel,
        pack_ctx=pack_ctx,
        pack=pack,
        api=api,
        record_event=record_event,
        dtmf_to_model=bool(resolved.config.capabilities.dtmf),
    )
