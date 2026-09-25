"""Tests for `lkap_agent.tools.builtin.*` against `FakePackSessionContext`
(W0-SCAFFOLD's `tests/fakes/fake_ctx.py`). No LiveKit connection, no vendor
keys, no network (HTTP calls go through `respx`).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
from lkap_contracts.tools import never_background
from packs.base import FrameSnapshot

from lkap_agent.locale import LOCALE_USERDATA_KEY, SessionLocale
from lkap_agent.settings import DEFAULT_HTTP_TOOL_USER_AGENT
from lkap_agent.tools.builtin import BUILTIN_TOOL_NAMES, build_builtin_tools
from lkap_agent.tools.builtin.convert_time import build_convert_time_tool
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

    async def test_prefers_the_contexts_request_shutdown(self) -> None:
        """Asks #33: the worker routes `end_call` through its own context, not `get_job_context`."""
        ctx = FakePackSessionContext()
        requested: list[str] = []
        ctx.request_shutdown = requested.append  # type: ignore[attr-defined]

        await build_end_call_tool(ctx)(context=_run_ctx())

        assert requested == ["end_call tool invoked"]

    async def test_text_channel_does_not_wait_forever_for_the_goodbye(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Asks #33: a goodbye that never finishes playing must not hold the shutdown."""
        from lkap_agent.tools.builtin import end_call as module  # noqa: PLC0415

        monkeypatch.setattr(module, "TEXT_GOODBYE_TIMEOUT_S", 0.01)
        ctx = FakePackSessionContext(pipeline_mode="cascaded")
        ctx.channel = "text"  # type: ignore[attr-defined]

        async def _never(text: str, **kwargs: Any) -> None:
            await asyncio.Event().wait()

        ctx.session.say = _never  # type: ignore[method-assign]
        shutdown_calls: list[str] = []

        await asyncio.wait_for(
            build_end_call_tool(ctx, shutdown=shutdown_calls.append)(context=_run_ctx()), timeout=1
        )

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


#: R-V5-10 fixtures: 20:15 UTC on Friday 25 September 2026 is 01:45 on Saturday in
#: Kolkata and 21:15 on Friday in London (BST). No test reads the machine's clock or zone.
_FIXED_NOW = datetime(2026, 9, 25, 20, 15, tzinfo=UTC)


def _located_ctx(caller: str = "Asia/Kolkata", business: str = "Europe/London") -> FakePackSessionContext:
    """A context whose session locale is already resolved, on a fixed clock."""
    ctx = FakePackSessionContext(config=default_agent_config(timezone=business))
    ctx.userdata[LOCALE_USERDATA_KEY] = SessionLocale(
        caller_timezone=caller,
        source="browser",
        business_timezone=business,
        started_at=_FIXED_NOW,
        clock=lambda: _FIXED_NOW,
    )
    return ctx


class TestCurrentTime:
    async def test_defaults_to_utc(self) -> None:
        ctx = FakePackSessionContext()
        tool = build_current_time_tool(ctx)

        result = json.loads(await tool(context=_run_ctx()))

        assert result["timezone"] == "UTC"
        assert result["utc_offset"] == "+00:00"
        assert result["business"]["timezone"] == "UTC"

    async def test_without_a_session_locale_uses_the_configured_timezone(self) -> None:
        """Compatibility: no browser zone and no number → the agent's `timezone`, as before."""
        ctx = FakePackSessionContext(config=default_agent_config(timezone="America/New_York"))
        tool = build_current_time_tool(ctx)

        result = json.loads(await tool(context=_run_ctx()))

        assert result["timezone"] == "America/New_York"
        assert result["utc_offset"] in {"-04:00", "-05:00"}

    async def test_falls_back_to_utc_for_unknown_timezone(self) -> None:
        ctx = FakePackSessionContext(config=default_agent_config(timezone="Not/AZone"))
        tool = build_current_time_tool(ctx)

        result = json.loads(await tool(context=_run_ctx()))

        assert result["timezone"] == "UTC"

    async def test_answers_in_the_callers_zone_with_the_business_block(self) -> None:
        tool = build_current_time_tool(_located_ctx())

        result = json.loads(await tool(context=_run_ctx()))

        assert result == {
            "now_iso": "2026-09-26T01:45:00+05:30",
            "weekday": "Saturday",
            "date": "2026-09-26",
            "time": "01:45",
            "timezone": "Asia/Kolkata",
            "utc_offset": "+05:30",
            "business": {
                "now_iso": "2026-09-25T21:15:00+01:00",
                "weekday": "Friday",
                "date": "2026-09-25",
                "time": "21:15",
                "timezone": "Europe/London",
                "utc_offset": "+01:00",
            },
        }

    async def test_an_explicit_timezone_adds_the_callers_block(self) -> None:
        tool = build_current_time_tool(_located_ctx())

        result = json.loads(await tool(context=_run_ctx(), timezone="America/New_York"))

        assert (result["timezone"], result["time"], result["weekday"]) == (
            "America/New_York",
            "16:15",
            "Friday",
        )
        assert result["caller"]["timezone"] == "Asia/Kolkata"
        assert result["business"]["timezone"] == "Europe/London"

    async def test_refuses_an_unknown_timezone(self) -> None:
        tool = build_current_time_tool(_located_ctx())

        with pytest.raises(ToolError, match="Unknown timezone"):
            await tool(context=_run_ctx(), timezone="Mars/Base")

    def test_its_timezone_argument_is_optional(self) -> None:
        params = _tool_parameters(build_current_time_tool(FakePackSessionContext()))

        assert "timezone" in params["properties"]
        assert "timezone" not in params.get("required", [])


class TestConvertTime:
    async def test_converts_the_business_opening_hour_to_the_callers_time_by_default(self) -> None:
        tool = build_convert_time_tool(_located_ctx())

        result = json.loads(await tool(context=_run_ctx(), time="9:00"))

        assert result["from"]["timezone"] == "Europe/London"
        assert (result["from"]["date"], result["from"]["time"]) == ("2026-09-25", "09:00")
        assert (result["to"]["timezone"], result["to"]["time"]) == ("Asia/Kolkata", "13:30")

    @pytest.mark.parametrize(
        ("time", "expected"),
        # London is UTC+1 on 25 September (BST) and UTC+0 on 1 December; New York is UTC-4 / UTC-5.
        [("2:30 pm", "09:30"), ("9am", "04:00"), ("14:30", "09:30"), ("2026-12-01T14:30", "09:30")],
    )
    async def test_reads_clock_times_and_iso_datetimes(self, time: str, expected: str) -> None:
        tool = build_convert_time_tool(_located_ctx())

        result = json.loads(
            await tool(context=_run_ctx(), time=time, from_tz="Europe/London", to_tz="America/New_York")
        )

        assert result["to"]["time"] == expected

    async def test_accepts_the_caller_and_business_aliases(self) -> None:
        tool = build_convert_time_tool(_located_ctx())

        result = json.loads(await tool(context=_run_ctx(), time="18:00", from_tz="caller", to_tz="business"))

        assert (result["from"]["timezone"], result["to"]["timezone"]) == ("Asia/Kolkata", "Europe/London")
        assert result["to"]["time"] == "13:30"

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"time": "9:00", "from_tz": "Mars/Base"}, "Unknown timezone"),
            ({"time": "9:00", "to_tz": "nowhere"}, "Unknown timezone"),
            ({"time": "noonish"}, "Could not read the time"),
            ({"time": "25:00"}, "Could not read the time"),
        ],
    )
    async def test_refuses_what_it_cannot_read(self, kwargs: dict[str, str], message: str) -> None:
        tool = build_convert_time_tool(_located_ctx())

        with pytest.raises(ToolError, match=message):
            await tool(context=_run_ctx(), **kwargs)

    def test_is_registered_and_can_be_disabled(self) -> None:
        ctx = FakePackSessionContext()

        enabled = {t.info.name for t in build_builtin_tools(ctx, disabled=[], http_enabled=False)}
        disabled = {
            t.info.name for t in build_builtin_tools(ctx, disabled=["convert_time"], http_enabled=False)
        }

        assert "convert_time" in enabled
        assert "convert_time" not in disabled

    def test_both_time_tools_never_run_in_the_background(self) -> None:
        assert never_background("current_time")
        assert never_background("convert_time")


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

    @respx.mock
    async def test_sends_the_configured_user_agent(self) -> None:
        """Asks #29: `LKAP_HTTP_TOOL_USER_AGENT` reaches the wire."""
        route = respx.get("https://api.example.com/status").mock(return_value=httpx.Response(200, text="up"))
        ctx = FakePackSessionContext()
        tool = build_http_request_tool(
            ctx, platform_allowed_hosts=["api.example.com"], user_agent=DEFAULT_HTTP_TOOL_USER_AGENT
        )

        await tool(context=_run_ctx(), method="GET", url="https://api.example.com/status")

        assert route.calls.last.request.headers["User-Agent"] == DEFAULT_HTTP_TOOL_USER_AGENT

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
