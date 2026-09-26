"""Start a session's Egress recording (CONTRACTS-V2 §3.4, ARCHITECTURE-V2 D-V2-16).

`start_recording` is the function `routers/internal.py`'s
`POST /internal/v1/sessions/{id}/recording/start` handler lazily imports
(ask #23) — its signature, `(db, session, connection, factory) ->
RecordingStartOut`, is fixed by that call site, not by this package.

The recording is audio-only `RoomCompositeEgress` to OGG (CONTRACTS-V2 §"D-V2-16"
names "MP4/OGG"; OGG is the natural pick for an audio-only capture and needs no
video codec), uploaded straight from LiveKit's Egress service to the resolved
connection's S3-compatible storage (`recordings/storage.py`) — never through
this api process. The requested `filepath` is stored as `recording_object_key`
**at start**, before Egress finishes, because it is deterministic (we choose
it) and is exactly the storage key a later signed URL needs; finalisation
(`recordings/finalize.py`) only ever updates status and duration.
"""

from __future__ import annotations

from livekit.api import LiveKitAPI, TwirpError
from livekit.protocol.egress import (
    EgressInfo,
    EncodedFileOutput,
    EncodedFileType,
    RoomCompositeEgressRequest,
    S3Upload,
    StopEgressRequest,
)
from lkap_contracts.agent_config import AgentConfig
from lkap_contracts.api_models import RecordingStartOut
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.connections.clients import ConnectionClientFactory
from lkap_api.connections.service import capabilities_of
from lkap_api.costs.service import config_for_session
from lkap_api.db.models import Agent, LiveKitConnection
from lkap_api.db.models import Session as SessionRow
from lkap_api.deps import get_vault
from lkap_api.errors import ConflictError, NotFoundError, UnprocessableEntityError
from lkap_api.logging import get_logger
from lkap_api.recordings.consent import ensure_recording_consent
from lkap_api.recordings.storage import resolve_egress_target
from lkap_api.settings import get_settings

log = get_logger(__name__)

#: `Egress` uploads run for up to a few hours; the *start* call itself should
#: answer quickly (it only creates the request, it does not wait for the room).
_START_TIMEOUT_S = 10.0


class EgressNotEnabledError(UnprocessableEntityError):
    """422: the session's connection has not probed `egress_enabled=true`."""


#: `recording_error` of a recording stopped because the caller withdrew consent (S5-5);
#: `finalize.apply_egress_result` keeps it once the stopped file is ready.
RECORDING_STOPPED_NOTE = "Stopped early: the caller withdrew consent"
#: The stop call itself answers quickly (it only asks Egress to end the file).
_STOP_TIMEOUT_S = 10.0


class RecordingStopOut(BaseModel):
    """``POST /internal/v1/sessions/{id}/recording/stop``."""

    stopped: bool
    """Whether this call asked Egress to stop (``False``: nothing running, or already stopped)."""
    egress_id: str | None = None


def _egress_object_key(session_id: str) -> str:
    return f"recordings/{session_id}.ogg"


async def start_recording(
    db: AsyncSession, session: SessionRow, connection: LiveKitConnection, factory: ConnectionClientFactory
) -> RecordingStartOut:
    """Start (or return the already-started) Egress recording for `session`.

    Raises:
        NotFoundError: If the session's agent no longer exists.
        EgressNotEnabledError: If the connection's last probe reported
            `egress_enabled=false` (a self-hosted deployment without the
            Egress service, or a Cloud project that has never been tested).
        ConflictError: If `AgentConfig.recording.enabled` is false — the
            worker should not have called this, but the check stays server-side.
        recordings.consent.RecordingConsentMissingError: (409) If the agent
            records only after consent (`recording.require_consent`) and the
            session's latest `recording` consent is not an acceptance (V5-15).
        recordings.storage.NoEgressStorageError: No S3-compatible storage
            config is reachable (see that module).
    """
    if session.recording_egress_id is not None:
        # The worker retried `recording/start` (e.g. after a transient network
        # error on its side); the request already went out once. `is not
        # None` rather than truthiness: an empty string is still a real,
        # already-issued id, not "never started" (`recording_egress_id`
        # itself, not `recording_status`, is the source of truth here).
        return RecordingStartOut(egress_id=session.recording_egress_id)

    agent = (
        await db.execute(
            select(Agent).where(Agent.id == session.agent_id, Agent.workspace_id == session.workspace_id)
        )
    ).scalar_one_or_none()
    if agent is None:
        raise NotFoundError(f"unknown agent '{session.agent_id}'")
    # S5-24: the config the session runs (its pinned version), so turning consent off in a
    # later save never lifts the gate of a live session.
    config = await config_for_session(db, session) or AgentConfig.model_validate(agent.config)
    if not config.recording.enabled:
        raise ConflictError(f"session '{session.id}' has no recording configured")
    # V5-15: an agent that records only after consent needs an accepted `recording` answer.
    ensure_recording_consent(session, config)
    if not capabilities_of(connection).egress_enabled:
        raise EgressNotEnabledError(
            f"connection '{connection.id}' has not verified Egress support (test the connection first)",
            details={"connection_id": connection.id},
        )

    settings = get_settings()
    vault = get_vault(settings)
    target = await resolve_egress_target(
        db,
        vault,
        workspace_id=connection.workspace_id,
        storage_config_id=config.recording.storage_config_id,
        connection=connection,
    )

    # `session.recording_object_key` is stored **without** the storage config's
    # `prefix` (`storage.s3.S3Storage._full_key` applies it on every `put`/`get`/
    # `signed_url` call); the Egress `filepath` sent to LiveKit — which uploads
    # straight to S3, bypassing that abstraction entirely — gets the prefix
    # applied here instead, so a later `S3Storage(prefix=...).signed_url(key)`
    # reconstructs the exact key Egress actually wrote to.
    object_key = _egress_object_key(session.id)
    filepath = f"{target.prefix}/{object_key}" if target.prefix else object_key
    request = RoomCompositeEgressRequest(
        room_name=session.room_name,
        audio_only=config.recording.audio_only,
        file_outputs=[
            EncodedFileOutput(
                file_type=EncodedFileType.OGG,
                filepath=filepath,
                s3=S3Upload(
                    access_key=target.access_key,
                    secret=target.secret_key,
                    bucket=target.bucket,
                    region=target.region or "",
                    endpoint=target.endpoint_url or "",
                    force_path_style=bool(target.endpoint_url),
                ),
            )
        ],
    )

    async with factory.api(connection, timeout_s=_START_TIMEOUT_S) as client:
        info = await _start(client, request)

    session.recording_status = "active"
    session.recording_egress_id = info.egress_id
    session.recording_object_key = object_key
    await db.flush()
    log.info(
        "recording_started",
        session_id=session.id,
        connection_id=connection.id,
        egress_id=info.egress_id,
        storage_config_id=target.storage_config_id,
    )
    return RecordingStartOut(egress_id=info.egress_id)


async def _start(client: LiveKitAPI, request: RoomCompositeEgressRequest) -> EgressInfo:
    return await client.egress.start_room_composite_egress(request)


async def stop_recording(
    db: AsyncSession, session: SessionRow, connection: LiveKitConnection, factory: ConnectionClientFactory
) -> RecordingStopOut:
    """Stop the session's running Egress, once (S5-5: the caller withdrew recording consent).

    Idempotent: a session with no Egress, one already finished, or one already stopped by
    this route answers ``stopped=false`` without calling LiveKit. The existing
    ``egress_ended`` webhook finalises the (shorter) file as usual.
    """
    egress_id = session.recording_egress_id
    if (
        egress_id is None  # `is None`: an empty string is a real, already-issued id (see start)
        or session.recording_status in ("ready", "failed")
        or session.recording_error == RECORDING_STOPPED_NOTE
    ):
        return RecordingStopOut(stopped=False, egress_id=egress_id)
    try:
        async with factory.api(connection, timeout_s=_STOP_TIMEOUT_S) as client:
            await client.egress.stop_egress(StopEgressRequest(egress_id=egress_id))
    except TwirpError as exc:
        # An Egress that already ended (or never ran) cannot be stopped: nothing is recording.
        log.info("recording_stop_refused", session_id=session.id, code=getattr(exc, "code", None))
    session.recording_error = RECORDING_STOPPED_NOTE
    await db.flush()
    log.info("recording_stopped", session_id=session.id, egress_id=egress_id, reason="consent_withdrawn")
    return RecordingStopOut(stopped=True, egress_id=egress_id)
