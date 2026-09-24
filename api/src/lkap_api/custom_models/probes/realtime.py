"""Realtime probes: a websocket handshake, no audio (D-V4-26).

* ``openai_realtime_ws``: open ``wss://…/v1/realtime?model=<id>`` with a
  bearer key and wait for the server's ``session.created`` (the installed
  ``livekit-plugins-openai`` builds the same url and headers).
* ``xai_realtime_ws``: the same flow against xAI (UNVERIFIED against a real
  key; the live check settles it).
* ``gemini_live_ws``: open the ``BidiGenerateContent`` socket with the key in
  ``x-goog-api-key`` (never the url), send ``setup`` with the model and wait
  for ``setupComplete`` (the frames ``google-genai``'s ``live.connect`` sends).

The socket closes as soon as the handshake completes; the probe fails on a
vendor ``error`` frame, a close, or :data:`HANDSHAKE_TIMEOUT_S` of silence.
The default :class:`AiohttpWsConnector` goes through the network guard.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import quote

import aiohttp
from lkap_contracts.api_models import ProbeResult
from lkap_contracts.providers import ModelCapabilities

from lkap_api import net_guard
from lkap_api.custom_models.probes.base import (
    ProbeContext,
    ProbeOutcome,
    WsConnector,
    WsSession,
    elapsed_ms,
)
from lkap_api.custom_models.probes.gemini import model_path

#: How long a realtime probe waits for the handshake reply.
HANDSHAKE_TIMEOUT_S = 10.0

OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime"
XAI_REALTIME_URL = "wss://api.x.ai/v1/realtime"
GEMINI_LIVE_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)


class _AiohttpSession:
    def __init__(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self._ws = ws

    async def send_json(self, data: Mapping[str, Any]) -> None:
        await self._ws.send_str(json.dumps(dict(data)))

    async def receive_json(self) -> dict[str, Any]:
        message = await self._ws.receive()
        if message.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
            raw = message.data if isinstance(message.data, str) else bytes(message.data).decode("utf-8")
            decoded = json.loads(raw)
            return decoded if isinstance(decoded, dict) else {}
        raise ConnectionError(f"the socket closed ({message.type.name})")


class AiohttpWsConnector:
    """Opens a websocket through :func:`lkap_api.net_guard.guarded_aiohttp_session`."""

    def __init__(self, policy: net_guard.NetPolicy) -> None:
        self._policy = policy

    @asynccontextmanager
    async def connect(self, url: str, headers: Mapping[str, str]) -> AsyncIterator[WsSession]:
        """Check ``url`` against the guard, then open it (redirects are never followed)."""
        problem = net_guard.check_url(url, self._policy, schemes=net_guard.LIVEKIT_SCHEMES)
        if problem is not None:
            raise net_guard.BlockedDestinationError(f"blocked destination: {problem}")
        timeout = aiohttp.ClientTimeout(total=None, connect=HANDSHAKE_TIMEOUT_S)
        session = net_guard.guarded_aiohttp_session(self._policy, timeout=timeout)
        try:
            async with session.ws_connect(url, headers=dict(headers), autoping=True) as ws:
                yield _AiohttpSession(ws)
        finally:
            await session.close()


def _ws_url(base: str) -> str:
    return "wss://" + base.removeprefix("https://") if base.startswith("https://") else base


class _HandshakeProbe:
    """Open, optionally send one frame, wait for the frame that proves the session exists."""

    name = "realtime"

    def url(self, ctx: ProbeContext) -> str:
        raise NotImplementedError

    def headers(self, ctx: ProbeContext) -> dict[str, str]:
        raise NotImplementedError

    def first_frame(self, ctx: ProbeContext) -> dict[str, Any] | None:
        return None

    def done(self, frame: Mapping[str, Any]) -> bool:
        raise NotImplementedError

    async def run(self, ctx: ProbeContext) -> ProbeOutcome:
        """Complete the handshake within :data:`HANDSHAKE_TIMEOUT_S`, without sending audio."""
        if ctx.ws is None:
            raise RuntimeError("a realtime probe needs a websocket connector")
        connector: WsConnector = ctx.ws
        start = time.perf_counter()
        try:
            async with connector.connect(self.url(ctx), self.headers(ctx)) as ws:
                frame = self.first_frame(ctx)
                if frame is not None:
                    await ws.send_json(frame)
                message = await asyncio.wait_for(self._await_done(ws), HANDSHAKE_TIMEOUT_S)
        except TimeoutError:
            message = f"no handshake reply within {HANDSHAKE_TIMEOUT_S:g} s"
            return _fail(message, elapsed_ms(start))
        except ConnectionError as exc:
            return _fail(f"the socket closed before the handshake: {exc}", elapsed_ms(start))
        except aiohttp.WSServerHandshakeError as exc:
            return _fail(f"HTTP {exc.status}: the vendor refused the websocket", elapsed_ms(start))
        if message is not None:
            return _fail(message, elapsed_ms(start))
        latency = elapsed_ms(start)
        done = "the realtime session opened"
        return ProbeOutcome(
            ok=True,
            results=[ProbeResult(name="basic", ok=True, latency_ms=latency, message=done)],
            detected=ModelCapabilities(audio_in=True, audio_out=True),
            message=done,
        )

    async def _await_done(self, ws: WsSession) -> str | None:
        while True:
            frame = await ws.receive_json()
            error = frame.get("error")
            if error is not None or frame.get("type") == "error":
                detail = error.get("message") if isinstance(error, dict) else error
                return f"the vendor refused the session: {detail or 'error'}"
            if self.done(frame):
                return None


def _fail(message: str, latency_ms: int) -> ProbeOutcome:
    return ProbeOutcome(
        ok=False,
        results=[ProbeResult(name="basic", ok=False, latency_ms=latency_ms, message=message)],
        message=message,
    )


class OpenAiRealtimeProbe(_HandshakeProbe):
    """Wait for ``session.created`` on ``wss://api.openai.com/v1/realtime?model=<id>``."""

    name = "openai_realtime_ws"
    default_url = OPENAI_REALTIME_URL

    def url(self, ctx: ProbeContext) -> str:
        base = _ws_url(ctx.base_url.rstrip("/")) + "/realtime" if ctx.base_url else self.default_url
        return f"{base}?model={quote(ctx.model, safe='')}"

    def headers(self, ctx: ProbeContext) -> dict[str, str]:
        return {"Authorization": f"Bearer {ctx.api_key}"}

    def done(self, frame: Mapping[str, Any]) -> bool:
        return frame.get("type") == "session.created"


class XaiRealtimeProbe(OpenAiRealtimeProbe):
    """The OpenAI flow against ``wss://api.x.ai/v1/realtime`` (UNVERIFIED with a real key)."""

    name = "xai_realtime_ws"
    default_url = XAI_REALTIME_URL

    def done(self, frame: Mapping[str, Any]) -> bool:
        return frame.get("type") in ("session.created", "conversation.created")


class GeminiLiveProbe(_HandshakeProbe):
    """Send ``setup`` with the model; wait for ``setupComplete``."""

    name = "gemini_live_ws"

    def url(self, ctx: ProbeContext) -> str:
        return GEMINI_LIVE_URL

    def headers(self, ctx: ProbeContext) -> dict[str, str]:
        return {"x-goog-api-key": ctx.api_key}

    def first_frame(self, ctx: ProbeContext) -> dict[str, Any]:
        return {
            "setup": {
                "model": model_path(ctx.model),
                "generationConfig": {"responseModalities": ["AUDIO"]},
            }
        }

    def done(self, frame: Mapping[str, Any]) -> bool:
        return "setupComplete" in frame


__all__ = [
    "GEMINI_LIVE_URL",
    "HANDSHAKE_TIMEOUT_S",
    "OPENAI_REALTIME_URL",
    "XAI_REALTIME_URL",
    "AiohttpWsConnector",
    "GeminiLiveProbe",
    "OpenAiRealtimeProbe",
    "XaiRealtimeProbe",
]
