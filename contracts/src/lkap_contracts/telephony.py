"""Telephony value patterns and an agent's transfer destinations (ruling R-V2-21).

* :data:`E164_PATTERN`, :data:`TRANSFER_TARGET_PATTERN`, :data:`DTMF_PATTERN` —
  the one definition the api's request models, the worker's tools and the
  console's inputs all mirror.
* :class:`TransferTarget` / :class:`TelephonyConfig` — ``AgentConfig.telephony``:
  the only destinations the ``transfer_call`` tool may hand the caller to. The
  model never dials free text, and every ``to`` must also pass the workspace's
  outbound dialing policy (R-V2-23) when the agent is saved; that check needs
  the database and lives in the api (``lkap_api.telephony.validation``), as does
  the case-insensitive uniqueness of labels (reported per entry).

* :class:`SmsTarget` — ``TelephonyConfig.sms_targets`` (V5-25): the only numbers
  ``send_sms`` may text besides the caller of a phone call; the model names them
  by label, never by number.

* :class:`AmdConfig` — ``TelephonyConfig.amd`` (V5-32): answering-machine
  detection on outbound calls, and what the agent does when a machine answers.
* ``TransferTarget.mode`` (V5-32, D-V5-21): ``warm`` = the agent calls the
  person first, briefs them privately while the caller hears hold music, then
  joins them to the call. Warm needs a LiveKit Cloud connection; anywhere else
  (and whenever the warm route is not available) the worker transfers cold and
  records the summary on the call row.
* :class:`WarmTransferRoute` — what the api hands the worker so it can place the
  private consult call itself (``ResolvedAgentConfig.warm_transfer``).

Imports nothing from the other contract modules (``agent_config`` imports this one).
"""

from typing import Literal

from pydantic import BaseModel, Field

__all__ = [
    "AMD_MACHINE_RESULTS",
    "AMD_RESULTS",
    "DTMF_PATTERN",
    "E164_PATTERN",
    "MAX_AMD_MESSAGE_CHARS",
    "MAX_TRANSFER_SUMMARY_CHARS",
    "TRANSFER_TARGET_PATTERN",
    "AmdConfig",
    "AmdOnMachine",
    "AmdResult",
    "SmsTarget",
    "TelephonyConfig",
    "TransferMode",
    "TransferTarget",
    "WarmTransferRoute",
]

#: E.164: a ``+``, a non-zero country digit, 7-15 digits in total.
E164_PATTERN = r"^\+[1-9]\d{6,14}$"

#: A transfer destination: an E.164 number, or a ``tel:`` / ``sip:`` / ``sips:`` URI.
TRANSFER_TARGET_PATTERN = r"^(\+[1-9]\d{6,14}|tel:\+?[0-9]{3,20}|sips?:[^\s@]+@[^\s]+)$"

#: DTMF digits LiveKit can publish (RFC 4733 events 0-15), at most 32.
DTMF_PATTERN = r"^[0-9*#A-D]{1,32}$"


#: How a caller is handed to a person (V5-32): ``cold`` = a SIP REFER, the agent leaves at once;
#: ``warm`` = the agent briefs the person on a private call first (LiveKit Cloud only, D-V5-21).
TransferMode = Literal["cold", "warm"]

#: What answered an outbound call (the categories of ``livekit.agents.AMDCategory`` in 1.8.3):
#: a person, a phone menu, a voicemail greeting, a mailbox that cannot take a message, or not sure.
AmdResult = Literal["human", "machine-ivr", "machine-vm", "machine-unavailable", "uncertain"]

#: Every :data:`AmdResult`, in the order above.
AMD_RESULTS: tuple[str, ...] = ("human", "machine-ivr", "machine-vm", "machine-unavailable", "uncertain")

#: The results that mean a machine answered (the ``call.voicemail`` webhook fires for these).
AMD_MACHINE_RESULTS: frozenset[str] = frozenset({"machine-ivr", "machine-vm", "machine-unavailable"})

#: What the agent does when a machine answers an outbound call.
AmdOnMachine = Literal["hangup", "leave_message"]

#: Longest voicemail message the agent reads out.
MAX_AMD_MESSAGE_CHARS = 1000

#: Longest transfer summary kept on a call row.
MAX_TRANSFER_SUMMARY_CHARS = 2000


class TransferTarget(BaseModel):
    """One destination the agent may transfer a caller to."""

    label: str = Field(min_length=1, max_length=64, description="What the model and the caller call it")
    to: str = Field(
        max_length=256,
        pattern=TRANSFER_TARGET_PATTERN,
        description="E.164 number or tel:/sip:/sips: URI",
    )
    mode: TransferMode = Field(
        default="cold",
        description=(
            "cold: hand the call over at once. warm: the agent calls the person first and briefs them "
            "while the caller is on hold, then joins them (LiveKit Cloud only; elsewhere the transfer "
            "is cold and the summary is kept on the call)."
        ),
    )


class AmdConfig(BaseModel):
    """Answering-machine detection on outbound calls (``TelephonyConfig.amd``, V5-32).

    When ``enabled``, the worker listens to how an outbound call is answered and
    classifies it with the agent's own speech-to-text and language model before
    the agent speaks. A person (or an unsure result) → the conversation goes on as
    usual. A voicemail greeting → ``on_machine``: ``leave_message`` speaks
    ``message`` after the greeting and hangs up; ``hangup`` hangs up at once. A
    mailbox that cannot take a message is always hung up. A phone menu is
    navigated by the agent when ``ivr_detection`` is on, else treated as a machine.
    Inbound calls and web sessions ignore this setting.
    """

    enabled: bool = False
    on_machine: AmdOnMachine = "hangup"
    message: str | None = Field(
        default=None,
        max_length=MAX_AMD_MESSAGE_CHARS,
        description="What the agent says to a voicemail (leave_message). Empty: a short call-back request.",
    )
    ivr_detection: bool = Field(
        default=False, description="Let the agent navigate a phone menu instead of hanging up on it."
    )


class SmsTarget(BaseModel):
    """One number ``send_sms`` may text, named by its label (V5-25)."""

    label: str = Field(min_length=1, max_length=64, description="What the model and the caller call it")
    to: str = Field(max_length=16, pattern=E164_PATTERN, description="E.164 mobile number")


class TelephonyConfig(BaseModel):
    """Phone-call settings of an agent (``AgentConfig.telephony``)."""

    transfer_targets: list[TransferTarget] = Field(default_factory=list, max_length=50)
    sms_targets: list[SmsTarget] = Field(
        default_factory=list,
        max_length=50,
        description="Numbers the agent may text by label, besides the caller of a phone call (send_sms).",
    )
    amd: AmdConfig = Field(
        default_factory=AmdConfig,
        description="Answering-machine detection on outbound calls (V5-32).",
    )


class WarmTransferRoute(BaseModel):
    """What the worker needs to place a warm transfer's private consult call (V5-32).

    Filled by the api in ``/internal/v1/sessions/{id}/resolved`` only when the
    session runs on a LiveKit Cloud connection with exactly one synced outbound
    trunk and at least one ``warm`` target passes the workspace's dialing policy
    today (R-V2-23: the worker dials these itself, so the api vets them here).
    ``None`` on the resolved document = every transfer is cold.
    """

    trunk_id: str = Field(min_length=1, max_length=128, description="The LiveKit outbound trunk id")
    caller_id: str = Field(default="", max_length=32, description="The number the person sees")
    targets: list[str] = Field(
        default_factory=list,
        max_length=50,
        description="The warm targets' ``to`` values that the dialing policy allows",
    )
