"""Dispatch metadata: the ID-only payload carried in ``RoomAgentDispatch.metadata``."""

from typing import Literal

from pydantic import BaseModel

from lkap_contracts.common import SessionChannel


class DispatchMetadata(BaseModel):
    """Serialised as JSON into ``RoomAgentDispatch.metadata``.

    IDs ONLY. The browser can read this, so it must never contain secrets,
    instructions or resolved provider configuration.

    Rooms the platform did not create (inbound SIP) have no session row yet, so
    v2 adds ``channel`` and ``connection_id`` and the worker calls
    ``POST /internal/v1/sessions/start`` when ``session_id`` is empty. The field
    stays a ``str`` (empty means "no session yet") rather than ``str | None``
    while the v1 worker still passes it straight to ``resolve()``; V2-07 widens
    it once the worker branches on it.
    """

    v: Literal[2] = 2
    session_id: str
    agent_id: str
    config_version: int
    participant_identity: str
    channel: SessionChannel = "web"
    connection_id: str = ""
