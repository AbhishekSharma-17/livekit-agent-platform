"""Worker → api client for the `/internal/v1` surface (docs/CONTRACTS.md §7).

The worker never reads the database or the credential vault. It fetches one
`ResolvedAgentConfig` per job with the static service token — through
`GET /internal/v1/sessions/{id}/resolved` when the dispatch named a session, or
by creating the session with `POST /internal/v1/sessions/start` when it did not
(server-created rooms such as inbound SIP, CONTRACTS-V2 D-V2-5) — posts session
events, asks the api to start an Egress recording, and pushes latency metrics
and a final summary from its shutdown callback.

Nothing in this module logs a response body: `ResolvedAgentConfig` carries
decrypted vendor keys and substituted tool secrets.
"""

from __future__ import annotations

from types import TracebackType
from typing import TYPE_CHECKING, Protocol, Self

import httpx
from lkap_contracts.agent_config import ResolvedAgentConfig
from lkap_contracts.api_models import (
    InternalKbSearchRequest,
    KbHit,
    KbSearchResponse,
    RecordingStartOut,
    SessionEventIn,
    SessionEventsIn,
    SessionMetricsIn,
    SessionRecordingIn,
    SessionStartIn,
    SessionSummaryIn,
)

from lkap_agent.logging import get_logger

if TYPE_CHECKING:
    from lkap_agent.qa import SessionQaIn

__all__ = [
    "ApiKbClient",
    "ConfigClient",
    "ConfigClientProtocol",
    "ConfigUnavailableError",
    "RecordingUnavailableError",
    "SessionEndedError",
    "SessionNotFoundError",
]

logger = get_logger(__name__)

_SERVICE_TOKEN_HEADER = "X-Service-Token"


class ConfigUnavailableError(RuntimeError):
    """The resolved config could not be fetched; the job must fail cleanly."""


class SessionNotFoundError(ConfigUnavailableError):
    """The api does not know this `session_id` (404) — a stale or forged dispatch."""


class SessionEndedError(ConfigUnavailableError):
    """The session row is already `ended` (409) — a replayed dispatch.

    `sessions/start` raises it too when the room already has a session: a
    second job for a room the platform already knows is a duplicate dispatch.
    """


class RecordingUnavailableError(RuntimeError):
    """The api could not start an Egress recording (not installed, or it failed)."""


class ConfigClientProtocol(Protocol):
    """The subset of the api the worker depends on, so tests can supply a fake."""

    async def resolve(self, session_id: str) -> ResolvedAgentConfig:
        """Fetch the resolved config, marking the session active."""
        ...

    async def start_session(self, request: SessionStartIn) -> ResolvedAgentConfig:
        """Create the session row for a room the platform did not create, and resolve it."""
        ...

    async def start_recording(self, session_id: str) -> str:
        """Ask the api to start the session's Egress recording; returns the egress id."""
        ...

    async def post_recording(self, session_id: str, recording: SessionRecordingIn) -> None:
        """Report the recording's state from the worker (best effort)."""
        ...

    async def post_metrics(self, session_id: str, metrics: SessionMetricsIn) -> None:
        """Post per-session latency (best effort)."""
        ...

    async def put_qa(self, session_id: str, qa: SessionQaIn) -> None:
        """Store the worker-side QA verdict (best effort, R-V2-5)."""
        ...

    async def post_events(self, session_id: str, events: list[SessionEventIn]) -> None:
        """Append session events (best effort: failures are logged, never raised)."""
        ...

    async def put_summary(self, session_id: str, summary: SessionSummaryIn) -> None:
        """Store the final usage, transcript and UI state."""
        ...

    async def kb_search(self, kb_ids: list[str], query: str, k: int = 4) -> list[KbHit]:
        """Search the agent's knowledge bases through the api."""
        ...

    async def aclose(self) -> None:
        """Release the underlying HTTP connection pool."""
        ...


class ConfigClient:
    """`httpx`-backed implementation of :class:`ConfigClientProtocol`."""

    def __init__(
        self,
        base_url: str,
        service_token: str,
        *,
        timeout_s: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create a client for the api's internal surface.

        Args:
            base_url: e.g. `http://127.0.0.1:8080`.
            service_token: The static `LKAP_SERVICE_TOKEN`.
            timeout_s: Per-request timeout.
            client: An existing client to use (tests pass a `respx`-mounted one).
        """
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout_s,
            headers={_SERVICE_TOKEN_HEADER: service_token},
        )
        if client is not None:
            self._client.headers[_SERVICE_TOKEN_HEADER] = service_token

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the HTTP pool if this client owns it."""
        if self._owns_client:
            await self._client.aclose()

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    async def resolve(self, session_id: str) -> ResolvedAgentConfig:
        """Fetch `GET /internal/v1/sessions/{id}/resolved`.

        Args:
            session_id: The id carried in the dispatch metadata.

        Returns:
            The validated `ResolvedAgentConfig`. Never log the return value.

        Raises:
            SessionNotFoundError: The api returned 404.
            SessionEndedError: The api returned 409.
            ConfigUnavailableError: Any transport error, other HTTP error status,
                or a payload that fails `ResolvedAgentConfig` validation.
        """
        url = self._url(f"/internal/v1/sessions/{session_id}/resolved")
        try:
            response = await self._client.get(url)
        except httpx.HTTPError as exc:
            raise ConfigUnavailableError(f"api unreachable at {url}: {exc}") from exc

        if response.status_code == httpx.codes.NOT_FOUND:
            raise SessionNotFoundError(f"unknown session {session_id!r}")
        if response.status_code == httpx.codes.CONFLICT:
            raise SessionEndedError(f"session {session_id!r} is already ended")
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise ConfigUnavailableError(
                f"resolve failed for session {session_id!r}: HTTP {response.status_code}"
            )
        try:
            return ResolvedAgentConfig.model_validate_json(response.content)
        except ValueError as exc:
            # Message only: the body holds decrypted credentials.
            raise ConfigUnavailableError(
                f"resolved config for session {session_id!r} failed validation"
            ) from exc

    async def start_session(self, request: SessionStartIn) -> ResolvedAgentConfig:
        """Post `POST /internal/v1/sessions/start` for a dispatch without a session.

        Args:
            request: The agent, room, channel and caller the dispatch carried.

        Returns:
            The validated `ResolvedAgentConfig` of the new session. Never log it.

        Raises:
            SessionNotFoundError: The api returned 404 (unknown agent).
            SessionEndedError: The api returned 409 (the room already has a
                session, or the agent is archived).
            ConfigUnavailableError: Any transport error, other HTTP error status,
                or a payload that fails `ResolvedAgentConfig` validation.
        """
        url = self._url("/internal/v1/sessions/start")
        try:
            response = await self._client.post(
                url, content=request.model_dump_json(), headers={"content-type": "application/json"}
            )
        except httpx.HTTPError as exc:
            raise ConfigUnavailableError(f"api unreachable at {url}: {exc}") from exc

        if response.status_code == httpx.codes.NOT_FOUND:
            raise SessionNotFoundError(f"unknown agent {request.agent_id!r}")
        if response.status_code == httpx.codes.CONFLICT:
            raise SessionEndedError(
                f"room {request.room_name!r} already has a session or agent {request.agent_id!r} is archived"
            )
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise ConfigUnavailableError(
                f"sessions/start failed for room {request.room_name!r}: HTTP {response.status_code}"
            )
        try:
            return ResolvedAgentConfig.model_validate_json(response.content)
        except ValueError as exc:
            # Message only: the body holds decrypted credentials.
            raise ConfigUnavailableError(
                f"resolved config for room {request.room_name!r} failed validation"
            ) from exc

    async def start_recording(self, session_id: str) -> str:
        """Post `POST /internal/v1/sessions/{id}/recording/start`.

        Returns:
            The Egress id the api started.

        Raises:
            RecordingUnavailableError: On any failure, including the 501 the api
                answers until its recordings package is installed.
        """
        url = self._url(f"/internal/v1/sessions/{session_id}/recording/start")
        try:
            response = await self._client.post(url)
        except httpx.HTTPError as exc:
            raise RecordingUnavailableError(f"api unreachable at {url}: {exc}") from exc
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise RecordingUnavailableError(f"recording/start answered HTTP {response.status_code}")
        try:
            return RecordingStartOut.model_validate_json(response.content).egress_id
        except ValueError as exc:
            raise RecordingUnavailableError("recording/start returned an unparseable payload") from exc

    async def post_recording(self, session_id: str, recording: SessionRecordingIn) -> None:
        """Post `POST /internal/v1/sessions/{id}/recording`, swallowing failures."""
        await self._post_best_effort(
            f"/internal/v1/sessions/{session_id}/recording", recording.model_dump_json(), what="recording"
        )

    async def post_metrics(self, session_id: str, metrics: SessionMetricsIn) -> None:
        """Post `POST /internal/v1/sessions/{id}/metrics`, swallowing failures."""
        await self._post_best_effort(
            f"/internal/v1/sessions/{session_id}/metrics", metrics.model_dump_json(), what="metrics"
        )

    async def put_qa(self, session_id: str, qa: SessionQaIn) -> None:
        """Put `PUT /internal/v1/sessions/{id}/qa`, swallowing failures."""
        await self._post_best_effort(
            f"/internal/v1/sessions/{session_id}/qa", qa.model_dump_json(), what="qa", method="PUT"
        )

    async def _post_best_effort(self, path: str, body: str, *, what: str, method: str = "POST") -> None:
        try:
            response = await self._client.request(
                method, self._url(path), content=body, headers={"content-type": "application/json"}
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning(f"failed to post session {what}", path=path, error=str(exc))

    async def post_events(self, session_id: str, events: list[SessionEventIn]) -> None:
        """Post `POST /internal/v1/sessions/{id}/events`, swallowing failures.

        Event delivery is observability, not correctness: a failing api must
        never take down a live conversation.
        """
        if not events:
            return
        payload = SessionEventsIn(events=events)
        try:
            response = await self._client.post(
                self._url(f"/internal/v1/sessions/{session_id}/events"),
                content=payload.model_dump_json(),
                headers={"content-type": "application/json"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("failed to post session events", session_id=session_id, error=str(exc))

    async def put_summary(self, session_id: str, summary: SessionSummaryIn) -> None:
        """Put `PUT /internal/v1/sessions/{id}/summary`, swallowing failures."""
        try:
            response = await self._client.put(
                self._url(f"/internal/v1/sessions/{session_id}/summary"),
                content=summary.model_dump_json(),
                headers={"content-type": "application/json"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("failed to put session summary", session_id=session_id, error=str(exc))

    async def kb_search(self, kb_ids: list[str], query: str, k: int = 4) -> list[KbHit]:
        """Post `POST /internal/v1/kb/search`.

        Returns:
            The hits, best first. An empty list when the search fails — retrieval
            is an enhancement, not a precondition for answering.
        """
        if not kb_ids or not query.strip():
            return []
        request = InternalKbSearchRequest(kb_ids=kb_ids, query=query, k=k)
        try:
            response = await self._client.post(
                self._url("/internal/v1/kb/search"),
                content=request.model_dump_json(),
                headers={"content-type": "application/json"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("kb search failed", kb_ids=kb_ids, error=str(exc))
            return []
        try:
            return KbSearchResponse.model_validate_json(response.content).hits
        except ValueError:
            logger.warning("kb search returned an unparseable payload", kb_ids=kb_ids)
            return []


class ApiKbClient:
    """`packs.base.KbClient` over :class:`ConfigClientProtocol`.

    Binds the agent's configured `kb_ids` so pack and built-in tools can call
    `ctx.kb.search("...")` without knowing them. `k` is passed through
    unchanged (DECISIONS-W2 D-W2-5): an explicit `k` wins, otherwise the
    contract default 4; platform callers pass `config.knowledge.top_k`.
    """

    def __init__(self, client: ConfigClientProtocol, kb_ids: list[str]) -> None:
        self._client = client
        self._kb_ids = list(kb_ids)

    async def search(self, query: str, k: int = 4, kb_ids: list[str] | None = None) -> list[KbHit]:
        """Search the agent's knowledge bases (or `kb_ids` when given) for `k` hits."""
        targets = kb_ids if kb_ids is not None else self._kb_ids
        return await self._client.kb_search(targets, query, k)
