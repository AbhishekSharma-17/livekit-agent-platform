"""Dispatch metadata: the ID-only payload carried in ``RoomAgentDispatch.metadata``."""

from typing import Literal

from pydantic import BaseModel

from lkap_contracts.common import SessionChannel


class DispatchMetadata(BaseModel):
    """Serialised as JSON into ``RoomAgentDispatch.metadata``.

    IDs ONLY. The browser can read this, so it must never contain secrets,
    instructions or resolved provider configuration.

    Rooms the platform did not create (inbound SIP) have no session row yet, so
    v2 adds ``channel`` and ``connection_id`` and makes ``session_id`` optional:
    the worker (V2-07) calls ``POST /internal/v1/sessions/start`` when
    ``session_id`` is ``None`` **or empty** (the v1-era "no session yet"
    spelling is still honoured), and ``GET /internal/v1/sessions/{id}/resolved``
    otherwise.
    """

    v: Literal[2] = 2
    session_id: str | None = None
    agent_id: str
    config_version: int
    participant_identity: str
    channel: SessionChannel = "web"
    connection_id: str = ""
