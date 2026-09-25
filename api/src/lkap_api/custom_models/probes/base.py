"""The "Test model" probe contract: context, outcome, budgets and shared helpers (D-V4-26, R-V4-25).

A probe is one capped, real vendor call (or a short sequence of them) that
answers "does this vendor accept this model id with this key?". It is a
second adapter tree, deliberately separate from the catalog adapters
(``ProviderSpec.test == catalog.adapter`` is a GET that lists; a probe is a
POST with a payload and a budget).

Budgets are fixed here and asserted by the tests: ``max_tokens`` 4 (16 on the
``tools`` call, R-V4-41), TTS input
``"Hello."``, the bundled 1 s STT clip, embeddings ``"ping"``, a realtime
websocket handshake without audio, a session-less avatar GET, and **no image
generation anywhere** (images cost money; an image model is checked against
the catalog only).

Probes receive the credential's decrypted, normalised secret bag
(``secrets["api_key"]`` is the primary secret, or the LiveKit Inference JWT)
and return only structured results. They never log a body or a header;
:mod:`lkap_api.custom_models.service` scrubs every message and sample against
the secret values before anything is stored, returned or logged.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from importlib import resources
from typing import Any, Protocol

import httpx
from lkap_contracts.api_models import ProbeResult
from lkap_contracts.providers import ModelCapabilities, ProviderSpec

#: The one prompt every LLM probe sends.
LLM_PROMPT = "Reply with the single word ok."
#: ``max_tokens`` (or the vendor's equivalent) on the ``basic`` and ``vision`` LLM calls (R-V4-25: ≤ 4).
MAX_TOKENS = 4
#: ``max_tokens`` (or the vendor's equivalent) on the ``tools`` LLM call: a forced function call
#: does not fit in 4 output tokens for several tokenisers (R-V4-41, ask #78).
TOOLS_MAX_TOKENS = 16
#: What every TTS probe speaks (≤ 8 characters, D-V4-26).
TTS_INPUT = "Hello."
#: What every embedding probe embeds.
EMBED_INPUT = "ping"
#: The tool the ``tools`` probe forces.
PING_TOOL_NAME = "ping"
PING_TOOL_DESCRIPTION = "Call this to answer the probe."
#: A 1x1 transparent PNG, sent only when the ``vision`` probe is requested.
PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
#: The shortest audio body a TTS probe accepts as speech.
MIN_AUDIO_BYTES = 1024
#: Most audio bytes a TTS probe reads before it stops (it reports a count, never the bytes).
MAX_AUDIO_BYTES = 2_000_000
#: Content types a raw PCM answer may carry (besides ``audio/*``): headerless samples.
RAW_PCM_TYPES = frozenset({"application/octet-stream", "binary/octet-stream", ""})
#: Longest ``sample`` kept (the LLM's word, the STT transcript).
SAMPLE_MAX = 200
#: Longest slice of a vendor error body quoted in a probe message (before scrubbing).
VENDOR_TEXT_MAX = 300
#: Seconds of audio in the bundled STT clip.
STT_CLIP_SECONDS = 1.0


def token_budget(variant: str) -> int:
    """The output budget of one LLM probe call: 16 for ``tools``, 4 otherwise (R-V4-41)."""
    return TOOLS_MAX_TOKENS if variant == "tools" else MAX_TOKENS


def stt_clip() -> bytes:
    """The bundled 1.0 s, 16 kHz mono 16-bit PCM WAVE clip (``fixtures/ok_1s_16k.wav``)."""
    return resources.files("lkap_api.custom_models.probes").joinpath("fixtures/ok_1s_16k.wav").read_bytes()


class ProbeInputError(Exception):
    """The probe needs a slot field the request did not carry (the route answers 422, value-free)."""


@dataclass
class Usage:
    """What a probe consumed, for the cost estimate."""

    tokens_in: int = 0
    tokens_out: int = 0
    chars: int = 0
    audio_s_in: float = 0.0

    def is_empty(self) -> bool:
        """Whether nothing billable was recorded."""
        return not (self.tokens_in or self.tokens_out or self.chars or self.audio_s_in)


class WsSession(Protocol):
    """One open websocket, as the realtime probes use it (JSON frames only)."""

    async def send_json(self, data: Mapping[str, Any]) -> None:
        """Send one JSON frame."""
        ...

    async def receive_json(self) -> dict[str, Any]:
        """The next JSON frame (text or binary); raises ``ConnectionError`` once the socket closed."""
        ...


class WsConnector(Protocol):
    """Opens a websocket behind the network guard (tests inject a fake)."""

    def connect(self, url: str, headers: Mapping[str, str]) -> AbstractAsyncContextManager[WsSession]:
        """Open ``url`` with ``headers``; the context closes it."""
        ...


@dataclass
class ProbeContext:
    """Everything one probe run may use. **Contains a secret** (``secrets``); never log it."""

    client: httpx.AsyncClient
    spec: ProviderSpec
    model: str
    secrets: Mapping[str, str] = field(repr=False)
    fields: Mapping[str, Any] = field(default_factory=dict)
    probes: frozenset[str] = frozenset({"basic", "tools"})
    base_url: str | None = None
    ws: WsConnector | None = None
    state: dict[str, Any] = field(default_factory=dict)
    """Per-run scratch a probe may keep between its calls (e.g. a parameter the vendor refused)."""

    @property
    def api_key(self) -> str:
        """The primary secret (or the LiveKit Inference JWT)."""
        return self.secrets.get("api_key", "")

    def field_str(self, name: str) -> str | None:
        """A non-empty string field of the slot, else the registry field's string default."""
        value = self.fields.get(name)
        if isinstance(value, str) and value:
            return value
        for spec_field in self.spec.fields:
            if spec_field.name == name and isinstance(spec_field.default, str) and spec_field.default:
                return spec_field.default
        return None


@dataclass
class ProbeOutcome:
    """What a probe found. Messages and ``sample`` are unscrubbed until the service scrubs them."""

    ok: bool | None
    results: list[ProbeResult] = field(default_factory=list)
    detected: ModelCapabilities = field(default_factory=ModelCapabilities)
    sample: str | None = None
    message: str | None = None
    usage: Usage = field(default_factory=Usage)

    @property
    def latency_ms(self) -> int | None:
        """The first call's latency (the ``basic`` probe, or a TTS request's time to first byte)."""
        return self.results[0].latency_ms if self.results else None


class Probe(Protocol):
    """One probe adapter, parameterised by wire shape (``openai_chat`` serves ten vendors)."""

    name: str

    async def run(self, ctx: ProbeContext) -> ProbeOutcome:
        """Probe ``ctx.model`` with ``ctx.secrets``; never raises for a vendor answer (only transport)."""
        ...


# ---------------------------------------------------------------------------- helpers
def as_dict(value: Any) -> dict[str, Any]:
    """``value`` when it is a JSON object, else an empty one."""
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    """``value`` when it is a JSON array, else an empty one."""
    return value if isinstance(value, list) else []


def positive_int(value: Any) -> int:
    """``value`` when it is a positive integer (a token count), else 0."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def elapsed_ms(start: float) -> int:
    """Milliseconds since ``start`` (a :func:`time.perf_counter` reading)."""
    return int((time.perf_counter() - start) * 1000)


def vendor_text(response: httpx.Response) -> str:
    """A short slice of a vendor answer for a message (the service scrubs it before use)."""
    try:
        body = response.json()
    except ValueError:
        text = response.text if response.headers.get("content-type", "").startswith(("text/", "app")) else ""
        return " ".join(text.split())[:VENDOR_TEXT_MAX]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        message = error.get("message") or error.get("code") or error.get("type")
        if message:
            return str(message)[:VENDOR_TEXT_MAX]
    if isinstance(error, str):
        return error[:VENDOR_TEXT_MAX]
    for key in ("message", "detail", "err_msg", "error_message"):
        value = body.get(key) if isinstance(body, dict) else None
        if isinstance(value, str | dict | list) and value:
            return str(value)[:VENDOR_TEXT_MAX]
    return " ".join(str(body).split())[:VENDOR_TEXT_MAX]


def failure(name: str, response: httpx.Response, latency_ms: int) -> ProbeResult:
    """The result of a non-2xx vendor answer: status plus the vendor's short reason."""
    reason = vendor_text(response)
    message = f"HTTP {response.status_code}: {reason}" if reason else f"HTTP {response.status_code}"
    return ProbeResult(name=name, ok=False, latency_ms=latency_ms, message=message)


def capability_from_status(status_code: int) -> bool | None:
    """What a refused optional call (vision) says about the capability.

    A 400/404/422 refusal means "not supported"; a 429, a 5xx or an auth error
    says nothing about the model, so the capability stays unknown. The ``tools``
    call is read more strictly by :func:`tools_from_refusal`.
    """
    if status_code in (400, 404, 422):
        return False
    return None


#: A refusal that names the output budget: the call was cut off, which says nothing
#: about the capability (``max_tokens`` 4 is shorter than some tool calls, ask #65).
_BUDGET_REFUSAL = re.compile(r"max_tokens|max_completion_tokens|output limit|token limit|finish_reason", re.I)
#: A refusal that names the tools parameter itself (the only reading that means "no tools").
_TOOLS_REFUSAL = re.compile(r"\btools?\b|tool_choice|tool use|tool[ _-]?call|function[ _-]?call", re.I)


def tools_from_refusal(status_code: int, reason: str) -> bool | None:
    """What a refused ``tools`` call says about tool support (D-V4-26, asks #60/#65).

    ``False`` only for a 400/404/422 whose reason names the tools parameter
    (``tools``, ``tool_choice``, tool use, function calling) and not the output
    budget. A refusal that names ``max_tokens`` or an output limit, any other
    4xx, a 429, a 5xx or an auth error is inconclusive (``None``): a truncated
    tool call must never read as "no tools".
    """
    if status_code not in (400, 404, 422) or _BUDGET_REFUSAL.search(reason):
        return None
    return False if _TOOLS_REFUSAL.search(reason) else None


def json_body(response: httpx.Response) -> Any:
    """The JSON body, or ``None`` when it is not JSON."""
    try:
        return response.json()
    except ValueError:
        return None


def clip(text: str | None) -> str | None:
    """``text`` cut to :data:`SAMPLE_MAX` characters, ``None`` when empty."""
    if not text:
        return None
    return text.strip()[:SAMPLE_MAX] or None


def base_url(ctx: ProbeContext, default: str | None) -> str:
    """The slot's ``base_url`` override (already checked by the net guard) or the vendor default.

    Raises:
        ProbeInputError: When neither exists (``openai-compatible-llm`` without a base URL).
    """
    chosen = ctx.base_url or default
    if not chosen:
        raise ProbeInputError(f"the '{ctx.spec.id}' probe needs the slot's 'base_url' field")
    return chosen.rstrip("/")


async def audio_probe(
    ctx: ProbeContext,
    url: str,
    *,
    headers: Mapping[str, str],
    params: Mapping[str, str] | None = None,
    json: Any = None,
    raw_audio_types: frozenset[str] = frozenset(),
) -> ProbeOutcome:
    """One TTS request: pass on 2xx, an audio content type and at least 1 KB of body.

    The content type is audio when it is ``audio/*`` or one of
    ``raw_audio_types`` (lower case; a probe that asked for raw PCM passes
    :data:`RAW_PCM_TYPES`, since a headerless stream may come back as
    ``application/octet-stream`` or untyped). The body is counted, never
    kept; ``latency_ms`` is the time to the response headers (TTFB).
    """
    start = time.perf_counter()
    async with ctx.client.stream("POST", url, headers=dict(headers), params=params, json=json) as response:
        ttfb = elapsed_ms(start)
        if not response.is_success:
            await response.aread()
            return ProbeOutcome(ok=False, results=[failure("basic", response, ttfb)])
        content_type = response.headers.get("content-type", "").split(";")[0].strip()
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size >= MAX_AUDIO_BYTES:
                break
    is_audio = content_type.lower().startswith("audio/") or content_type.lower() in raw_audio_types
    ok = is_audio and size >= MIN_AUDIO_BYTES
    described = f"{size} bytes of {content_type or 'an unknown type'}, first byte after {ttfb} ms"
    message = described if ok else f"the answer was not speech audio ({described})"
    return ProbeOutcome(
        ok=ok,
        results=[ProbeResult(name="basic", ok=ok, latency_ms=ttfb, message=message)],
        detected=ModelCapabilities(audio_out=True) if ok else ModelCapabilities(),
        message=message,
        usage=Usage(chars=len(TTS_INPUT)),
    )


def transcript_outcome(response: httpx.Response, latency_ms: int, transcript: str | None) -> ProbeOutcome:
    """The outcome of one STT request: pass on 2xx with a transcript field (any text)."""
    if not response.is_success:
        return ProbeOutcome(ok=False, results=[failure("basic", response, latency_ms)])
    if transcript is None:
        message = "the answer carried no transcript"
        return ProbeOutcome(
            ok=False, results=[ProbeResult(name="basic", ok=False, latency_ms=latency_ms, message=message)]
        )
    message = "transcribed the 1 s clip" if transcript.strip() else "answered with an empty transcript"
    return ProbeOutcome(
        ok=True,
        results=[ProbeResult(name="basic", ok=True, latency_ms=latency_ms, message=message)],
        detected=ModelCapabilities(audio_in=True),
        sample=clip(transcript),
        message=message,
        usage=Usage(audio_s_in=STT_CLIP_SECONDS),
    )


@dataclass
class ChatAnswer:
    """What one LLM answer said, read by a vendor-specific parser."""

    well_formed: bool
    text: str | None = None
    tool_called: bool = False
    truncated: bool = False
    tokens_in: int = 0
    tokens_out: int = 0


class LlmProbe:
    """The LLM probe sequence shared by every chat wire shape (D-V4-26).

    ``basic`` always runs (pass: 2xx and a well-formed answer — the text may be
    empty when a reasoning model spends the 4-token budget thinking); ``tools``
    forces one call of the ``ping`` tool; ``vision`` (opt-in) sends a 1x1 PNG.
    The optional calls never change ``ok``; they fill ``detected``.
    Subclasses implement :meth:`call` and :meth:`parse`.
    """

    name = "llm"

    async def call(self, ctx: ProbeContext, variant: str) -> tuple[httpx.Response, int]:
        """Send the ``basic``/``tools``/``vision`` request; return the answer and its latency."""
        raise NotImplementedError

    def parse(self, body: Any) -> ChatAnswer:
        """Read one 2xx answer body."""
        raise NotImplementedError

    async def run(self, ctx: ProbeContext) -> ProbeOutcome:
        """Run ``basic``, then the requested ``tools``/``vision`` calls."""
        usage = Usage()
        response, latency = await self.call(ctx, "basic")
        if not response.is_success:
            return ProbeOutcome(ok=False, results=[failure("basic", response, latency)])
        answer = self.parse(json_body(response))
        usage.tokens_in += answer.tokens_in
        usage.tokens_out += answer.tokens_out
        if not answer.well_formed:
            # OpenRouter (among others) can answer 200 with an `{"error": ...}` body.
            reason = vendor_text(response)
            message = "the vendor answered 2xx but not with a chat completion"
            if reason:
                message = f"{message}: {reason}"
            return ProbeOutcome(
                ok=False,
                results=[ProbeResult(name="basic", ok=False, latency_ms=latency, message=message)],
                usage=usage,
            )
        if answer.text or answer.tool_called:
            message = "answered"
        else:
            message = f"answered, with no text within the {MAX_TOKENS}-token budget"
        results = [ProbeResult(name="basic", ok=True, latency_ms=latency, message=message)]
        detected: dict[str, bool | None] = {}
        for variant in ("tools", "vision"):
            if variant not in ctx.probes:
                continue
            found, result = await self._optional(ctx, variant, usage)
            detected[variant] = found
            results.append(result)
        return ProbeOutcome(
            ok=True,
            results=results,
            detected=ModelCapabilities.model_validate(detected),
            sample=clip(answer.text),
            message=message,
            usage=usage,
        )

    async def _optional(
        self, ctx: ProbeContext, variant: str, usage: Usage
    ) -> tuple[bool | None, ProbeResult]:
        response, latency = await self.call(ctx, variant)
        if not response.is_success:
            refused = failure(variant, response, latency)
            if variant == "tools":
                found = tools_from_refusal(response.status_code, vendor_text(response))
                if found is None:
                    refused = refused.model_copy(update={"message": f"{refused.message} (inconclusive)"})
            else:
                found = capability_from_status(response.status_code)
            return found, refused.model_copy(update={"ok": found})
        answer = self.parse(json_body(response))
        usage.tokens_in += answer.tokens_in
        usage.tokens_out += answer.tokens_out
        if variant == "tools":
            if answer.tool_called:
                return True, ProbeResult(name="tools", ok=True, latency_ms=latency, message="called the tool")
            if answer.truncated:
                message = f"no tool call within the {TOOLS_MAX_TOKENS}-token budget (inconclusive)"
                return None, ProbeResult(name="tools", ok=None, latency_ms=latency, message=message)
            message = "answered without calling the forced tool"
            return False, ProbeResult(name="tools", ok=False, latency_ms=latency, message=message)
        return True, ProbeResult(name="vision", ok=True, latency_ms=latency, message="accepted an image")


__all__ = [
    "EMBED_INPUT",
    "LLM_PROMPT",
    "MAX_AUDIO_BYTES",
    "MAX_TOKENS",
    "MIN_AUDIO_BYTES",
    "PING_TOOL_DESCRIPTION",
    "PING_TOOL_NAME",
    "PNG_1X1_BASE64",
    "RAW_PCM_TYPES",
    "SAMPLE_MAX",
    "STT_CLIP_SECONDS",
    "TOOLS_MAX_TOKENS",
    "TTS_INPUT",
    "ChatAnswer",
    "LlmProbe",
    "Probe",
    "ProbeContext",
    "ProbeInputError",
    "ProbeOutcome",
    "Usage",
    "WsConnector",
    "WsSession",
    "audio_probe",
    "base_url",
    "capability_from_status",
    "clip",
    "elapsed_ms",
    "failure",
    "json_body",
    "stt_clip",
    "token_budget",
    "tools_from_refusal",
    "transcript_outcome",
    "vendor_text",
]
