"""Unit tests for `lkap_agent.text_mode` (V2-18 rewind / inject_user_text).

Two layers: pure `truncate_to_turn`/`rewind`/`inject_user_text`/
`handle_agent_action` tests against lightweight fakes (no LiveKit SDK
objects), and one dispatch test through the real `UiChannel` RPC handler
(`fakes.fake_room.FakeRoom`, the same harness `test_ui_channel.py` uses),
plus one end-to-end test through `main.run_session` proving the `channel="text"`
branch — and only that branch — wires `on_text_action`.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from fakes.fake_api import FakeApi, resolved_config
from fakes.fake_llm import FakeLLM
from fakes.fake_room import FakeRoom
from fakes.fake_tts import FakeTTS
from livekit import rtc
from livekit.agents import llm
from lkap_contracts.ui_protocol import RPC_AGENT_ACTION, AgentAction, AgentActionResult
from test_main import FakeJobContext, RoomlessStarter, _deps, _metadata

from lkap_agent.main import _wire_optional_modules, run_session
from lkap_agent.providers.factory import BuiltProviders, ProviderFactory
from lkap_agent.text_mode import (
    TextModeError,
    handle_agent_action,
    inject_user_text,
    rewind,
    truncate_to_turn,
)
from lkap_agent.ui.channel import UiChannel


def _msg(role: str, text: str) -> llm.ChatMessage:
    return llm.ChatMessage(role=role, content=[text])  # type: ignore[arg-type]


def _conversation() -> llm.ChatContext:
    """`system, user1, assistant1, user2, assistant2` — two full turns."""
    return llm.ChatContext(
        [
            _msg("system", "You are helpful."),
            _msg("user", "hi"),
            _msg("assistant", "hello"),
            _msg("user", "what is 2+2"),
            _msg("assistant", "4"),
        ]
    )


# ------------------------------------------------------------- truncate_to_turn


def test_truncate_to_turn_keeps_the_system_prompt_and_first_turn() -> None:
    ctx = _conversation()
    truncated = truncate_to_turn(ctx, 1)
    assert [i.text_content for i in truncated.items] == ["You are helpful.", "hi"]
    assert len(ctx.items) == 5, "the input is never mutated"


def test_truncate_to_turn_keeps_two_full_turns() -> None:
    truncated = truncate_to_turn(_conversation(), 2)
    assert [i.text_content for i in truncated.items] == ["You are helpful.", "hi", "hello", "what is 2+2"]


def test_truncate_to_turn_zero_drops_every_turn() -> None:
    truncated = truncate_to_turn(_conversation(), 0)
    assert [i.text_content for i in truncated.items] == ["You are helpful."]


@pytest.mark.parametrize("turn_index", [-1, 3])
def test_truncate_to_turn_out_of_range_raises(turn_index: int) -> None:
    with pytest.raises(TextModeError):
        truncate_to_turn(_conversation(), turn_index)


# -------------------------------------------------------------- rewind / inject


class _FakeAgent:
    def __init__(self) -> None:
        self.updated: list[llm.ChatContext] = []

    async def update_chat_ctx(self, chat_ctx: llm.ChatContext) -> None:
        self.updated.append(chat_ctx)


class _FakeSession:
    def __init__(self, history: llm.ChatContext, *, interrupt_raises: bool = False) -> None:
        self._history = history
        self.agent = _FakeAgent()
        self.interrupt_calls = 0
        self.generate_reply_calls: list[dict[str, Any]] = []
        self._interrupt_raises = interrupt_raises

    @property
    def history(self) -> llm.ChatContext:
        return self._history

    @property
    def current_agent(self) -> _FakeAgent:
        return self.agent

    async def interrupt(self, *, force: bool = False) -> None:
        self.interrupt_calls += 1
        if self._interrupt_raises:
            raise RuntimeError("nothing is playing")

    def generate_reply(self, **kwargs: Any) -> object:
        self.generate_reply_calls.append(kwargs)
        return object()


async def test_rewind_truncates_and_regenerates_once() -> None:
    session = _FakeSession(_conversation())
    await rewind(session, 1)
    assert [i.text_content for i in session.agent.updated[-1].items] == ["You are helpful.", "hi"]
    assert session.generate_reply_calls == [{}]
    assert session.interrupt_calls == 1


async def test_rewind_survives_interrupt_raising() -> None:
    """Nothing playing yet (fresh session) must not block the rewind."""
    session = _FakeSession(_conversation(), interrupt_raises=True)
    await rewind(session, 2)
    assert session.agent.updated, "update_chat_ctx still ran despite interrupt() raising"
    assert session.generate_reply_calls == [{}]


async def test_inject_user_text_calls_generate_reply_with_user_input() -> None:
    session = _FakeSession(_conversation())
    await inject_user_text(session, "one more thing")
    assert session.generate_reply_calls == [{"user_input": "one more thing"}]
    assert session.agent.updated == [], "inject_user_text without a turn_index never truncates"


async def test_inject_user_text_with_turn_index_truncates_then_appends_once() -> None:
    """The "edit turn N" case: exactly one `generate_reply`, not a truncate-then-two-replies race."""
    session = _FakeSession(_conversation())
    await inject_user_text(session, "edited question", turn_index=1)
    assert [i.text_content for i in session.agent.updated[-1].items] == ["You are helpful.", "hi"]
    assert [i.text_content for i in session.history.items] == ["You are helpful.", "hi"]
    assert session.generate_reply_calls == [{"user_input": "edited question"}]


# ------------------------------------------------------------ handle_agent_action


async def test_handle_agent_action_rewind_echoes_turn_index() -> None:
    session = _FakeSession(_conversation())
    result = await handle_agent_action(session, "rewind", {"turn_index": 1})
    assert result == {"turn_index": 1}


async def test_handle_agent_action_inject_user_text_echoes_text_and_turn_index() -> None:
    session = _FakeSession(_conversation())
    result = await handle_agent_action(session, "inject_user_text", {"text": "hello again"})
    assert result == {"text": "hello again", "turn_index": None}


async def test_handle_agent_action_inject_user_text_with_turn_index_edits_in_one_call() -> None:
    session = _FakeSession(_conversation())
    result = await handle_agent_action(
        session, "inject_user_text", {"text": "edited question", "turn_index": 1}
    )
    assert result == {"text": "edited question", "turn_index": 1}
    assert session.generate_reply_calls == [{"user_input": "edited question"}]


@pytest.mark.parametrize("payload", [{}, {"turn_index": "2"}, {"turn_index": -1}, {"turn_index": True}])
async def test_handle_agent_action_rewind_rejects_a_bad_turn_index(payload: dict[str, Any]) -> None:
    with pytest.raises(TextModeError):
        await handle_agent_action(_FakeSession(_conversation()), "rewind", payload)


@pytest.mark.parametrize("turn_index", ["2", -1, True])
async def test_handle_agent_action_inject_user_text_rejects_a_bad_turn_index(turn_index: object) -> None:
    with pytest.raises(TextModeError):
        await handle_agent_action(
            _FakeSession(_conversation()), "inject_user_text", {"text": "hi", "turn_index": turn_index}
        )


@pytest.mark.parametrize("payload", [{}, {"text": ""}, {"text": "   "}, {"text": 5}])
async def test_handle_agent_action_inject_user_text_rejects_empty_text(payload: dict[str, Any]) -> None:
    with pytest.raises(TextModeError):
        await handle_agent_action(_FakeSession(_conversation()), "inject_user_text", payload)


async def test_handle_agent_action_unknown_action_raises() -> None:
    with pytest.raises(TextModeError):
        await handle_agent_action(_FakeSession(_conversation()), "block_action", {})


# ---------------------------------------------------- UiChannel RPC dispatch


def _make_channel(**kwargs: object) -> tuple[UiChannel, FakeRoom]:
    room = FakeRoom()
    channel = UiChannel(room, "sess-1", **kwargs)  # type: ignore[arg-type]
    channel.start()
    return channel, room


async def test_rewind_action_over_rpc_reaches_the_bound_handler() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def on_text_action(action: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append((action, payload))
        return {"turn_index": payload["turn_index"]}

    _channel, room = _make_channel(on_text_action=on_text_action)
    action = AgentAction(action="rewind", payload={"turn_index": 2})
    response = await room.local_participant.invoke_rpc(RPC_AGENT_ACTION, action.model_dump_json())
    result = AgentActionResult.model_validate_json(response)

    assert result.ok is True
    assert result.payload == {"turn_index": 2}
    assert calls == [("rewind", {"turn_index": 2})]


async def test_inject_user_text_action_without_a_bound_handler_returns_an_error() -> None:
    _channel, room = _make_channel()
    action = AgentAction(action="inject_user_text", payload={"text": "hi"})
    response = await room.local_participant.invoke_rpc(RPC_AGENT_ACTION, action.model_dump_json())
    result = AgentActionResult.model_validate_json(response)

    assert result.ok is False
    assert result.error is not None


async def test_bind_does_not_clobber_an_already_bound_on_text_action() -> None:
    """`PlatformAgent._init_blocks` calls `bind()` again for block callbacks; it must not
    silently drop the text-mode hook `main.py` bound first (see `UiChannel.bind` docstring).
    """
    calls: list[str] = []

    async def on_text_action(action: str, _payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(action)
        return {}

    channel, room = _make_channel(on_text_action=on_text_action)

    async def on_block_action(_block_id: str, _name: str, _data: dict[str, Any]) -> dict[str, Any]:
        return {}

    channel.bind(on_block_action=on_block_action)  # a later, unrelated bind() call
    action = AgentAction(action="rewind", payload={"turn_index": 0})
    await room.local_participant.invoke_rpc(RPC_AGENT_ACTION, action.model_dump_json())
    assert calls == ["rewind"]


# -------------------------------------------------------- end-to-end via run_session


class _TextFactory(ProviderFactory):
    """Never touches vendor SDKs: hands back a scripted `FakeLLM` for the `llm` slot."""

    def __init__(self, llm_stub: FakeLLM) -> None:
        self._llm = llm_stub

    def build_all(self, resolved: Any, *, optional: Any = None) -> BuiltProviders:
        assert set(resolved.resolved) == {"llm"}, "a text-channel session builds no audio slots"
        return BuiltProviders(llm=self._llm, tts=FakeTTS())


async def test_run_session_text_channel_wires_rewind_end_to_end() -> None:
    """A `channel="text"` job binds `on_text_action`; `rewind` truncates the started
    session's real chat context and asks it to reply again (CONTRACTS-V2 §3.4).
    """
    api = FakeApi(resolved_config(channel="text", greeting="Hi!"))
    ctx = FakeJobContext(_metadata(), room=cast(rtc.Room, FakeRoom()))
    fake_llm = FakeLLM(["Hi!", "second answer"])
    starter = RoomlessStarter()

    deps = _deps(api, factory=_TextFactory(fake_llm), session_starter=starter)
    _wire_optional_modules(deps)

    await run_session(ctx, deps)
    await asyncio.sleep(0.1)  # let the greeting's `generate_reply` land in history
    assert starter.session is not None
    session = starter.session

    # Seed two user turns directly (bypassing `generate_reply`'s own async
    # scheduling, which nothing here needs to wait out) and spy on
    # `generate_reply` to prove `rewind` asks for exactly one fresh reply.
    session.history.add_message(role="user", content="first question")
    session.history.add_message(role="assistant", content="first answer")
    session.history.add_message(role="user", content="second question")
    reply_calls: list[dict[str, Any]] = []
    session.generate_reply = lambda **kw: reply_calls.append(kw)  # type: ignore[method-assign]

    action = AgentAction(action="rewind", payload={"turn_index": 1})
    response = await ctx.room.local_participant.invoke_rpc(RPC_AGENT_ACTION, action.model_dump_json())
    result = AgentActionResult.model_validate_json(response)
    assert result.ok is True, result.error
    assert result.payload == {"turn_index": 1}
    messages = [i.text_content for i in session.history.items if isinstance(i, llm.ChatMessage)]
    assert messages == ["Hi!", "first question"], "the second turn and its answer are dropped"
    assert reply_calls == [{}], "rewind regenerates exactly once"


async def test_run_session_non_text_channel_does_not_wire_on_text_action() -> None:
    """Only `channel="text"` sessions accept `rewind`/`inject_user_text` (the scaffolding
    the room otherwise takes real audio input/output for, per `session_builder.py`)."""
    api = FakeApi(resolved_config())  # default channel is not "text"
    ctx = FakeJobContext(_metadata(), room=cast(rtc.Room, FakeRoom()))
    deps = _deps(api)
    _wire_optional_modules(deps)

    await run_session(ctx, deps)
    action = AgentAction(action="rewind", payload={"turn_index": 0})
    response = await ctx.room.local_participant.invoke_rpc(RPC_AGENT_ACTION, action.model_dump_json())
    result = AgentActionResult.model_validate_json(response)
    assert result.ok is False
    assert result.error is not None
