"""LiveKit token minting: grants, dispatch metadata and secret hygiene."""

from __future__ import annotations

import json
from typing import Any

import jwt
import pytest
from lkap_contracts.dispatch import DispatchMetadata

from lkap_api.livekit_tokens import (
    TOKEN_TTL,
    mint_participant_token,
    new_participant_identity,
    room_name_for,
)
from lkap_api.settings import Settings

DISPATCH = DispatchMetadata(
    session_id="1234567890abcdef",
    agent_id="agent-1",
    config_version=7,
    participant_identity="user-abcd1234",
)


def _mint(**overrides: Any) -> str:
    kwargs: dict[str, Any] = {
        "api_key": "APItestkey",
        "api_secret": "test-secret-value-long-enough-for-hs256",
        "agent_name": "lkap-agent",
        "room_name": "lkap-12345678",
        "identity": "user-abcd1234",
        "participant_name": "Guest",
        "dispatch": DISPATCH,
    }
    kwargs.update(overrides)
    return mint_participant_token(**kwargs)


def _claims(token: str, secret: str = "test-secret-value-long-enough-for-hs256") -> dict[str, Any]:
    decoded: dict[str, Any] = jwt.decode(token, secret, algorithms=["HS256"], issuer="APItestkey")
    return decoded


def test_room_name_is_derived_from_the_session_id() -> None:
    assert room_name_for("1234567890abcdef") == "lkap-12345678"


def test_generated_identities_are_unique() -> None:
    assert new_participant_identity() != new_participant_identity()


def test_token_grants_only_what_a_participant_needs() -> None:
    claims = _claims(_mint())

    video = claims["video"]
    assert video["roomJoin"] is True
    assert video["room"] == "lkap-12345678"
    assert video["canPublish"] is True
    assert video["canSubscribe"] is True
    assert video["canPublishData"] is True
    assert "roomCreate" not in video
    assert "agent" not in video
    assert claims["sub"] == "user-abcd1234"


def test_token_dispatches_exactly_one_agent_with_id_only_metadata() -> None:
    claims = _claims(_mint())

    agents = claims["roomConfig"]["agents"]
    assert len(agents) == 1
    assert agents[0]["agentName"] == "lkap-agent"

    metadata = json.loads(agents[0]["metadata"])
    assert set(metadata) == set(DispatchMetadata.model_fields)
    assert DispatchMetadata.model_validate(metadata) == DISPATCH


def test_token_carries_no_secret_or_instruction_material() -> None:
    raw = json.dumps(_claims(_mint()))

    for forbidden in (
        "api_key",
        "sk-",
        "instructions",
        "credential_id",
        "test-secret-value-long-enough-for-hs256",
    ):
        assert forbidden not in raw


def test_token_expires_two_hours_out() -> None:
    claims = _claims(_mint())

    assert claims["exp"] - claims["nbf"] == pytest.approx(TOKEN_TTL.total_seconds(), abs=2)


def test_participant_attributes_are_attached_when_present() -> None:
    claims = _claims(_mint(attributes={"tier": "gold"}))

    assert claims["attributes"] == {"tier": "gold"}


def test_settings_agent_name_defaults_to_the_platform_agent(settings: Settings) -> None:
    assert settings.agent_name == "lkap-agent"
