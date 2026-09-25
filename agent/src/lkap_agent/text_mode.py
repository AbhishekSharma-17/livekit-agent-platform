"""Text-channel sessions: no audio tracks, transcript in and out (CONTRACTS-V2 §3.4).

``channel=text`` sessions run the same agent with audio disabled
(``session_builder.prepare_resolved``/``SessionBuilder.build`` already drop the
audio slots and force typed input — that scaffolding is V2-07's). This module
adds V2-18's two extra ``lkap.agent.action`` verbs the console **Test chat**
drawer and the widget's text mode use to control the conversation directly,
bypassing the room's text-input stream:

* ``rewind`` (``payload={"turn_index": int}``) — truncates the session's
  ``ChatContext`` to end just after the *N*-th user message (1-based;
  ``turn_index=0`` drops every turn) and asks the agent to generate a fresh
  reply to it, discarding whatever the model said the first time and any
  later turns. This is "replay": replaying turn *N* unchanged calls
  ``rewind(N)``.
* ``inject_user_text`` (``payload={"text": str, "turn_index"?: int}``) —
  appends ``text`` as a new user turn and generates a reply, exactly as if
  the browser had typed it through ``RoomOptions.text_input``. With
  ``turn_index`` given, this is "edit": it truncates to that turn **and**
  appends the edited text in the same call, so exactly one reply is
  generated — doing this as two RPCs (``rewind(N - 1)`` then a plain
  ``inject_user_text``) would race two ``generate_reply()`` calls against
  each other and leave a stray reply to the *un*-edited turn in the
  transcript. Without ``turn_index`` it just appends, e.g. for a widget/console
  composer that always sends new text.

Both verbs go through the ``AgentAction`` RPC (``lkap.agent.action``,
CONTRACTS-V2 §4.4) that ``lkap_agent.ui.channel.UiChannel`` already terminates;
``UiChannel.bind(on_text_action=...)`` is the one small hook this package adds
there (see ``docs/v2/_asks.md``, "Open — left by V2-18"). ``main.py`` wires
:func:`handle_agent_action` to that hook only for ``channel="text"`` sessions.

Verified against livekit-agents 1.8.2 (``agent/.venv``):

* ``AgentSession.history`` (``session._chat_ctx``) and the current ``Agent``'s
  own ``chat_ctx`` (``agent._chat_ctx``) are **two different objects**: the
  session's is the append-only transcript built purely from
  ``conversation_item_added``/tool-item events, the agent's is its own working
  copy the *next* LLM/realtime call actually reads. Rewinding needs both kept
  in sync — ``ChatContext.items`` has a public setter for exactly this, so
  :func:`_truncate_and_sync` truncates a copy of ``session.history`` and
  assigns it to both ``session.history.items`` (so the transcript — and a
  later ``rewind`` reading it again — reflects the drop) and, via
  ``Agent.update_chat_ctx``, the agent's own context (so the next reply is
  generated from it).
* ``Agent.update_chat_ctx`` delegates to ``AgentActivity.update_chat_ctx``,
  which forwards to the realtime session too (``self._rt_session.
  update_chat_ctx``) when one is running — so this module needs no
  cascaded/realtime branch: a half-cascade text session
  (``session_builder.factory_view`` turns a ``realtime`` pipeline into
  ``half_cascade`` for the text channel) calls the same code path.
* **Gemini Live is best-effort only.** ``livekit-plugins-google``'s
  ``RealtimeSession.update_chat_ctx`` → ``_sync_chat_ctx`` diffs the new
  context against its own record and, for anything in ``diff_ops.to_remove``,
  logs ``"Gemini Live does not support removing messages"`` and does nothing
  further — the Realtime API itself has no "delete an item" call. On a
  half-cascade text session bound to Gemini Live, ``rewind``/``inject_user_text``
  with a ``turn_index`` still truncate `session.history` (so the *browser's*
  transcript and turn numbering are correct) and the local ``Agent`` object's
  working copy, but the remote model may still recall the dropped turns when
  reasoning about the conversation so far. Every other realtime engine that
  reaches this path (`RealtimeSession.update_chat_ctx` implementations for
  OpenAI/Azure Realtime) is unaffected. Tracked for live verification
  (PLAN-V2 §6 stage L12c) rather than worked around here — there is no local
  workaround short of not using Gemini Live for text-mode rewind.
* ``AgentSession.generate_reply(user_input=...)`` inserts the user message and
  produces a reply in one call; it is synchronous (returns a ``SpeechHandle``
  immediately, per the codebase's existing fire-and-forget
  ``PlatformAgent._on_unsolicited_form`` pattern) — nothing here awaits it.
"""

from __future__ import annotations

import contextlib
from collections.abc import Collection
from typing import Any, Protocol

from livekit.agents import llm

from lkap_agent.logging import get_logger
from lkap_agent.tools.execution import cancel_running

__all__ = [
    "TextModeError",
    "handle_agent_action",
    "inject_user_text",
    "rewind",
    "strip_calls",
    "truncate_to_turn",
]

logger = get_logger(__name__)


class TextModeError(ValueError):
    """An invalid ``rewind``/``inject_user_text`` payload.

    Raised from :func:`handle_agent_action`; the caller (``UiChannel``) turns
    it into ``AgentActionResult(ok=False, error=str(exc))``, same as any other
    ``lkap.agent.action`` failure.
    """


class _AgentLike(Protocol):
    """The slice of ``livekit.agents.Agent`` this module needs."""

    async def update_chat_ctx(self, chat_ctx: llm.ChatContext) -> None: ...


class _SessionLike(Protocol):
    """The slice of ``livekit.agents.AgentSession`` this module needs (testable without the SDK)."""

    @property
    def history(self) -> llm.ChatContext: ...

    @property
    def current_agent(self) -> _AgentLike: ...

    def interrupt(self, *, force: bool = False) -> Any: ...

    # Only ever called with no arguments (`rewind`) or `user_input=` alone
    # (`inject_user_text`); a real `AgentSession.generate_reply` takes several
    # other optional keyword arguments, all with `NotGiven` defaults, which
    # this module never sets — so `user_input: str = ...` (never `None`,
    # which the real signature's `NotGivenOr[str | ChatMessage]` rejects) is
    # the exact, mypy-compatible slice used here.
    def generate_reply(self, *, user_input: str = ...) -> Any: ...


def truncate_to_turn(chat_ctx: llm.ChatContext, turn_index: int) -> llm.ChatContext:
    """Return a new ``ChatContext`` ending just after the ``turn_index``-th user message.

    ``turn_index`` is 1-based and counts only ``role="user"`` messages
    (function calls/outputs and assistant replies are not turns). ``0`` keeps
    only whatever precedes the first user message (e.g. a stored system
    prompt). The input is never mutated.

    Args:
        chat_ctx: The session's current chat context (``session.history``).
        turn_index: The last user turn to keep.

    Returns:
        A fresh ``ChatContext`` (never ``chat_ctx`` itself).

    Raises:
        TextModeError: ``turn_index`` is negative or exceeds the number of
            user turns recorded.
    """
    items = chat_ctx.items
    user_indices = [i for i, item in enumerate(items) if item.type == "message" and item.role == "user"]
    if turn_index < 0 or turn_index > len(user_indices):
        raise TextModeError(
            f"turn_index {turn_index} is out of range: this conversation has {len(user_indices)} user turn(s)"
        )
    if turn_index == 0:
        # Keep whatever precedes the first user message (e.g. a stored system prompt);
        # with no user turns at all there is nothing to drop.
        boundary = user_indices[0] - 1 if user_indices else len(items) - 1
    else:
        boundary = user_indices[turn_index - 1]
    return llm.ChatContext(list(items[: boundary + 1]))


async def _truncate_and_sync(
    session: _SessionLike, turn_index: int, *, drop_call_ids: Collection[str] = ()
) -> None:
    """Truncate to ``turn_index`` and apply it to both chat-context copies.

    Shared by :func:`rewind` and :func:`inject_user_text`'s edit path — see
    the module docstring's "two different objects" note for why both
    ``session.history`` and the current agent's own context need the write.
    Never calls ``generate_reply`` itself, so a caller can truncate and then
    append in the same turn without two competing replies being queued.

    ``drop_call_ids`` are the calls :func:`cancel_running` just cancelled: their
    items are removed even when they sit before the cut (a background call from
    a kept turn), so the model never reads an announcement whose result will not
    come (ask #102).

    Raises:
        TextModeError: An invalid ``turn_index`` (:func:`truncate_to_turn`).
    """
    with contextlib.suppress(Exception):
        await session.interrupt(force=True)
    truncated = strip_calls(truncate_to_turn(session.history, turn_index), drop_call_ids)
    session.history.items = truncated.items
    await session.current_agent.update_chat_ctx(truncated)


def strip_calls(chat_ctx: llm.ChatContext, call_ids: Collection[str]) -> llm.ChatContext:
    """Return ``chat_ctx`` without the function calls and outputs of ``call_ids``.

    A call's entries are its own ``call_id`` plus the SDK's derived ids for its
    progress updates and deferred result (``<call_id>_update_N``, ``<call_id>_final``,
    ``RunContext._make_update_pair`` in livekit-agents 1.8.2 ``voice/events.py``).

    Args:
        chat_ctx: The context to filter; never mutated.
        call_ids: The base call ids to drop.

    Returns:
        A new ``ChatContext`` (``chat_ctx`` itself when there is nothing to drop).
    """
    if not call_ids:
        return chat_ctx
    ids = tuple(call_ids)
    prefixes = tuple(f"{call_id}_" for call_id in ids)

    def _belongs(item: llm.ChatItem) -> bool:
        if item.type not in ("function_call", "function_call_output"):
            return False
        return item.call_id in ids or item.call_id.startswith(prefixes)

    return llm.ChatContext([item for item in chat_ctx.items if not _belongs(item)])


async def rewind(session: _SessionLike, turn_index: int) -> None:
    """Truncate the session to turn ``turn_index`` and regenerate the reply once.

    Args:
        session: The running ``AgentSession``.
        turn_index: See :func:`truncate_to_turn`.

    Raises:
        TextModeError: An invalid ``turn_index``.
    """
    # A background tool still running belongs to the conversation being cut away;
    # its result would land after the rewind (docs/v4/BACKGROUND-TOOLS.md D-V4-34).
    # Awaited, and its items stripped, so the regenerated reply neither reads the
    # SDK's "still in progress" placeholder nor an announcement for it (ask #102).
    cancelled = await cancel_running(session)
    await _truncate_and_sync(session, turn_index, drop_call_ids=cancelled)
    session.generate_reply()


async def inject_user_text(session: _SessionLike, text: str, *, turn_index: int | None = None) -> None:
    """Add ``text`` as a new user turn and generate a reply, once.

    Args:
        session: The running ``AgentSession``.
        text: The typed (or edited) message.
        turn_index: When given, truncates to this turn *before* appending —
            the "edit turn N" case (the caller passes ``N - 1``) — in the same
            call that generates the reply, so exactly one reply is produced.
            ``None`` just appends to the conversation as it stands.

    Raises:
        TextModeError: An invalid ``turn_index``.
    """
    if turn_index is not None:
        # Editing a turn rewinds too: work started after it no longer belongs to the conversation.
        cancelled = await cancel_running(session)
        await _truncate_and_sync(session, turn_index, drop_call_ids=cancelled)
    session.generate_reply(user_input=text)


async def handle_agent_action(session: _SessionLike, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a ``rewind``/``inject_user_text`` ``AgentAction`` onto ``session``.

    Args:
        session: The running ``AgentSession`` for this text-channel job.
        action: ``"rewind"`` or ``"inject_user_text"``.
        payload: The action's ``AgentAction.payload``.

    Returns:
        The ``AgentActionResult.payload`` to echo back to the browser.

    Raises:
        TextModeError: A malformed payload or an unknown action.
    """
    if action == "rewind":
        turn_index = _require_turn_index(payload, key="turn_index", required=True)
        assert turn_index is not None  # noqa: S101 - `required=True` guarantees this
        await rewind(session, turn_index)
        logger.info("text_mode_rewind", turn_index=turn_index)
        return {"turn_index": turn_index}
    if action == "inject_user_text":
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise TextModeError("inject_user_text needs non-empty text")
        turn_index = _require_turn_index(payload, key="turn_index", required=False)
        await inject_user_text(session, text, turn_index=turn_index)
        logger.info("text_mode_inject_user_text", length=len(text), turn_index=turn_index)
        return {"text": text, "turn_index": turn_index}
    raise TextModeError(f"unsupported text-mode action: {action!r}")


def _require_turn_index(payload: dict[str, Any], *, key: str, required: bool) -> int | None:
    """Validate ``payload[key]`` as a non-negative int; ``None`` only when ``not required``."""
    value = payload.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise TextModeError(f"{key} must be a non-negative integer")
    return value
