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
  registered only when ``config.telephony.transfer_targets`` names at least
  one (R-V2-21), and the api checks every target against the workspace's
  dialing policy again before it dials (R-V2-23).
* **Warm transfer** (V5-32, D-V5-21): a target with ``mode="warm"`` runs
  :func:`run_warm_transfer` — livekit-agents 1.8.3's beta ``WarmTransferTask``
  (hold music from ``BuiltinAudioClip.HOLD_MUSIC`` for the caller, a private
  consult room, the person dialled through the connection's outbound trunk and
  briefed from the conversation, then ``MoveParticipant`` into the caller's
  room). It runs only on a LiveKit Cloud connection whose resolved document
  carries a :class:`~lkap_contracts.telephony.WarmTransferRoute` naming the
  target (the api vets it against the dialing policy, since the worker dials
  it itself); otherwise :meth:`TelephonySession.hand_over` transfers cold and
  keeps the summary on the call row. Nobody answering raises a ``ToolError``
  and the conversation resumes. The ``handoff`` block follows along
  (``requested → connecting → connected | timeout | ended``).
* **Answering-machine detection** (V5-32): :class:`AmdRunner` wraps
  livekit-agents 1.8.3's ``AMD`` for outbound calls with
  ``config.telephony.amd.enabled``. The api places the call after dispatching
  the agent, so ``main._start`` waits for the leg to *join*, starts the
  session, enters the detector (which holds the agent's speech and waits for
  ``sip.callStatus == "active"`` itself), then waits for the answer; the
  verdict goes to the api on the call report and a machine is hung up on or
  left a message (``on_machine``).
* **Call variables** (R-V2-22): :func:`apply_call_variables` appends an
  outbound call's ``variables`` to a prompt agent's instructions; flow agents
  seed ``FlowState.variables`` with them instead.

Wiring (R-V2-20, ``main.py``): :func:`session_for` builds the object in
``_assemble`` with ``api=deps.config_client`` (``report_call`` /
``transfer_call`` live on ``ConfigClientProtocol``) and stores it as
``SessionContext.userdata["telephony"]``; ``_start`` waits for the callee with
:func:`wait_for_answer` on ``sip_out``; :meth:`TelephonySession.start` runs once
the session is up and :meth:`TelephonySession.aclose` in the shutdown callback.

Nothing here runs for web, test or text sessions: :func:`is_sip_channel`.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

from livekit import rtc
from lkap_contracts.agent_config import PanelLayout, ResolvedAgentConfig
from lkap_contracts.api_models import CallReportIn, InternalTransferOut, TransferEvent, VoicemailEvent
from lkap_contracts.telephony import (
    AMD_MACHINE_RESULTS,
    DTMF_PATTERN,
    MAX_TRANSFER_SUMMARY_CHARS,
    AmdConfig,
    TelephonyConfig,
    WarmTransferRoute,
)
from lkap_contracts.ui_protocol import HandoffStatus
from packs.base import DtmfPack

from lkap_agent.flow.runtime import is_flow
from lkap_agent.flow.variables import VariableValue, known_variables_block
from lkap_agent.logging import get_logger
from lkap_agent.ui.blocks import set_handoff

__all__ = [
    "AMD_USERDATA_KEY",
    "DEFAULT_VOICEMAIL_MESSAGE",
    "DTMF_TOPIC",
    "SIP_CHANNELS",
    "TELEPHONY_TOOL_NAMES",
    "TRANSFER_EVENT",
    "VOICEMAIL_EVENT",
    "WARM_RING_TIMEOUT_S",
    "AmdRunner",
    "DtmfCollector",
    "DtmfPack",
    "HandOver",
    "HandOverResult",
    "TelephonyApi",
    "TelephonySession",
    "WarmRequest",
    "WarmResult",
    "amd_config_for",
    "amd_supported",
    "apply_call_variables",
    "build_telephony_tools",
    "caller_info",
    "default_amd_factory",
    "dtmf_code",
    "hang_up",
    "is_sip_channel",
    "publish_digits",
    "run_warm_transfer",
    "seed_variables",
    "session_for",
    "sip_participant",
    "transfer_modes",
    "transfer_targets",
    "wait_for_answer",
    "wait_for_sip_participant",
    "warm_transfer_task_class",
]

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

#: Digits LiveKit can publish (RFC 4733 events 0-15; ``lkap_contracts.telephony.DTMF_PATTERN``).
DTMF_DIGITS = re.compile(DTMF_PATTERN)

#: The phone-only built-in tools (``tools.builtin_disabled`` switches them off; the console
#: shows them as ``TELEPHONY_TOOLS``). Re-exported by ``lkap_agent.tools.builtin``.
TELEPHONY_TOOL_NAMES: tuple[str, ...] = ("send_dtmf", "transfer_call")

#: Session event of a transfer (`lkap_contracts.api_models.TransferEvent`).
TRANSFER_EVENT = "transfer"

#: Session event of a machine answering an outbound call (`VoicemailEvent`, V5-32).
VOICEMAIL_EVENT = "voicemail"

#: `SessionContext.userdata` key of the outbound call's :class:`AmdRunner` (V5-32).
AMD_USERDATA_KEY = "lkap.amd"

#: What the agent says to a voicemail when ``amd.message`` is empty.
DEFAULT_VOICEMAIL_MESSAGE = (
    "Hello, sorry we missed you. Please call us back when you have a moment. Thank you, goodbye."
)

#: How long a warm transfer lets the person's phone ring before the caller is taken off hold.
WARM_RING_TIMEOUT_S = 30.0

#: Longest the agent waits for its voicemail message to finish playing.
_VOICEMAIL_PLAYOUT_TIMEOUT_S = 60.0

#: Longest the worker waits for LiveKit to delete the room when it hangs up.
_HANGUP_TIMEOUT_S = 5.0


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


async def wait_for_sip_participant(room: rtc.Room, *, timeout_s: float) -> rtc.RemoteParticipant | None:
    """Wait until the call's SIP leg has *joined* the room (ringing or answered).

    Answering-machine detection must be listening before the callee answers
    (V5-32), so an outbound job with AMD starts its session once the leg is in
    the room, not once it is answered (:func:`wait_for_answer`).

    Returns:
        The SIP participant, or ``None`` after ``timeout_s`` or when the room
        closes first (the api deleted it: the dial failed).
    """
    present = sip_participant(room)
    if present is not None:
        return present
    loop = asyncio.get_running_loop()
    done: asyncio.Future[rtc.RemoteParticipant | None] = loop.create_future()

    def on_connected(participant: Any) -> None:
        if not done.done() and getattr(participant, "kind", None) == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            done.set_result(participant)

    def on_disconnected(*_args: Any) -> None:
        if not done.done():
            done.set_result(None)

    room.on("participant_connected", on_connected)
    room.on("disconnected", on_disconnected)
    try:
        return await asyncio.wait_for(done, timeout=timeout_s)
    except TimeoutError:
        return None
    finally:
        room.off("participant_connected", on_connected)
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


def transfer_targets(telephony: TelephonyConfig) -> dict[str, str]:
    """The agent's transfer allowlist as ``{label: target}`` (R-V2-21: ``config.telephony`` only).

    ``pack_settings["transfer_targets"]`` is retired and never read. Labels
    are unique case-insensitively (the api refuses duplicates at save); should
    a stored config still carry one, the first entry wins.
    """
    targets: dict[str, str] = {}
    seen: set[str] = set()
    for target in telephony.transfer_targets:
        label = target.label.strip()
        if label and label.casefold() not in seen:
            seen.add(label.casefold())
            targets[label] = target.to.strip()
    return targets


def transfer_modes(telephony: TelephonyConfig) -> dict[str, str]:
    """``{label: mode}`` of the transfer allowlist, keyed like :func:`transfer_targets` (V5-32)."""
    modes: dict[str, str] = {}
    seen: set[str] = set()
    for target in telephony.transfer_targets:
        label = target.label.strip()
        if label and label.casefold() not in seen:
            seen.add(label.casefold())
            modes[label] = target.mode
    return modes


def seed_variables(variables: Mapping[str, Any]) -> dict[str, VariableValue]:
    """A session's seed variables as flow-variable values (R-V2-22).

    Scalars pass through; anything else (a list, an object) becomes its JSON
    text, because ``FlowState.variables`` holds scalars only.
    """
    values: dict[str, VariableValue] = {}
    for name, value in variables.items():
        if value is None or isinstance(value, str | int | float | bool):
            values[str(name)] = value
        else:
            values[str(name)] = json.dumps(value, ensure_ascii=False, default=str)
    return values


def apply_call_variables(resolved: ResolvedAgentConfig) -> ResolvedAgentConfig:
    """Append the session's seed variables to a prompt agent's instructions (R-V2-22).

    An outbound call's ``CallCreate.variables`` reach the worker as
    ``ResolvedAgentConfig.variables``. A prompt agent gets the same "already
    collected" block the flow runtime renders; a flow agent is returned
    unchanged (``FlowServices.initial_variables`` seeds its ``FlowState``).
    """
    if not resolved.variables or is_flow(resolved):
        return resolved
    block = known_variables_block(seed_variables(resolved.variables), [])
    if block is None:
        return resolved
    instructions = f"{resolved.config.instructions.rstrip()}\n\n{block}".lstrip()
    return resolved.model_copy(
        update={"config": resolved.config.model_copy(update={"instructions": instructions})}
    )


# ------------------------------------------------------------------------ api client
class TelephonyApi(Protocol):
    """The two worker → api telephony calls; ``ConfigClientProtocol`` provides both (R-V2-20)."""

    async def report_call(self, report: CallReportIn) -> None:
        """``POST /internal/v1/telephony/calls/report`` (best effort, never raises)."""
        ...

    async def transfer_call(
        self, session_id: str, to: str, participant_identity: str | None
    ) -> InternalTransferOut:
        """``POST /internal/v1/telephony/sessions/{id}/transfer`` (never raises)."""
        ...


# ----------------------------------------------------------------------------- DTMF in
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
        panel: PanelLayout | None = None,
        cloud: bool = False,
        warm_route: WarmTransferRoute | None = None,
        warm_runner: WarmRunner | None = None,
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
            panel: The agent's panel layout (the ``handoff`` block, V5-32).
            cloud: The session runs on a LiveKit Cloud connection (warm transfer, D-V5-21).
            warm_route: The api's vetted warm-transfer route (``ResolvedAgentConfig.warm_transfer``).
            warm_runner: Runs a warm transfer (default :func:`run_warm_transfer`; tests pass a fake).
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
        self._panel = panel if panel is not None else PanelLayout()
        self._cloud = cloud
        self._warm_route = warm_route
        self._warm_runner: WarmRunner = warm_runner or run_warm_transfer

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
        await self._api.report_call(
            CallReportIn(
                session_id=self._session_id,
                status="completed" if self._answered else "failed",
                participant_identity=self._identity,
                reason=reason[:128],
            )
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
            self._api.report_call(
                CallReportIn(
                    session_id=self._session_id,
                    status="answered",
                    direction="outbound" if self._channel == "sip_out" else "inbound",
                    participant_identity=participant.identity,
                    sip_call_id=info["call_id"][:128] or None,
                    from_e164=info["from"][:32] or None,
                    to_e164=info["to"][:32] or None,
                )
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
    async def transfer(self, to: str) -> InternalTransferOut:
        """Cold-transfer the caller through the api and record a ``transfer`` event.

        The api answers ``refused`` (reason "destination not allowed by the
        dialing policy") for a target outside the workspace's policy (R-V2-23).
        """
        identity = self._identity
        if identity is None:
            participant = sip_participant(self._room)
            identity = participant.identity if participant else None
        result = await self._api.transfer_call(self._session_id, to, identity)
        self._record(
            "transfer", {"to": to, "ok": result.ok, "status": result.status, "reason": result.reason}
        )
        return result

    def _leg_identity(self) -> str | None:
        if self._identity is not None:
            return self._identity
        participant = sip_participant(self._room)
        return participant.identity if participant else None

    def warm_unavailable(self, to: str) -> str | None:
        """Why ``to`` cannot be transferred warm here, in plain words; ``None`` = it can (D-V5-21)."""
        if not self._cloud:
            return "warm transfer needs a LiveKit Cloud connection"
        if self._warm_route is None:
            return "no outbound line is set up for warm transfer"
        if to not in self._warm_route.targets:
            return "this destination is not allowed for warm transfer"
        return None

    async def _handoff(self, status: HandoffStatus, **fields: Any) -> None:
        ui = getattr(self._pack_ctx, "ui", None)
        if ui is None:
            return
        try:
            await set_handoff(ui, self._panel, status, **fields)
        except Exception:  # noqa: BLE001 - the panel never fails a transfer
            logger.debug("handoff block update failed", exc_info=True)

    async def hand_over(self, request: HandOver) -> HandOverResult:
        """Hand the caller to a person: warm when the target and the connection allow it, else cold.

        Drives the ``handoff`` block, records one ``transfer`` event
        (:class:`~lkap_contracts.api_models.TransferEvent`) and, on success, reports
        ``transferred`` with the mode and the summary before the tool ends the job (so
        the call row has them whatever the job's teardown order).
        """
        summary = (request.summary or "").strip()[:MAX_TRANSFER_SUMMARY_CHARS] or None
        await self._handoff("requested", mode=request.mode, target=request.label)
        fallback = self.warm_unavailable(request.to) if request.mode == "warm" else None
        if request.mode == "warm" and fallback is None and self._warm_route is not None:
            return await self._warm(request, summary, self._warm_route)
        if fallback is not None:
            logger.info("warm transfer falls back to cold", reason=fallback)
            self._record(
                "info", {"message": f"Warm transfer is not available ({fallback}); transferring directly."}
            )
        await self._handoff("connecting", mode="cold", target=request.label)
        result = await self._api.transfer_call(self._session_id, request.to, self._leg_identity())
        outcome = "transferred" if result.ok else ("refused" if result.status == "refused" else "failed")
        self._record_transfer(request, result.ok, result.status, result.reason, "cold", outcome, summary)
        if result.ok:
            await self._report_transferred(request.to, "cold", summary)
            await self._handoff("ended", mode="cold", target=request.label)
        else:
            await self._handoff("timeout", mode="cold", target=request.label, reason=_plain_reason(outcome))
        return HandOverResult(
            ok=result.ok, status=result.status, reason=result.reason, mode="cold", outcome=outcome
        )

    async def _warm(self, request: HandOver, summary: str | None, route: WarmTransferRoute) -> HandOverResult:
        await self._handoff("connecting", mode="warm", target=request.label)
        warm = await self._warm_runner(
            WarmRequest(
                to=request.to, label=request.label, summary=summary, chat_ctx=request.chat_ctx, route=route
            )
        )
        status = "transferred" if warm.ok else warm.outcome
        self._record_transfer(request, warm.ok, status, warm.reason, "warm", warm.outcome, summary)
        if warm.ok:
            await self._report_transferred(request.to, "warm", summary)
            await self._handoff("connected", mode="warm", target=request.label)
        else:
            await self._handoff(
                "timeout", mode="warm", target=request.label, reason=_plain_reason(warm.outcome)
            )
        return HandOverResult(
            ok=warm.ok, status=status, reason=warm.reason, mode="warm", outcome=warm.outcome
        )

    def _record_transfer(
        self,
        request: HandOver,
        ok: bool,
        status: str,
        reason: str | None,
        mode: str,
        outcome: str,
        summary: str | None,
    ) -> None:
        payload = TransferEvent.model_validate(
            {
                "to": request.to,
                "ok": ok,
                "status": status,
                "reason": reason,
                "mode": mode,
                "requested_mode": request.mode,
                "target": request.label,
                "outcome": outcome,
                "summary": summary,
            }
        )
        self._record(TRANSFER_EVENT, payload.model_dump(mode="json"))

    async def _report_transferred(self, to: str, mode: str, summary: str | None) -> None:
        await self._api.report_call(
            CallReportIn.model_validate(
                {
                    "session_id": self._session_id,
                    "status": "transferred",
                    "participant_identity": self._leg_identity(),
                    "transfer_mode": mode,
                    "transfer_to": to[:256],
                    "transfer_summary": summary,
                }
            )
        )

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
            targets=transfer_targets(config.telephony),
            shutdown=shutdown,
            modes=transfer_modes(config.telephony),
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
    modes: Mapping[str, str] | None = None,
) -> list[Any]:
    """The phone-only built-in tools this session gets.

    ``send_dtmf`` needs ``capabilities.dtmf``; ``transfer_call`` needs a
    non-empty ``config.telephony.transfer_targets``. Either can be switched off
    with ``tools.builtin_disabled`` (the console's ``TELEPHONY_TOOLS`` toggles).
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
                pack_ctx,
                targets=dict(targets),
                transfer=telephony.transfer,
                shutdown=shutdown,
                modes=dict(modes or {}),
                hand_over=telephony.hand_over,
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
    :meth:`TelephonySession.aclose` in the shutdown callback (R-V2-20).

    Args:
        room: The job room (connected later).
        resolved: The ``ResolvedAgentConfig``.
        pack_ctx: The session's ``PackSessionContext``.
        pack: The session's pack (its optional ``on_dtmf`` hook).
        record_event: The observer's ``record``.
        api: Worker → api telephony calls (``deps.config_client``).
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
        panel=getattr(resolved, "panel", None),
        cloud=getattr(getattr(resolved, "connection", None), "deployment_type", "") == "cloud",
        warm_route=getattr(resolved, "warm_transfer", None),
    )


# ---------------------------------------------------------------- warm transfer (V5-32)
@dataclass(frozen=True, slots=True)
class HandOver:
    """One ``transfer_call`` request (label and number already matched against the allowlist)."""

    label: str
    to: str
    mode: str = "cold"
    summary: str | None = None
    chat_ctx: Any = None


@dataclass(frozen=True, slots=True)
class HandOverResult:
    """How :meth:`TelephonySession.hand_over` ended (the tool reads ``ok`` / ``status`` / ``reason``)."""

    ok: bool
    status: str
    reason: str | None = None
    mode: str = "cold"
    outcome: str = "transferred"


@dataclass(frozen=True, slots=True)
class WarmRequest:
    """What :func:`run_warm_transfer` needs."""

    to: str
    label: str
    summary: str | None
    chat_ctx: Any
    route: WarmTransferRoute


@dataclass(frozen=True, slots=True)
class WarmResult:
    """A warm transfer's end: ``connected``, or ``timeout`` / ``declined`` / ``failed`` with a reason."""

    ok: bool
    outcome: str
    reason: str | None = None


WarmRunner = Callable[[WarmRequest], Awaitable[WarmResult]]


def _plain_reason(outcome: str) -> str:
    return {
        "timeout": "Nobody answered.",
        "declined": "They could not take the call.",
        "refused": "That transfer is not allowed.",
    }.get(outcome, "The transfer did not go through.")


def _briefing(summary: str | None) -> str:
    if not summary:
        return ""
    return f"The assistant's own summary of the call, to share with them: {summary}"


@functools.cache
def warm_transfer_task_class() -> type[Any]:
    """LKAP's ``WarmTransferTask`` (livekit-agents 1.8.3 beta), built on first use.

    The beta import stays out of module import time, so a changed SDK breaks the
    tripwire test (``test_warm_transfer.py``), not the worker's start. The subclass
    only reports its steps — ``hold`` (the task starts the hold music), ``consult``
    (the private room and the dial), ``briefing`` (the person answered and the
    agent briefs them) and ``move`` (``MoveParticipant`` into the caller's room) —
    through ``on_step``; everything else is the SDK's.
    """
    from livekit.agents.beta.workflows import WarmTransferTask  # noqa: PLC0415

    class LkapWarmTransferTask(WarmTransferTask):
        def __init__(
            self,
            *,
            to: str,
            route: WarmTransferRoute,
            chat_ctx: Any,
            summary: str | None,
            on_step: Callable[[str], None],
            ringing_timeout: float = WARM_RING_TIMEOUT_S,
        ) -> None:
            kwargs: dict[str, Any] = {
                "sip_trunk_id": route.trunk_id,
                "sip_number": route.caller_id,
                "ringing_timeout": ringing_timeout,
                "extra_instructions": _briefing(summary),
            }
            if chat_ctx is not None:
                kwargs["chat_ctx"] = chat_ctx
            super().__init__(to, **kwargs)
            self._on_step = on_step

        async def on_enter(self) -> None:
            self._on_step("hold")
            await super().on_enter()

        async def _dial_human_agent(self) -> Any:
            self._on_step("consult")
            session = await super()._dial_human_agent()
            self._on_step("briefing")
            return session

        async def _merge_calls(self) -> None:
            self._on_step("move")
            await super()._merge_calls()

    return LkapWarmTransferTask


def _warm_outcome(exc: BaseException) -> WarmResult:
    message = str(exc) or type(exc).__name__
    lowered = message.lower()
    if "declined" in lowered:
        return WarmResult(ok=False, outcome="declined", reason=message[:300])
    if "could not dial" in lowered or "voicemail" in lowered or "room closed" in lowered:
        return WarmResult(ok=False, outcome="timeout", reason=message[:300])
    return WarmResult(ok=False, outcome="failed", reason=message[:300])


async def run_warm_transfer(request: WarmRequest, *, task_class: type[Any] | None = None) -> WarmResult:
    """Run the SDK's warm transfer from inside the ``transfer_call`` tool; never raises.

    Must be awaited inside a tool call (an ``AgentTask`` takes over the session
    until it completes). A ``ToolError`` from the task (nobody answered, the person
    declined, voicemail) or any other failure comes back as a result.
    """
    steps: list[str] = []
    cls = task_class or warm_transfer_task_class()
    try:
        task = cls(
            to=request.to,
            route=request.route,
            chat_ctx=request.chat_ctx,
            summary=request.summary,
            on_step=steps.append,
        )
        await task
    except Exception as exc:  # noqa: BLE001 - reported to the model as a result
        logger.info("warm transfer did not connect", steps=steps, error=str(exc)[:200])
        return _warm_outcome(exc)
    logger.info("warm transfer connected", steps=steps)
    return WarmResult(ok=True, outcome="connected")


# ------------------------------------------------------- answering-machine detection (V5-32)
def amd_config_for(resolved: ResolvedAgentConfig) -> AmdConfig | None:
    """The AMD settings of an outbound call that asked for them, else ``None``."""
    if resolved.channel != "sip_out":
        return None
    amd = resolved.config.telephony.amd
    return amd if amd.enabled else None


def amd_supported(session: Any) -> bool:
    """Whether the session's LLM can classify greetings (a cascaded pipeline's text LLM).

    livekit-agents 1.8.3 ``AMD`` with ``llm=None`` reuses the session's LLM and
    refuses anything that is not an ``llm.LLM`` (a realtime model), so AMD is
    skipped there with an event instead of failing the call.
    """
    from livekit.agents import llm as lk_llm  # noqa: PLC0415

    return isinstance(getattr(session, "llm", None), lk_llm.LLM)


def default_amd_factory(session: Any, *, participant_identity: str, ivr_detection: bool) -> Any:
    """livekit-agents 1.8.3 ``AMD`` on LKAP's own slots.

    ``llm=None`` / ``stt=None`` make it reuse the session's resolved LLM and
    transcripts: the default (``NOT_GIVEN``) would pick LiveKit Inference models on a
    Cloud connection, which is neither what the agent is configured with nor free.
    """
    from livekit.agents import AMD  # noqa: PLC0415

    kwargs: dict[str, Any] = {"participant_identity": participant_identity} if participant_identity else {}
    return AMD(
        session,
        llm=None,
        stt=None,
        ivr_detection=ivr_detection,
        suppress_compatibility_warning=True,
        **kwargs,
    )


class AmdRunner:
    """One answering-machine detection on an outbound call (V5-32).

    :meth:`start` enters the detector (the agent's speech is held from then on);
    :meth:`run` waits for the verdict, reports it on the call row, and acts on a
    machine: ``leave_message`` speaks the message once the greeting is over, then
    hangs up; ``hangup`` (and a mailbox that cannot take a message, and a phone
    menu without ``ivr_detection``) hangs up at once. A person or an unsure
    verdict lets the conversation go on (the held greeting plays).
    """

    def __init__(
        self,
        *,
        session: Any,
        config: AmdConfig,
        session_id: str,
        participant_identity: str,
        api: TelephonyApi,
        record_event: RecordEvent,
        hang_up: Callable[[str], Awaitable[None]],
        factory: Callable[..., Any] = default_amd_factory,
    ) -> None:
        """Create the runner; nothing listens until :meth:`start`."""
        self._session = session
        self._config = config
        self._session_id = session_id
        self._identity = participant_identity
        self._api = api
        self._record = record_event
        self._hang_up = hang_up
        self._factory = factory
        self._detector: Any = None
        self.verdict: str | None = None
        self.action: str | None = None

    async def start(self) -> None:
        """Enter the detector: it holds the agent's speech and waits for the answer itself."""
        self._detector = self._factory(
            self._session, participant_identity=self._identity, ivr_detection=self._config.ivr_detection
        )
        await self._detector.__aenter__()

    def action_for(self, verdict: str) -> str | None:
        """What the agent does for a verdict (``None`` = carry on with the conversation)."""
        if verdict not in AMD_MACHINE_RESULTS:
            return None
        if verdict == "machine-ivr":
            return "navigate" if self._config.ivr_detection else "hangup"
        if verdict == "machine-vm" and self._config.on_machine == "leave_message":
            return "leave_message"
        return "hangup"

    async def run(self) -> str | None:
        """Wait for the verdict and act on it; returns the verdict (``None`` if detection broke)."""
        if self._detector is None:
            return None
        try:
            result = await self._detector.execute()
        except Exception as exc:  # noqa: BLE001 - a detector failure never ends the call
            logger.warning("answering-machine detection gave no verdict", error=str(exc)[:200])
            await self.aclose()
            return None
        category = getattr(result, "category", "uncertain")
        verdict = str(getattr(category, "value", category))
        self.verdict = verdict
        await self._api.report_call(
            CallReportIn.model_validate(
                {
                    "session_id": self._session_id,
                    "status": "answered",
                    "direction": "outbound",
                    "participant_identity": self._identity,
                    "amd_result": verdict if verdict in _AMD_VALUES else "uncertain",
                }
            )
        )
        self.action = self.action_for(verdict)
        if self.action is None:
            await self.aclose()
            return verdict
        message_left = False
        if self.action == "leave_message":
            message_left = await self._leave_message()
        self._record(
            VOICEMAIL_EVENT,
            VoicemailEvent.model_validate(
                {"result": verdict, "action": self.action, "message_left": message_left}
            ).model_dump(mode="json"),
        )
        await self.aclose()
        if self.action != "navigate":
            await self._hang_up(f"answering machine ({verdict})")
        return verdict

    async def _leave_message(self) -> bool:
        text = (self._config.message or "").strip() or DEFAULT_VOICEMAIL_MESSAGE
        try:
            handle: Any = self._session.say(text, allow_interruptions=False)
            if inspect.isawaitable(handle):
                handle = await handle
            wait = getattr(handle, "wait_for_playout", None)
            if callable(wait):
                await asyncio.wait_for(wait(), _VOICEMAIL_PLAYOUT_TIMEOUT_S)
        except Exception:  # noqa: BLE001 - hang up anyway
            logger.warning("voicemail message could not be played", exc_info=True)
            return False
        return True

    async def aclose(self) -> None:
        """Leave the detector (resumes the agent's speech); idempotent."""
        detector, self._detector = self._detector, None
        if detector is None:
            return
        try:
            await detector.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001 - teardown
            logger.debug("amd close failed", exc_info=True)


_AMD_VALUES: frozenset[str] = frozenset(
    {"human", "machine-ivr", "machine-vm", "machine-unavailable", "uncertain"}
)


async def hang_up(ctx: Any, reason: str) -> None:
    """End a phone call from the worker: delete the room (the SIP leg hangs up), then end the job.

    ``shutdown`` alone would leave the leg in the room (``delete_room_on_close`` is off).
    """
    delete = getattr(ctx, "delete_room", None)
    if callable(delete):
        try:
            pending = delete()
            if inspect.isawaitable(pending):
                await asyncio.wait_for(pending, _HANGUP_TIMEOUT_S)
        except Exception:  # noqa: BLE001 - the job still ends
            logger.warning("could not delete the call's room", exc_info=True)
    ctx.shutdown(reason=reason[:200])
