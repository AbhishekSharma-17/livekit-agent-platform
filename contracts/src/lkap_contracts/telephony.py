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

Imports nothing from the other contract modules (``agent_config`` imports this one).
"""

from pydantic import BaseModel, Field

__all__ = [
    "DTMF_PATTERN",
    "E164_PATTERN",
    "TRANSFER_TARGET_PATTERN",
    "TelephonyConfig",
    "TransferTarget",
]

#: E.164: a ``+``, a non-zero country digit, 7-15 digits in total.
E164_PATTERN = r"^\+[1-9]\d{6,14}$"

#: A transfer destination: an E.164 number, or a ``tel:`` / ``sip:`` / ``sips:`` URI.
TRANSFER_TARGET_PATTERN = r"^(\+[1-9]\d{6,14}|tel:\+?[0-9]{3,20}|sips?:[^\s@]+@[^\s]+)$"

#: DTMF digits LiveKit can publish (RFC 4733 events 0-15), at most 32.
DTMF_PATTERN = r"^[0-9*#A-D]{1,32}$"


class TransferTarget(BaseModel):
    """One destination the agent may transfer a caller to."""

    label: str = Field(min_length=1, max_length=64, description="What the model and the caller call it")
    to: str = Field(
        max_length=256,
        pattern=TRANSFER_TARGET_PATTERN,
        description="E.164 number or tel:/sip:/sips: URI",
    )


class TelephonyConfig(BaseModel):
    """Phone-call settings of an agent (``AgentConfig.telephony``)."""

    transfer_targets: list[TransferTarget] = Field(default_factory=list, max_length=50)
