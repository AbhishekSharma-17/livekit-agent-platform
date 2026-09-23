"""Tests for `lkap_agent.tools.builtin.*` against `FakePackSessionContext`
(W0-SCAFFOLD's `tests/fakes/fake_ctx.py`). No LiveKit connection, no vendor
keys, no network (HTTP calls go through `respx`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
import respx
from fakes.fake_ctx import FakePackSessionContext, default_agent_config
from livekit import rtc
from livekit.agents import ChatContext, RunContext, ToolError
from livekit.agents.llm.utils import build_legacy_openai_schema
from lkap_contracts.agent_config import CapabilitiesConfig, KnowledgeConfig, PipelineMode, ToolsConfig
from lkap_contracts.api_models import KbHit
from packs.base import FrameSnapshot

from lkap_agent.tools.builtin import BUILTIN_TOOL_NAMES, build_builtin_tools
from lkap_agent.tools.builtin.current_time import build_current_time_tool
from lkap_agent.tools.builtin.describe_current_frame import build_describe_current_frame_tool
from lkap_agent.tools.builtin.end_call import build_end_call_tool
from lkap_agent.tools.builtin.escalate_to_human import build_escalate_to_human_tool
from lkap_agent.tools.builtin.http_request import build_http_request_tool
from lkap_agent.tools.builtin.pin_frame import build_pin_frame_tool
from lkap_agent.tools.builtin.push_note import build_push_note_tool
from lkap_agent.tools.builtin.search_knowledge import build_search_knowledge_tool
from lkap_agent.tools.builtin.set_status import build_set_status_tool


@dataclass
class _FakeFunctionCall:
    call_id: str = "call-1"


@dataclass
class _FakeRunContext:
    function_call: _FakeFunctionCall = field(default_factory=_FakeFunctionCall)


def _run_ctx(call_id: str = "call-1") -> RunContext:
    return cast(RunContext, _FakeRunContext(_FakeFunctionCall(call_id)))


def _tool_parameters(tool: Any) -> dict[str, Any]:
    return cast(dict[str, Any], build_legacy_openai_schema(tool, internally_tagged=True)["parameters"])


def _vision_config(model: str | None = "google/gemini-3.5-flash", **overrides: Any) -> Any:
    """A cascaded config whose LLM slot is a registry model marked `supports_video`."""
    base = default_agent_config(**overrides)
    assert base.pipeline.llm is not None
    base.pipeline.llm.model = model
    return base


def _video_frame(width: int = 2, height: int = 2) -> rtc.VideoFrame:
    return rtc.VideoFrame(
        width=width, height=height, type=rtc.VideoBufferType.RGBA, data=bytes(width * height * 4)
    )


class TestEndCall:
    async def test_cascaded_says_goodbye_and_shuts_down(self) -> None:
        ctx = FakePackSessionContext(pipeline_mode="cascaded")
        shutdown_calls: list[str] = []
        tool = build_end_call_tool(ctx, shutdown=shutdown_calls.append)

        await tool(context=_run_ctx())

        assert ctx.session.said == ["Thanks for calling. Goodbye!"]  # type: ignore[attr-defined]
        assert shutdown_calls == ["end_call tool invoked"]
        # end_call must not clobber a pack's own /status stamp (e.g. a route stamp) —
        # it reports via activity instead (docs/ARCHITECTURE.md §7.3: "session_ending").
        assert ctx.ui.state.status is None
        assert len(ctx.ui.state.activity) == 1
        assert ctx.ui.state.activity[0].source == "end_call"
        assert ctx.ui.state.activity[0].headline == "Session ending"

    async def test_realtime_uses_generate_reply(self) -> None:
        ctx = FakePackSessionContext(pipeline_mode="realtime")
        shutdown_calls: list[str] = []
        tool = build_end_call_tool(ctx, shutdown=shutdown_calls.append)

        await tool(context=_run_ctx(), closing_message="Bye now")

        assert ctx.session.replies_generated == ["Say goodbye: Bye now"]  # type: ignore[attr-defined]
        assert shutdown_calls == ["end_call tool invoked"]

    async def test_defaults_to_get_job_context_shutdown(self) -> None:
        ctx = FakePackSessionContext()
        tool = build_end_call_tool(ctx)

        with pytest.raises(RuntimeError, match="no job context"):
            await tool(context=_run_ctx())


class TestSearchKnowledge:
    async def test_returns_hits_as_json(self) -> None:
        hit = KbHit(
            chunk_id="c1", document_id="d1", filename="policy.md", score=0.987654, text="Coverage is X."
        )
        ctx = FakePackSessionContext()
        ctx.kb.hits = [hit]
        tool = build_search_knowledge_tool(ctx)

        result = await tool(context=_run_ctx(), query="what is covered?")

        assert "policy.md" in result
        assert "Coverage is X." in result
        assert ctx.kb.queries == [("what is covered?", 4, None)]

    async def test_returns_message_when_no_hits(self) -> None:
        ctx = FakePackSessionContext()
        tool = build_search_knowledge_tool(ctx)

        result = await tool(context=_run_ctx(), query="anything")

        assert result == "No relevant knowledge found."

    async def test_uses_configured_top_k_and_the_agents_own_kbs(self) -> None:
        ctx = FakePackSessionContext(
            config=default_agent_config(knowledge=KnowledgeConfig(top_k=2, kb_ids=["kb-1"]))
        )
        tool = build_search_knowledge_tool(ctx)

        await tool(context=_run_ctx(), query="q")

        assert ctx.kb.queries == [("q", 2, None)]

    def test_tool_schema_has_exactly_one_parameter_query(self) -> None:
        """D-W2-12: the `kb` filter is gone; the agent only ever searches its own KBs."""
        params = _tool_parameters(build_search_knowledge_tool(FakePackSessionContext()))
        assert list(params["properties"]) == ["query"]
        assert params.get("required") == ["query"]


class TestPushNote:
    async def test_adds_note_to_ui_state(self) -> None:
        ctx = FakePackSessionContext()
        tool = build_push_note_tool(ctx)

        result = await tool(context=_run_ctx(), text="Hello there")

        assert result == "Noted."
        assert [n.text for n in ctx.ui.state.notes] == ["Hello there"]


class TestSetStatus:
    async def test_patches_status(self) -> None:
        ctx = FakePackSessionContext()
        tool = build_set_status_tool(ctx)

        await tool(context=_run_ctx(), label="Under review", tone="info")

        assert ctx.ui.state.status is not None
        assert ctx.ui.state.status.label == "Under review"
        assert ctx.ui.state.status.tone == "info"


class TestEscalateToHuman:
    async def test_sets_status_and_activity(self) -> None:
        ctx = FakePackSessionContext()
        tool = build_escalate_to_human_tool(ctx)

        result = await tool(context=_run_ctx("call-42"), reason="caller is upset", urgency="high")

        assert "follow up" in result
        assert ctx.ui.state.status is not None
        assert ctx.ui.state.status.label == "Escalated"
        assert len(ctx.ui.state.activity) == 1
        event = ctx.ui.state.activity[0]
        assert event.id == "call-42"
        assert event.headline == "caller is upset"
        assert event.urgent is True

    async def test_escalate_to_human_records_exactly_one_escalation_event(self) -> None:
        ctx = FakePackSessionContext()
        tool = build_escalate_to_human_tool(ctx)

        await tool(context=_run_ctx("call-7"), reason="injury reported", urgency="high")

        assert ctx.events == [("escalation", {"reason": "injury reported", "urgency": "high"})]

    async def test_escalate_to_human_survives_a_failing_event_sink(self) -> None:
        ctx = FakePackSessionContext()

        def _broken(event_type: str, payload: dict[str, Any]) -> None:
            raise RuntimeError("observer gone")

        cast(Any, ctx).record_event = _broken
        tool = build_escalate_to_human_tool(ctx)

        result = await tool(context=_run_ctx(), reason="caller is upset")

        assert "follow up" in result
        assert len(ctx.ui.state.activity) == 1


class TestCurrentTime:
    async def test_defaults_to_utc(self) -> None:
        ctx = FakePackSessionContext()
        tool = build_current_time_tool(ctx)

        result = await tool(context=_run_ctx())

        assert result.endswith("+00:00")

    async def test_uses_configured_timezone(self) -> None:
        ctx = FakePackSessionContext(config=default_agent_config(timezone="America/New_York"))
        tool = build_current_time_tool(ctx)

        result = await tool(context=_run_ctx())

        assert "-04:" in result or "-05:" in result

    async def test_falls_back_to_utc_for_unknown_timezone(self) -> None:
        ctx = FakePackSessionContext(config=default_agent_config(timezone="Not/AZone"))
        tool = build_current_time_tool(ctx)

        result = await tool(context=_run_ctx())

        assert result.endswith("+00:00")


class TestPinFrame:
    async def test_pins_fresh_frame_as_asset(self) -> None:
        ctx = FakePackSessionContext()
        snapshot = FrameSnapshot(frame=_video_frame(), source="camera", age_s=1.0)
        ctx.frames.set_latest(snapshot, jpeg_bytes=b"\xff\xd8\xff\xd9")
        tool = build_pin_frame_tool(ctx)

        result = await tool(context=_run_ctx(), caption="the damage", confirmed=True)

        assert '"pinned": true' in result
        assert len(ctx.ui.state.assets) == 1
        asset = ctx.ui.state.assets[0]
        assert asset.caption == "the damage"
        assert asset.meta == {"source": "camera", "confirmed": "true"}
        assert ctx.ui.assets_pushed[0][1] == "image/jpeg"

    async def test_no_fresh_frame_returns_message(self) -> None:
        ctx = FakePackSessionContext()
        stale = FrameSnapshot(frame=_video_frame(), source="camera", age_s=999.0)
        ctx.frames.set_latest(stale)
        tool = build_pin_frame_tool(ctx)

        result = await tool(context=_run_ctx(), caption="too old")

        assert "No fresh" in result
        assert ctx.ui.state.assets == []


class _FakeChatChunk:
    def __init__(self, content: str | None) -> None:
        self.delta = SimpleNamespace(content=content)


class _FakeLLMStream:
    def __init__(self, chunks: list[_FakeChatChunk]) -> None:
        self._chunks = chunks

    async def __aenter__(self) -> _FakeLLMStream:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def __aiter__(self) -> _FakeLLMStream:
        self._iter = iter(self._chunks)
        return self

    async def __anext__(self) -> _FakeChatChunk:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration from None


class _FakeCascadedLLM:
    def __init__(self, text: str) -> None:
        self._text = text
        self.received_chat_ctx: ChatContext | None = None

    def chat(self, *, chat_ctx: ChatContext, **_kwargs: Any) -> _FakeLLMStream:
        self.received_chat_ctx = chat_ctx
        return _FakeLLMStream([_FakeChatChunk(self._text)])


class TestDescribeCurrentFrame:
    async def test_cascaded_describes_fresh_frame_via_llm(self) -> None:
        fake_llm = _FakeCascadedLLM("A flooded basement.")
        session = SimpleNamespace(llm=fake_llm)
        ctx = FakePackSessionContext(pipeline_mode="cascaded", session=session, config=_vision_config())
        snapshot = FrameSnapshot(frame=_video_frame(), source="camera", age_s=1.0)
        ctx.frames.set_latest(snapshot)
        tool = build_describe_current_frame_tool(ctx)

        result = await tool(context=_run_ctx(), question="what happened?")

        assert result == "A flooded basement."
        assert fake_llm.received_chat_ctx is not None
        message = fake_llm.received_chat_ctx.messages()[0]
        assert message.content[0].__class__.__name__ == "ImageContent"
        assert message.content[1] == "what happened?"

    async def test_cascaded_with_no_fresh_frame(self) -> None:
        session = SimpleNamespace(llm=_FakeCascadedLLM("unused"))
        ctx = FakePackSessionContext(pipeline_mode="cascaded", session=session, config=_vision_config())
        tool = build_describe_current_frame_tool(ctx)

        result = await tool(context=_run_ctx())

        assert "No fresh" in result

    @pytest.mark.parametrize("model", ["google/gemma-4-31b-it", None])
    async def test_cascaded_text_only_model_refuses_before_touching_a_frame(self, model: str | None) -> None:
        """D-W2-10: gemma (also the provider default) silently ignores images."""
        fake_llm = _FakeCascadedLLM("should not be called")
        ctx = FakePackSessionContext(
            pipeline_mode="cascaded", session=SimpleNamespace(llm=fake_llm), config=_vision_config(model)
        )
        ctx.frames.set_latest(FrameSnapshot(frame=_video_frame(), source="camera", age_s=1.0))
        tool = build_describe_current_frame_tool(ctx)

        with pytest.raises(ToolError, match="cannot see images"):
            await tool(context=_run_ctx())
        assert fake_llm.received_chat_ctx is None

    async def test_cascaded_unknown_model_id_is_tried(self) -> None:
        fake_llm = _FakeCascadedLLM("A red card.")
        ctx = FakePackSessionContext(
            pipeline_mode="cascaded",
            session=SimpleNamespace(llm=fake_llm),
            config=_vision_config("someone/free-text-model"),
        )
        ctx.frames.set_latest(FrameSnapshot(frame=_video_frame(), source="camera", age_s=1.0))
        tool = build_describe_current_frame_tool(ctx)

        assert await tool(context=_run_ctx()) == "A red card."

    async def test_cascaded_without_llm_raises_tool_error(self) -> None:
        session = SimpleNamespace(llm=None)
        ctx = FakePackSessionContext(pipeline_mode="cascaded", session=session, config=_vision_config())
        ctx.frames.set_latest(FrameSnapshot(frame=_video_frame(), source="camera", age_s=1.0))
        tool = build_describe_current_frame_tool(ctx)

        with pytest.raises(ToolError, match="No cascaded LLM"):
            await tool(context=_run_ctx())

    @pytest.mark.parametrize("mode", ["realtime", "half_cascade"])
    async def test_realtime_reports_frame_presence_without_calling_llm(self, mode: PipelineMode) -> None:
        # Half-cascade follows the realtime path (asks #54): its realtime model
        # sees the video, and there is no cascaded LLM (session.llm is None).
        ctx = FakePackSessionContext(pipeline_mode=mode, session=SimpleNamespace(llm=None))
        ctx.frames.set_latest(FrameSnapshot(frame=_video_frame(), source="screen", age_s=0.5))
        tool = build_describe_current_frame_tool(ctx)

        result = await tool(context=_run_ctx())

        assert "screen" in result
        assert "already see it" in result

    @pytest.mark.parametrize("mode", ["realtime", "half_cascade"])
    async def test_realtime_with_no_frame(self, mode: PipelineMode) -> None:
        ctx = FakePackSessionContext(pipeline_mode=mode)
        tool = build_describe_current_frame_tool(ctx)

        result = await tool(context=_run_ctx())

        assert "No camera or screen frame" in result


class TestHttpRequestBuiltin:
    @respx.mock
    async def test_allows_platform_allowlisted_host(self) -> None:
        respx.get("https://api.example.com/status").mock(return_value=httpx.Response(200, text="up"))
        ctx = FakePackSessionContext()
        tool = build_http_request_tool(ctx, platform_allowed_hosts=["api.example.com"])

        result = await tool(context=_run_ctx(), method="GET", url="https://api.example.com/status")

        assert result == "up"

    async def test_rejects_host_not_on_platform_allowlist(self) -> None:
        ctx = FakePackSessionContext()
        tool = build_http_request_tool(ctx, platform_allowed_hosts=["other.example.com"])

        with pytest.raises(ToolError, match="not on the outbound allowlist"):
            await tool(context=_run_ctx(), method="GET", url="https://api.example.com/status")

    async def test_rejects_when_no_platform_allowlist_configured(self) -> None:
        ctx = FakePackSessionContext()
        tool = build_http_request_tool(ctx, platform_allowed_hosts=None)

        with pytest.raises(ToolError, match="not on the outbound allowlist"):
            await tool(context=_run_ctx(), method="GET", url="https://api.example.com/status")


class TestBuildBuiltinTools:
    def test_returns_all_tools_by_default_without_vision(self) -> None:
        ctx = FakePackSessionContext(config=default_agent_config(capabilities=CapabilitiesConfig()))
        tools = build_builtin_tools(ctx, disabled=[], http_enabled=True)

        names = {t.info.name for t in tools}
        assert names == set(BUILTIN_TOOL_NAMES) - {"describe_current_frame", "pin_frame"}

    def test_includes_vision_tools_when_camera_enabled(self) -> None:
        ctx = FakePackSessionContext(
            config=default_agent_config(capabilities=CapabilitiesConfig(camera=True))
        )
        tools = build_builtin_tools(ctx, disabled=[], http_enabled=True)

        names = {t.info.name for t in tools}
        assert "describe_current_frame" in names
        assert "pin_frame" in names

    def test_respects_disabled_list(self) -> None:
        ctx = FakePackSessionContext()
        tools = build_builtin_tools(ctx, disabled=["push_note", "current_time"], http_enabled=True)

        names = {t.info.name for t in tools}
        assert "push_note" not in names
        assert "current_time" not in names

    def test_http_request_omitted_when_not_enabled(self) -> None:
        ctx = FakePackSessionContext(
            config=default_agent_config(tools=ToolsConfig(http_request_enabled=False))
        )
        tools = build_builtin_tools(ctx, disabled=[], http_enabled=False)

        names = {t.info.name for t in tools}
        assert "http_request" not in names
