"""Dispatch metadata: the ID-only payload carried in ``RoomAgentDispatch.metadata``."""

from typing import Literal

from pydantic import BaseModel


class DispatchMetadata(BaseModel):
    """Serialised as JSON into ``RoomAgentDispatch.metadata``.

    IDs ONLY. The browser can read this, so it must never contain secrets,
    instructions or resolved provider configuration.
    """

    v: Literal[1] = 1
    session_id: str
    agent_id: str
    config_version: int
    participant_identity: str
