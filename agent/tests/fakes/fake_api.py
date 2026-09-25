"""An in-memory stand-in for the api's `/internal/v1` surface.

`FakeApi` satisfies `lkap_agent.config_client.ConfigClientProtocol`, so
`run_session` can be driven end to end with no HTTP at all. It records
everything the worker sends, which is what the observability tests assert on.

`resolved_config(...)` builds a valid `ResolvedAgentConfig` for the default
cascaded LiveKit Inference pipeline, with knobs for the cases the suite needs
(realtime, no TTS, avatar, tools, knowledge bases).
"""

from __future__ import annotations

from typing import Any

from lkap_contracts.agent_config import (
    AgentConfig,
    CapabilitiesConfig,
    KnowledgeConfig,
    PipelineConfig,
    PipelineMode,
    ProviderRef,
    ProviderSlot,
    ResolvedAgentConfig,
    ResolvedProvider,
    ToolsConfig,
    VoiceConfig,
)
from lkap_contracts.api_models import (
    CallReportIn,
    InternalTransferOut,
    KbHit,
    KbSearchOptions,
    SessionEventIn,
    SessionMetricsIn,
    SessionRecordingIn,
    SessionStartIn,
    SessionSummaryIn,
)

__all__ = ["FakeApi", "resolved_config"]


def _provider(provider_id: str, python_class: str, model: str | None, **kwargs: Any) -> ResolvedProvider:
    return ResolvedProvider(
        provider_id=provider_id, python_class=python_class, model=model, kwargs=dict(kwargs)
    )


def resolved_config(
    *,
    session_id: str = "sess-1",
    agent_id: str = "agent-1",
    mode: PipelineMode = "cascaded",
    with_tts: bool = True,
    with_avatar: bool = False,
    instructions: str = "You are a helpful test agent.",
    greeting: str = "Hello there!",
    greeting_mode: str = "say",
    pack_id: str = "generic",
    kb_ids: list[str] | None = None,
    auto_inject: bool = True,
    camera: bool = False,
    tools: list[Any] | None = None,
    participant_identity: str = "user-guest",
    llm_model: str | None = None,
    chat_input: bool = True,
    channel: str = "web",
) -> ResolvedAgentConfig:
    """Build a valid `ResolvedAgentConfig` for tests.

    Providers point at the real registry ids so `ProviderFactory` accepts them,
    but a test that does not want live constructors injects its own factory.
    `llm_model=None` leaves the cascaded LLM on the provider default (gemma,
    text-only per the registry); vision tests pass `"google/gemini-3.5-flash"`.
    """
    resolved: dict[ProviderSlot, ResolvedProvider] = {}
    pipeline_kwargs: dict[str, Any] = {"mode": mode}

    if mode in ("realtime", "half_cascade"):
        resolved["realtime"] = _provider(
            "google-realtime",
            "livekit.plugins.google.realtime.RealtimeModel",
            "gemini-3.8-live",
            api_key="test-google-key",
            voice="Kore",
        )
        pipeline_kwargs["realtime"] = ProviderRef(
            provider_id="google-realtime", credential_id="cred-1", model="gemini-3.8-live"
        )
    else:
        resolved["stt"] = _provider(
            "livekit-inference-stt", "livekit.agents.inference.STT", "deepgram/nova-3", language="en"
        )
        resolved["llm"] = _provider(
            "livekit-inference-llm",
            "livekit.agents.inference.LLM",
            llm_model or "google/gemma-4-31b-it",
            temperature=0.7,
        )
        pipeline_kwargs["stt"] = ProviderRef(provider_id="livekit-inference-stt")
        pipeline_kwargs["llm"] = ProviderRef(provider_id="livekit-inference-llm", model=llm_model)

    if with_tts:
        resolved["tts"] = _provider(
            "livekit-inference-tts",
            "livekit.agents.inference.TTS",
            "inworld/inworld-tts-2",
            voice="Ashley",
        )
        pipeline_kwargs["tts"] = ProviderRef(provider_id="livekit-inference-tts")

    if with_avatar:
        resolved["avatar"] = _provider(
            "bey-avatar",
            "livekit.plugins.bey.AvatarSession",
            None,
            api_key="test-bey-key",
            avatar_id="avatar-123",
        )
        pipeline_kwargs["avatar"] = ProviderRef(provider_id="bey-avatar", credential_id="cred-2")

    config = AgentConfig(
        instructions=instructions,
        pipeline=PipelineConfig(**pipeline_kwargs),
        voice=VoiceConfig(greeting=greeting, greeting_mode=greeting_mode),  # type: ignore[arg-type]
        capabilities=CapabilitiesConfig(camera=camera, chat_input=chat_input),
        tools=ToolsConfig(),
        knowledge=KnowledgeConfig(kb_ids=kb_ids or [], auto_inject=auto_inject),
    )
    return ResolvedAgentConfig(
        session_id=session_id,
        agent_id=agent_id,
        agent_slug="test-agent",
        config_version=1,
        pack_id=pack_id,
        ui_panel_id="generic",
        config=config,
        resolved=resolved,
        tools=tools or [],
        kb_ids=kb_ids or [],
        participant_identity=participant_identity,
        channel=channel,  # type: ignore[arg-type]
    )


class FakeApi:
    """Records everything the worker posts and serves a canned resolved config."""

    def __init__(
        self,
        config: ResolvedAgentConfig | None = None,
        *,
        resolve_error: Exception | None = None,
        hits: list[KbHit] | None = None,
        recording_error: Exception | None = None,
        egress_id: str = "EG_test",
        transfer_result: InternalTransferOut | None = None,
    ) -> None:
        self.config = config or resolved_config()
        self.resolve_error = resolve_error
        self.recording_error = recording_error
        self.egress_id = egress_id
        self.hits = hits or []
        self.resolve_calls: list[str] = []
        self.start_calls: list[SessionStartIn] = []
        self.recording_starts: list[str] = []
        self.recordings: list[SessionRecordingIn] = []
        self.metrics: list[SessionMetricsIn] = []
        self.qa: list[Any] = []
        #: Every write, in order (`"summary"`, `"qa"`, `"metrics"`, ...).
        self.call_log: list[str] = []
        self.events: list[SessionEventIn] = []
        self.summaries: list[SessionSummaryIn] = []
        self.kb_queries: list[tuple[list[str], str, int]] = []
        #: The search options of each `kb_search` (V5-06; `None` when the caller sent none).
        self.kb_options: list[KbSearchOptions | None] = []
        #: Telephony (R-V2-20): every call report, and every transfer request.
        self.call_reports: list[CallReportIn] = []
        self.transfers: list[tuple[str, str, str | None]] = []
        self.transfer_result = transfer_result or InternalTransferOut(ok=True, status="transferred")
        self.closed = False

    async def resolve(self, session_id: str) -> ResolvedAgentConfig:
        """Return the canned config, or raise the configured error."""
        self.resolve_calls.append(session_id)
        if self.resolve_error is not None:
            raise self.resolve_error
        return self.config

    async def start_session(self, request: SessionStartIn) -> ResolvedAgentConfig:
        """Record the request and return the canned config, or raise the configured error."""
        self.start_calls.append(request)
        if self.resolve_error is not None:
            raise self.resolve_error
        return self.config

    async def start_recording(self, session_id: str) -> str:
        """Record the request and return the canned egress id, or raise the configured error."""
        self.recording_starts.append(session_id)
        if self.recording_error is not None:
            raise self.recording_error
        return self.egress_id

    async def post_recording(self, session_id: str, recording: SessionRecordingIn) -> None:
        """Record the posted recording state."""
        self.recordings.append(recording)
        self.call_log.append("recording")

    async def post_metrics(self, session_id: str, metrics: SessionMetricsIn) -> None:
        """Record the posted metrics."""
        self.metrics.append(metrics)
        self.call_log.append("metrics")

    async def put_qa(self, session_id: str, qa: Any) -> None:
        """Record the posted QA verdict."""
        self.qa.append(qa)
        self.call_log.append("qa")

    async def post_events(self, session_id: str, events: list[SessionEventIn]) -> None:
        """Record the posted events."""
        self.events.extend(events)

    async def put_summary(self, session_id: str, summary: SessionSummaryIn) -> None:
        """Record the posted summary."""
        self.summaries.append(summary)
        self.call_log.append("summary")

    async def kb_search(
        self, kb_ids: list[str], query: str, k: int = 4, *, options: KbSearchOptions | None = None
    ) -> list[KbHit]:
        """Record the query (and its options) and return the canned hits."""
        self.kb_queries.append((list(kb_ids), query, k))
        self.kb_options.append(options)
        return self.hits[:k]

    async def report_call(self, report: CallReportIn) -> None:
        """Record a SIP leg report (`call_log` entry `call_report:<status>`)."""
        self.call_reports.append(report)
        self.call_log.append(f"call_report:{report.status}")

    async def transfer_call(
        self, session_id: str, to: str, participant_identity: str | None
    ) -> InternalTransferOut:
        """Record a transfer request and return the canned result."""
        self.transfers.append((session_id, to, participant_identity))
        return self.transfer_result

    async def aclose(self) -> None:
        """Mark the client closed."""
        self.closed = True

    # --- assertions helpers ---------------------------------------------------

    def event_types(self) -> list[str]:
        """Every recorded event's `type`, in order."""
        return [e.type for e in self.events]

    def events_of(self, event_type: str) -> list[SessionEventIn]:
        """Every recorded event of one type."""
        return [e for e in self.events if e.type == event_type]
