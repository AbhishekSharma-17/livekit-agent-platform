"""`ChatManager`: the MCP process's text test chats (AGENT-ACCESS §4.7, PLAN-V3 V3-02).

One chat = one ``channel="text"`` session the MCP process joins as the user:

* **start** -- limits first (``too_many_chats``), then the **worker preflight**
  (``GET /v1/agents/{id}`` → the agent's connection, or the workspace default
  when it is unbound, exactly as the api's ``resolve_agent_connection`` falls
  back; ``GET /v1/connections/{id}/fleet`` must list a ``ready`` instance, else
  ``no_worker`` with ``next_steps`` and **no session is minted**), then
  ``POST /v1/agents/{id}/text-sessions``, join the room, wait for the agent
  (``agent_did_not_join`` after ``timeout_s``; the session row is left for the
  api's stale-session sweep) and capture the greeting;
* **send** -- one user turn on ``lk.chat``; returns when a final agent reply
  has arrived and no further one follows within ``settle_s`` (a tool-calling
  turn often speaks twice), or at ``timeout_s`` with ``state="timeout"`` and
  whatever arrived; then tails ``GET /v1/sessions/{id}/events?after_id=``
  (best-effort: the worker flushes events on a timer);
* **rewind** -- the V2-18 ``rewind`` / ``inject_user_text`` ``AgentAction`` over
  ``lkap.agent.action``, then collects the regenerated reply like ``send``;
* **end** -- leaves the room and forgets the chat.

Turn numbering matches the worker's (``lkap_agent.text_mode.truncate_to_turn``):
1-based, user turns only; the greeting is not a turn. After ``rewind(N)`` (or
an edit of turn ``N``) the chat is at turn ``N``.

Limits: ``max_per_owner`` concurrent chats per owner (the MCP session; one
owner in stdio mode) and ``max_total`` per process; a chat idle for
``idle_timeout_s`` (measured on the injectable ``clock``) is closed by
:meth:`ChatManager.sweep_idle`, which the reaper task runs periodically; a
chat whose room drops is closed as soon as the drop is seen and reported once
as ``chat_disconnected``; :meth:`ChatManager.close_all` runs on shutdown.

The manager talks to the api through the small :class:`ChatApi` Protocol and to
the room through :class:`~lkap_mcp.chat.transport.RoomTransport`, so it has no
dependency on the server core; ``chat.tools`` adapts the core's client. Reply
text is returned raw here; the tools wrap it as untrusted content.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from urllib.parse import quote

from lkap_contracts.ui_protocol import RPC_AGENT_ACTION, AgentAction, AgentActionResult

from lkap_mcp.chat.transport import (
    RoomTransport,
    TransportFactory,
    TransportUnavailableError,
)

__all__ = [
    "DEFAULT_OWNER",
    "ChatAgent",
    "ChatApi",
    "ChatApiError",
    "ChatError",
    "ChatManager",
    "EndOutcome",
    "StartOutcome",
    "TurnOutcome",
    "TurnState",
]

logger = logging.getLogger(__name__)

DEFAULT_OWNER = "stdio"
"""The owner key of every chat in stdio mode (one MCP session per process)."""

TurnState = Literal["final", "timeout", "disconnected"]


class ChatApiError(Exception):
    """An api error relayed by the :class:`ChatApi` adapter (``{code, message, details}``)."""

    def __init__(
        self, code: str, message: str, *, status: int | None = None, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


class ChatApi(Protocol):
    """The two ``/v1`` calls the chat needs; ``chat.tools`` adapts the server core's client.

    Both return the decoded JSON body and raise :class:`ChatApiError` on a non-2xx.
    """

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        """``GET path``."""
        ...

    async def post(self, path: str, *, json: Any = None) -> Any:
        """``POST path`` with a JSON body."""
        ...


class ChatError(Exception):
    """A chat tool failure with a stable ``code`` (``no_worker``, ``too_many_chats`` …)."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        next_steps: list[str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.next_steps = next_steps or []
        self.details = details or {}


@dataclass(frozen=True, slots=True)
class ChatAgent:
    """The agent a chat talks to."""

    id: str
    slug: str
    name: str
    mode: str
    pipeline_mode: str


@dataclass(frozen=True, slots=True)
class StartOutcome:
    """``chat_start``'s result before wrapping."""

    chat_id: str
    session_id: str
    agent: ChatAgent
    greeting: str | None
    greeting_state: Literal["final", "timeout", "skipped", "disconnected"]


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    """``chat_send`` / ``chat_rewind``'s result before wrapping."""

    replies: list[str]
    turn_index: int
    state: TurnState
    events: list[dict[str, Any]]
    late_replies: list[str] = field(default_factory=list)
    events_error: str | None = None
    session_id: str = ""


@dataclass(frozen=True, slots=True)
class EndOutcome:
    """``chat_end``'s result."""

    session_id: str
    turns: int
    url: str


_DISCONNECTED: Any = object()
_RECENT_MAX = 50


@dataclass
class _Chat:
    chat_id: str
    owner: str
    session_id: str
    agent: ChatAgent
    transport: RoomTransport
    agent_identity: str
    last_used: float
    turn_index: int = 0
    last_event_id: int | None = None
    disconnected: bool = False
    replies: asyncio.Queue[Any] = field(default_factory=asyncio.Queue)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pump: asyncio.Task[None] | None = None


class ChatManager:
    """Holds the process's test chats (see the module docstring)."""

    def __init__(
        self,
        api: ChatApi,
        transport_factory: TransportFactory,
        *,
        max_per_owner: int = 3,
        max_total: int = 20,
        idle_timeout_s: float = 300.0,
        settle_s: float = 1.0,
        rpc_timeout_s: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._api = api
        self._factory = transport_factory
        self.max_per_owner = max_per_owner
        self.max_total = max_total
        self.idle_timeout_s = idle_timeout_s
        self.settle_s = settle_s
        self.rpc_timeout_s = rpc_timeout_s
        self._clock = clock
        self._chats: dict[str, _Chat] = {}
        self._starting: dict[str, int] = {}
        #: Outcomes of recently closed chats, so ``chat_end`` stays idempotent after an
        #: idle close or a drop (bounded; oldest dropped first).
        self._recent: dict[str, tuple[str, EndOutcome]] = {}
        self._reaper: asyncio.Task[None] | None = None

    # -- introspection -------------------------------------------------------------
    def chat_ids(self, owner: str | None = None) -> list[str]:
        """Open chat ids, optionally only ``owner``'s."""
        return [c.chat_id for c in self._chats.values() if owner is None or c.owner == owner]

    # -- start ---------------------------------------------------------------------
    async def start(
        self,
        agent_id_or_slug: str,
        *,
        owner: str = DEFAULT_OWNER,
        participant_name: str = "lkap-mcp",
        wait_for_greeting: bool = True,
        timeout_s: float = 30.0,
    ) -> StartOutcome:
        """Preflight, mint a text session, join the room and capture the greeting.

        Raises:
            ChatError: ``too_many_chats``, ``no_worker``, ``chat_unavailable``,
                ``connect_failed`` or ``agent_did_not_join``.
            ChatApiError: An api call failed (unknown agent, rate limit, busy …).
        """
        self._reserve(owner)
        try:
            return await self._start(agent_id_or_slug, owner, participant_name, wait_for_greeting, timeout_s)
        finally:
            self._starting[owner] -= 1
            if not self._starting[owner]:
                del self._starting[owner]

    def _reserve(self, owner: str) -> None:
        mine = sum(1 for c in self._chats.values() if c.owner == owner) + self._starting.get(owner, 0)
        total = len(self._chats) + sum(self._starting.values())
        if mine >= self.max_per_owner or total >= self.max_total:
            limit = self.max_per_owner if mine >= self.max_per_owner else self.max_total
            raise ChatError(
                "too_many_chats",
                f"at most {limit} test chats may be open at once",
                next_steps=["chat_end(chat_id) a chat you no longer need, then retry"],
                details={"open_chat_ids": self.chat_ids(owner), "limit": limit},
            )
        self._starting[owner] = self._starting.get(owner, 0) + 1

    async def _start(
        self,
        agent_id_or_slug: str,
        owner: str,
        participant_name: str,
        wait_for_greeting: bool,
        timeout_s: float,
    ) -> StartOutcome:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        agent_path = f"/v1/agents/{quote(agent_id_or_slug, safe='')}"
        agent_row = await self._api.get(agent_path)
        agent = _agent_of(agent_row)
        await self._preflight_worker(agent_row)

        try:
            transport = self._factory()
        except TransportUnavailableError as exc:
            raise ChatError(
                "chat_unavailable",
                "the LiveKit rtc SDK cannot be loaded in this MCP process",
                next_steps=["use the console's Test chat on the agent page instead"],
                details={"reason": str(exc)},
            ) from exc

        try:
            minted = await self._api.post(
                f"/v1/agents/{quote(agent.id, safe='')}/text-sessions",
                json={"participant_name": participant_name},
            )
        except BaseException:
            await _quiet_close(transport)
            raise
        session_id = str(minted["sessionId"])
        try:
            await transport.connect(str(minted["serverUrl"]), str(minted["participantToken"]))
        except (ConnectionError, OSError, TransportUnavailableError) as exc:
            await _quiet_close(transport)
            raise ChatError(
                "connect_failed",
                f"could not join the session's room: {exc}",
                details={"session_id": session_id},
            ) from exc

        try:
            identity = await transport.wait_for_agent(max(0.0, deadline - loop.time()))
        except TimeoutError as exc:
            await _quiet_close(transport)
            raise ChatError(
                "agent_did_not_join",
                f"no agent joined the room within {timeout_s:g}s",
                next_steps=[
                    "check the connection's fleet (connection_get / connection_fleet): a ready "
                    "worker must be registered under the connection's agent_name",
                    "the session is left for the api's stale-session sweep",
                ],
                details={"session_id": session_id},
            ) from exc

        chat = _Chat(
            chat_id=f"chat_{secrets.token_urlsafe(9)}",
            owner=owner,
            session_id=session_id,
            agent=agent,
            transport=transport,
            agent_identity=identity,
            last_used=self._clock(),
        )
        chat.pump = asyncio.ensure_future(self._pump(chat))
        self._chats[chat.chat_id] = chat
        logger.info("chat_started chat_id=%s session_id=%s", chat.chat_id, session_id)

        greeting: str | None = None
        greeting_state: Literal["final", "timeout", "skipped", "disconnected"] = "skipped"
        if wait_for_greeting:
            texts, state = await self._collect(chat, max(0.0, deadline - loop.time()))
            greeting = "\n\n".join(texts) if texts else None
            greeting_state = state
        return StartOutcome(
            chat_id=chat.chat_id,
            session_id=session_id,
            agent=agent,
            greeting=greeting,
            greeting_state=greeting_state,
        )

    async def _preflight_worker(self, agent_row: dict[str, Any]) -> None:
        """Refuse with ``no_worker`` unless the agent's connection has a ``ready`` worker."""
        connection_id = agent_row.get("connection_id")
        connection: dict[str, Any] | None = None
        if not connection_id:
            page = await self._api.get("/v1/connections")
            items = page.get("items", []) if isinstance(page, dict) else list(page)
            connection = next((c for c in items if c.get("is_default")), None)
            if connection is None:
                raise ChatError(
                    "no_connection",
                    "the agent is not bound to a LiveKit connection and the workspace has no default",
                    next_steps=["agent_update(id, connection_id=...) or connection_create(...)"],
                )
            connection_id = connection["id"]
        cid = quote(str(connection_id), safe="")
        fleet = await self._api.get(f"/v1/connections/{cid}/fleet")
        if any(i.get("status") == "ready" for i in fleet.get("instances", [])):
            return
        if connection is None:
            connection = await self._api.get(f"/v1/connections/{cid}")
        mode = connection.get("deployment_mode", "external")
        name = connection.get("agent_name", "lkap-agent")
        if mode == "supervised":
            steps = [
                f'connection_fleet(id="{connection_id}", action="start") to start the supervised pool, '
                "then retry chat_start once an instance is ready",
            ]
        else:
            steps = [
                f'connection_get(id="{connection_id}", include_worker_env=true) for the worker-env '
                f'template, then run a worker registered as agent_name "{name}"',
                "retry chat_start once connection_fleet shows a ready instance",
            ]
        raise ChatError(
            "no_worker",
            "no ready worker is registered for the agent's LiveKit connection, so no agent would "
            "join; no session was started",
            next_steps=steps,
            details={"connection_id": str(connection_id), "deployment_mode": mode},
        )

    # -- send / rewind -------------------------------------------------------------
    async def send(
        self, chat_id: str, text: str, *, owner: str = DEFAULT_OWNER, timeout_s: float = 60.0
    ) -> TurnOutcome:
        """Send one user turn and wait for the reply.

        Raises:
            ChatError: ``unknown_chat``, ``chat_disconnected`` or ``send_failed``.
        """
        if not text.strip():
            raise ChatError("invalid_input", "text must not be empty")
        chat = self._get(chat_id, owner)
        async with chat.lock:
            await self._ensure_live(chat)
            late = _drain(chat)
            try:
                await chat.transport.send_text(text)
            except Exception as exc:  # noqa: BLE001 - relayed as a typed tool error
                raise ChatError("send_failed", f"could not send the turn: {exc}") from exc
            chat.turn_index += 1
            return await self._finish_turn(chat, timeout_s, late)

    async def rewind(
        self,
        chat_id: str,
        turn_index: int,
        *,
        replace_text: str | None = None,
        owner: str = DEFAULT_OWNER,
        timeout_s: float = 60.0,
    ) -> TurnOutcome:
        """Rewind to ``turn_index`` (regenerate its reply) or, with ``replace_text``, edit it.

        ``rewind`` sends ``{"action": "rewind", "payload": {"turn_index": N}}``;
        an edit sends ``inject_user_text`` with ``turn_index=N-1`` so the worker
        truncates and appends in one call (exactly one reply), as the console's
        ``editTurn`` does.

        Raises:
            ChatError: ``unknown_chat``, ``invalid_turn``, ``chat_disconnected``,
                ``rpc_failed`` or ``rewind_failed``.
        """
        chat = self._get(chat_id, owner)
        async with chat.lock:
            await self._ensure_live(chat)
            low = 1 if replace_text is not None else 0
            if turn_index < low or turn_index > chat.turn_index:
                raise ChatError(
                    "invalid_turn",
                    f"turn_index must be between {low} and {chat.turn_index} for this chat",
                    details={"turns": chat.turn_index},
                )
            if replace_text is not None:
                if not replace_text.strip():
                    raise ChatError("invalid_input", "replace_text must not be empty")
                action = AgentAction(
                    action="inject_user_text", payload={"text": replace_text, "turn_index": turn_index - 1}
                )
            else:
                action = AgentAction(action="rewind", payload={"turn_index": turn_index})
            late = _drain(chat)
            try:
                raw = await chat.transport.rpc(
                    RPC_AGENT_ACTION, action.model_dump_json(), timeout_s=self.rpc_timeout_s
                )
            except Exception as exc:  # noqa: BLE001 - rtc.RpcError, timeouts: relayed typed
                raise ChatError("rpc_failed", f"the agent did not accept the action: {exc}") from exc
            try:
                result = AgentActionResult.model_validate_json(raw)
            except ValueError as exc:
                raise ChatError("rpc_failed", "the agent returned an unreadable action result") from exc
            if not result.ok:
                raise ChatError("rewind_failed", result.error or "the agent refused the action")
            chat.turn_index = turn_index
            return await self._finish_turn(chat, timeout_s, late)

    async def _finish_turn(self, chat: _Chat, timeout_s: float, late: list[str]) -> TurnOutcome:
        replies, state = await self._collect(chat, timeout_s)
        events, events_error = await self._tail_events(chat)
        chat.last_used = self._clock()
        if state == "disconnected":
            await self._discard(chat)
        return TurnOutcome(
            replies=replies,
            turn_index=chat.turn_index,
            state=state,
            events=events,
            late_replies=late,
            events_error=events_error,
            session_id=chat.session_id,
        )

    # -- end / lifecycle -----------------------------------------------------------
    async def end(self, chat_id: str, *, owner: str = DEFAULT_OWNER) -> EndOutcome:
        """Leave the room and forget the chat.

        Raises:
            ChatError: ``unknown_chat``.
        """
        recent = self._recent.get(chat_id)
        if chat_id not in self._chats and recent is not None and recent[0] == owner:
            return recent[1]
        chat = self._get(chat_id, owner)
        async with chat.lock:
            await self._discard(chat)
        return _end_outcome(chat)

    async def sweep_idle(self) -> list[str]:
        """Close every chat idle for longer than ``idle_timeout_s`` (or already dropped)."""
        now = self._clock()
        stale = [
            c for c in list(self._chats.values()) if c.disconnected or now - c.last_used > self.idle_timeout_s
        ]
        for chat in stale:
            if chat.lock.locked():
                continue
            await self._discard(chat)
            logger.info("chat_closed_idle chat_id=%s", chat.chat_id)
        return [c.chat_id for c in stale if c.chat_id not in self._chats]

    def start_reaper(self, interval_s: float = 30.0) -> None:
        """Run :meth:`sweep_idle` every ``interval_s`` until :meth:`close_all`."""
        if self._reaper is not None and not self._reaper.done():
            return

        async def loop() -> None:
            while True:
                await asyncio.sleep(interval_s)
                try:
                    await self.sweep_idle()
                except Exception:  # noqa: BLE001 - the reaper must outlive one bad sweep
                    logger.exception("chat idle sweep failed")

        self._reaper = asyncio.ensure_future(loop())

    async def close_owner(self, owner: str) -> list[str]:
        """Close every chat of ``owner`` (HTTP mode: the MCP session ended, §9.1)."""
        mine = [c for c in list(self._chats.values()) if c.owner == owner]
        for chat in mine:
            await self._discard(chat)
        return [c.chat_id for c in mine]

    async def close_all(self) -> None:
        """Stop the reaper and close every chat (server shutdown)."""
        if self._reaper is not None:
            self._reaper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reaper
            self._reaper = None
        for chat in list(self._chats.values()):
            await self._discard(chat)

    # -- internals -----------------------------------------------------------------
    def _get(self, chat_id: str, owner: str) -> _Chat:
        chat = self._chats.get(chat_id)
        if chat is None or chat.owner != owner:
            raise ChatError(
                "unknown_chat",
                f"no open chat '{chat_id}' (ended, idle-closed or never started)",
                next_steps=["chat_start(agent_id_or_slug) to open a new chat"],
            )
        return chat

    async def _ensure_live(self, chat: _Chat) -> None:
        if chat.disconnected or not chat.transport.connected:
            await self._discard(chat)
            raise ChatError(
                "chat_disconnected",
                "the room disconnected (the agent left or the session ended); the chat is closed",
                next_steps=["session_get(session_id) for the transcript", "chat_start to open a new chat"],
                details={"session_id": chat.session_id, "turns": chat.turn_index},
            )

    async def _pump(self, chat: _Chat) -> None:
        """Forward the agent's final replies into the chat's queue; mark the chat dropped at the end."""
        try:
            async for reply in chat.transport.replies():
                if reply.participant != chat.agent_identity or not reply.final:
                    continue
                if reply.text.strip():
                    chat.replies.put_nowait(reply.text)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a transport failure is a disconnect
            logger.debug("chat reply pump failed chat_id=%s", chat.chat_id, exc_info=True)
        chat.disconnected = True
        chat.replies.put_nowait(_DISCONNECTED)

    async def _collect(self, chat: _Chat, timeout_s: float) -> tuple[list[str], TurnState]:
        """Wait for a final reply, then keep collecting while more arrive within ``settle_s``."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        texts: list[str] = []
        dropped = False
        while True:
            remaining = deadline - loop.time()
            wait = min(remaining, self.settle_s) if texts else remaining
            if wait <= 0:
                break
            try:
                item = await asyncio.wait_for(chat.replies.get(), wait)
            except TimeoutError:
                break
            if item is _DISCONNECTED:
                dropped = True
                break
            texts.append(item)
        if texts:
            return texts, "final"
        return texts, "disconnected" if dropped else "timeout"

    async def _tail_events(self, chat: _Chat) -> tuple[list[dict[str, Any]], str | None]:
        params: dict[str, Any] = {"limit": 200}
        if chat.last_event_id is not None:
            params["after_id"] = chat.last_event_id
        try:
            page = await self._api.get(
                f"/v1/sessions/{quote(chat.session_id, safe='')}/events", params=params
            )
        except ChatApiError as exc:
            return [], f"{exc.code}: {exc.message}"
        items = [i for i in page.get("items", []) if isinstance(i, dict)]
        ids = [int(i["id"]) for i in items if isinstance(i.get("id"), int)]
        if ids:
            chat.last_event_id = max(ids)
        return items, None

    async def _discard(self, chat: _Chat) -> None:
        if self._chats.pop(chat.chat_id, None) is not None:
            self._recent[chat.chat_id] = (chat.owner, _end_outcome(chat))
            while len(self._recent) > _RECENT_MAX:
                self._recent.pop(next(iter(self._recent)))
        if chat.pump is not None and not chat.pump.done():
            chat.pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await chat.pump
        await _quiet_close(chat.transport)
        chat.disconnected = True


def _drain(chat: _Chat) -> list[str]:
    """Take replies that arrived after the previous turn returned (late segments)."""
    late: list[str] = []
    while not chat.replies.empty():
        item = chat.replies.get_nowait()
        if item is _DISCONNECTED:
            chat.replies.put_nowait(item)
            break
        late.append(item)
    return late


def _agent_of(row: dict[str, Any]) -> ChatAgent:
    config = row.get("config") or {}
    pipeline = config.get("pipeline") or {}
    return ChatAgent(
        id=str(row["id"]),
        slug=str(row.get("slug", "")),
        name=str(row.get("name", "")),
        mode=str(row.get("mode", "prompt")),
        pipeline_mode=str(pipeline.get("mode", "")),
    )


def _end_outcome(chat: _Chat) -> EndOutcome:
    return EndOutcome(
        session_id=chat.session_id,
        turns=chat.turn_index,
        url=f"/console/sessions/{chat.session_id}",
    )


async def _quiet_close(transport: RoomTransport) -> None:
    try:
        await transport.close()
    except Exception:  # noqa: BLE001 - closing must never mask the real outcome
        logger.debug("transport close failed", exc_info=True)
