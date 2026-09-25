"""The "Test model" probe tree, offline (docs/v4/CUSTOM-MODELS.md D-V4-26, R-V4-25).

Every vendor is an ``httpx.MockTransport`` (or a fake websocket); no probe here
reaches a network, and every "key" is a placeholder. The request bodies are
asserted against the budgets: ``max_tokens`` 4, ``temperature`` 0, the forced
tool on the second call, the 1x1 PNG only when ``vision`` is asked for, TTS
``input="Hello."``, STT multipart carrying the bundled clip, embeddings
``input="ping"``.
"""

from __future__ import annotations

import io
import json
import wave
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from lkap_contracts.providers import REGISTRY, get

from lkap_api.custom_models.probes import PROBES, ProbeContext, ProbeInputError
from lkap_api.custom_models.probes import realtime as realtime_probes
from lkap_api.custom_models.probes.base import MAX_TOKENS, PNG_1X1_BASE64, stt_clip

PLACEHOLDER_KEY = "placeholder-vendor-key-0000"
Handler = Callable[[httpx.Request], httpx.Response]


def _ctx(
    provider_id: str,
    model: str,
    handler: Handler,
    *,
    probes: frozenset[str] = frozenset({"basic", "tools"}),
    fields: dict[str, Any] | None = None,
    ws: Any = None,
    base_url: str | None = None,
) -> ProbeContext:
    return ProbeContext(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        spec=get(provider_id),
        model=model,
        secrets={"api_key": PLACEHOLDER_KEY},
        fields=fields or {},
        probes=probes | {"basic"},
        base_url=base_url,
        ws=ws,
    )


def _body(request: httpx.Request) -> Any:
    return json.loads(request.content)


def _chat_answer(request: httpx.Request) -> httpx.Response:
    body = _body(request)
    message: dict[str, Any] = {"role": "assistant", "content": "ok"}
    if body.get("tools"):
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "ping", "arguments": "{}"}}],
        }
    return httpx.Response(
        200,
        json={
            "choices": [{"message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 1},
        },
    )


# --------------------------------------------------------------------------- registry
@pytest.mark.parametrize("spec", [s for s in REGISTRY if s.probe], ids=lambda s: s.id)
def test_every_registry_probe_names_an_adapter(spec: Any) -> None:
    assert spec.probe in PROBES


def test_the_stt_clip_is_one_second_of_16k_mono_pcm() -> None:
    raw = stt_clip()
    with wave.open(io.BytesIO(raw)) as clip:
        assert clip.getnchannels() == 1
        assert clip.getsampwidth() == 2
        assert clip.getframerate() == 16_000
        assert clip.getnframes() == 16_000
    assert len(raw) <= 40_000


def test_no_probe_ever_calls_an_images_endpoint() -> None:
    probes_dir = Path(__file__).resolve().parents[1] / "src" / "lkap_api" / "custom_models"
    for path in probes_dir.rglob("*.py"):
        text = path.read_text()
        assert "/images" not in text, path
        assert "generations" not in text, path


# ------------------------------------------------------------------------------- llm
async def test_openai_chat_sends_the_budgeted_body_and_detects_tools() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _chat_answer(request)

    outcome = await PROBES["openai_chat"].run(_ctx("openai-llm", "gpt-4.1-nano-2026", handler))

    assert outcome.ok is True
    assert outcome.detected.tools is True
    assert outcome.sample == "ok"
    assert outcome.latency_ms is not None
    assert len(seen) == 2
    first, second = (_body(r) for r in seen)
    assert str(seen[0].url) == "https://api.openai.com/v1/chat/completions"
    assert seen[0].headers["authorization"] == f"Bearer {PLACEHOLDER_KEY}"
    assert first["max_tokens"] <= MAX_TOKENS and first["temperature"] == 0 and first["stream"] is False
    assert first["model"] == "gpt-4.1-nano-2026"
    assert "tools" not in first
    assert second["tools"][0]["function"]["name"] == "ping"
    assert second["tools"][0]["function"]["parameters"] == {"type": "object", "properties": {}}
    assert second["tool_choice"] == "required"
    assert PNG_1X1_BASE64 not in json.dumps(first) + json.dumps(second)
    assert outcome.usage.tokens_in == 24 and outcome.usage.tokens_out == 2


async def test_the_png_is_sent_only_when_vision_is_requested() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _chat_answer(request)

    outcome = await PROBES["openai_chat"].run(
        _ctx("openrouter-llm", "openai/gpt-4.1-mini", handler, probes=frozenset({"vision"}))
    )

    assert outcome.detected.vision is True and outcome.detected.tools is None
    assert len(seen) == 2
    assert str(seen[0].url).startswith("https://openrouter.ai/api/v1/")
    vision = _body(seen[1])
    assert vision["max_tokens"] <= MAX_TOKENS
    assert PNG_1X1_BASE64 in json.dumps(vision["messages"])


async def test_a_refused_image_marks_vision_false_without_failing_the_test() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "image_url" in request.content.decode():
            return httpx.Response(400, json={"error": {"message": "this model does not support images"}})
        return _chat_answer(request)

    outcome = await PROBES["openai_chat"].run(
        _ctx("openai-llm", "gpt-4.1-nano", handler, probes=frozenset({"vision"}))
    )

    assert outcome.ok is True
    assert outcome.detected.vision is False


async def test_a_404_naming_the_model_is_a_failure_with_the_vendor_reason() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "The model `nope` does not exist"}})

    outcome = await PROBES["openai_chat"].run(_ctx("openai-llm", "nope", handler))

    assert outcome.ok is False
    assert outcome.results[0].message is not None
    assert outcome.results[0].message.startswith("HTTP 404")
    assert "does not exist" in outcome.results[0].message


async def test_a_200_carrying_an_error_body_fails_with_the_vendor_reason() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"error": {"message": "No endpoints found for acme/nope", "code": 404}}
        )

    outcome = await PROBES["openai_chat"].run(_ctx("openrouter-llm", "acme/nope", handler))

    assert outcome.ok is False
    assert outcome.results[0].message is not None
    assert "No endpoints found" in outcome.results[0].message


async def test_a_reasoning_model_is_retried_once_with_max_completion_tokens() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _body(request)
        seen.append(body)
        if "max_tokens" in body:
            return httpx.Response(
                400,
                json={
                    "error": {"message": "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens'."}
                },
            )
        return httpx.Response(
            200, json={"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
        )

    outcome = await PROBES["openai_chat"].run(
        _ctx("openai-llm", "o9-mini", handler, probes=frozenset({"basic"}))
    )

    assert outcome.ok is True, "a 2xx with an empty answer inside the budget still proves the id"
    assert [("max_tokens" in b, b.get("max_completion_tokens")) for b in seen] == [(True, None), (False, 4)]
    assert "temperature" not in seen[1]


async def test_a_truncated_tool_answer_is_inconclusive_not_false() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "o"}, "finish_reason": "length"}]}
        )

    outcome = await PROBES["openai_chat"].run(_ctx("groq-llm", "llama-4-scout", handler))

    assert outcome.ok is True
    assert outcome.detected.tools is None
    assert outcome.results[1].ok is None


def _refuse_tools(status: int, message: str) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        if _body(request).get("tools"):
            return httpx.Response(status, json={"error": {"message": message}})
        return _chat_answer(request)

    return handler


async def test_a_tool_call_refused_for_the_output_limit_is_inconclusive_not_false() -> None:
    """Ask #65: LiveKit Inference's gpt-4o-mini answered the 4-token tool call with this 400."""
    seen: list[dict[str, Any]] = []
    refuse = _refuse_tools(
        400, "Could not finish the message because max_tokens or model output limit was reached."
    )

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(_body(request))
        return refuse(request)

    outcome = await PROBES["openai_chat"].run(_ctx("livekit-inference-llm", "openai/gpt-4o-mini", handler))

    assert outcome.ok is True
    assert outcome.detected.tools is None
    assert outcome.results[1].ok is None
    assert outcome.results[1].message is not None and "(inconclusive)" in outcome.results[1].message
    assert len(seen) == 2, "the reasoning-shape retry must not fire on an output-limit 400"
    assert all(body.get("max_tokens", body.get("max_completion_tokens")) <= MAX_TOKENS for body in seen)


@pytest.mark.parametrize(
    ("status", "message", "expected"),
    [
        (400, "This model does not support tools.", False),
        (404, "No endpoints found that support tool use.", False),
        (400, "tool_choice 'required' is not supported by this model", False),
        (422, "Function calling is not enabled for this model", False),
        (400, "max_tokens is too small for a tool call", None),
        (400, "Invalid request: something else went wrong", None),
        (429, "Rate limit exceeded for tools", None),
        (500, "internal error", None),
    ],
)
async def test_only_a_refusal_naming_tools_reads_as_no_tools(
    status: int, message: str, expected: bool | None
) -> None:
    outcome = await PROBES["openai_chat"].run(
        _ctx("groq-llm", "llama-4-scout", _refuse_tools(status, message))
    )

    assert outcome.ok is True
    assert outcome.detected.tools is expected
    assert outcome.results[1].ok is expected


async def test_openai_compatible_needs_a_base_url() -> None:
    with pytest.raises(ProbeInputError):
        await PROBES["openai_chat"].run(_ctx("openai-compatible-llm", "my-model", _chat_answer))


async def test_a_base_url_override_is_used() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return _chat_answer(request)

    await PROBES["openai_chat"].run(
        _ctx("openai-compatible-llm", "my-model", handler, base_url="https://llm.example.com/v1/")
    )

    assert seen[0] == "https://llm.example.com/v1/chat/completions"


async def test_anthropic_forces_the_ping_tool() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = _body(request)
        content: list[dict[str, Any]] = [{"type": "text", "text": "ok"}]
        if body.get("tools"):
            content = [{"type": "tool_use", "id": "t1", "name": "ping", "input": {}}]
        return httpx.Response(
            200,
            json={
                "content": content,
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 9, "output_tokens": 1},
            },
        )

    outcome = await PROBES["anthropic_messages"].run(_ctx("anthropic-llm", "claude-sonnet-4-6", handler))

    assert outcome.ok is True and outcome.detected.tools is True
    first, second = (_body(r) for r in seen)
    assert seen[0].headers["x-api-key"] == PLACEHOLDER_KEY
    assert seen[0].headers["anthropic-version"]
    assert first["max_tokens"] <= MAX_TOKENS and first["temperature"] == 0
    assert second["tool_choice"] == {"type": "tool", "name": "ping"}
    assert second["tools"][0]["input_schema"] == {"type": "object", "properties": {}}


async def test_gemini_forces_function_calling_and_keeps_the_key_out_of_the_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = _body(request)
        parts: list[dict[str, Any]] = [{"text": "ok"}]
        if body.get("tools"):
            parts = [{"functionCall": {"name": "ping", "args": {}}}]
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": parts}, "finishReason": "STOP"}]}
        )

    outcome = await PROBES["gemini_generate"].run(_ctx("google-llm", "gemini-2.5-flash-lite", handler))

    assert outcome.ok is True and outcome.detected.tools is True
    assert seen[0].url.path == "/v1beta/models/gemini-2.5-flash-lite:generateContent"
    assert PLACEHOLDER_KEY not in str(seen[0].url)
    assert seen[0].headers["x-goog-api-key"] == PLACEHOLDER_KEY
    first, second = (_body(r) for r in seen)
    assert first["generationConfig"] == {"maxOutputTokens": MAX_TOKENS, "temperature": 0}
    assert second["toolConfig"] == {"functionCallingConfig": {"mode": "ANY"}}


# ------------------------------------------------------------------------------- stt
async def test_openai_transcriptions_posts_the_clip_as_multipart() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": "Okay."})

    outcome = await PROBES["openai_transcriptions"].run(
        _ctx("openrouter-stt", "openai/gpt-4o-mini-transcribe", handler)
    )

    assert outcome.ok is True and outcome.detected.audio_in is True
    assert outcome.sample == "Okay."
    request = seen[0]
    assert str(request.url) == "https://openrouter.ai/api/v1/audio/transcriptions"
    assert request.headers["content-type"].startswith("multipart/form-data")
    assert stt_clip() in request.content
    assert b'name="model"' in request.content and b"openai/gpt-4o-mini-transcribe" in request.content


async def test_deepgram_listen_sends_the_raw_clip() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"results": {"channels": [{"alternatives": [{"transcript": "okay"}]}]}}
        )

    outcome = await PROBES["deepgram_listen"].run(_ctx("deepgram-stt", "nova-3", handler))

    assert outcome.ok is True and outcome.sample == "okay"
    assert seen[0].url.params["model"] == "nova-3"
    assert seen[0].headers["authorization"] == f"Token {PLACEHOLDER_KEY}"
    assert seen[0].content == stt_clip()


@pytest.mark.parametrize(
    ("probe", "provider_id"), [("elevenlabs_stt", "elevenlabs-stt"), ("cartesia_stt", "cartesia-stt")]
)
async def test_multipart_stt_probes_carry_the_clip(probe: str, provider_id: str) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": "okay"})

    outcome = await PROBES[probe].run(_ctx(provider_id, "scribe-or-ink", handler))

    assert outcome.ok is True
    assert stt_clip() in seen[0].content


# ------------------------------------------------------------------------------- tts
def _audio(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "audio/mpeg"}, content=b"\xff\xfb" * 2048)


async def test_openai_speech_says_hello_and_reports_bytes_not_audio() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _audio(request)

    outcome = await PROBES["openai_speech"].run(_ctx("openai-tts", "gpt-4o-mini-tts", handler))

    assert outcome.ok is True and outcome.detected.audio_out is True
    body = _body(seen[0])
    assert body["input"] == "Hello."
    assert body["voice"] == "ash", "the registry field default"
    assert body["response_format"] == "mp3"
    assert outcome.sample is None
    assert outcome.message is not None and "4096 bytes of audio/mpeg" in outcome.message


async def test_a_tiny_or_non_audio_answer_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/json"}, content=b"{}")

    outcome = await PROBES["deepgram_speak"].run(_ctx("deepgram-tts", "aura-2-thalia-en", handler))

    assert outcome.ok is False


_GEMINI_TTS_REFUSAL = 'Gemini TTS only supports response_format="pcm". Got "mp3".'


async def test_openai_speech_retries_once_with_pcm_when_the_vendor_refuses_mp3() -> None:
    """Ask #64: OpenRouter's Gemini TTS takes only pcm, and answers pcm bytes."""
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _body(request)
        seen.append(body)
        if body["response_format"] != "pcm":
            return httpx.Response(400, json={"error": {"message": _GEMINI_TTS_REFUSAL}})
        return httpx.Response(200, headers={"content-type": "audio/pcm"}, content=b"\x00\x01" * 4000)

    outcome = await PROBES["openai_speech"].run(
        _ctx("openrouter-tts", "google/gemini-3.8-flash-tts", handler, fields={"voice": "Kore"})
    )

    assert outcome.ok is True and outcome.detected.audio_out is True
    assert [b["response_format"] for b in seen] == ["mp3", "pcm"]
    assert all(b["input"] == "Hello." and b["voice"] == "Kore" for b in seen)
    assert outcome.message is not None and "8000 bytes of audio/pcm" in outcome.message
    assert "retried with pcm" in outcome.message
    assert len(outcome.results) == 1 and outcome.results[0].ok is True
    assert outcome.usage.chars == len("Hello.")


@pytest.mark.parametrize("content_type", ["application/octet-stream", None])
async def test_untyped_or_octet_stream_pcm_counts_as_audio(content_type: str | None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if _body(request)["response_format"] != "pcm":
            return httpx.Response(400, json={"error": {"message": _GEMINI_TTS_REFUSAL}})
        headers = {"content-type": content_type} if content_type else {}
        return httpx.Response(200, headers=headers, content=b"\x00" * 4096)

    outcome = await PROBES["openai_speech"].run(
        _ctx("openrouter-tts", "google/gemini-3.8-flash-tts", handler, fields={"voice": "Kore"})
    )

    assert outcome.ok is True


async def test_an_octet_stream_answer_to_an_mp3_request_is_still_not_speech() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "application/octet-stream"}, content=b"\x00" * 4096
        )

    outcome = await PROBES["openai_speech"].run(
        _ctx("openrouter-tts", "deepgram/aura-2", handler, fields={"voice": "aura-2-thalia-en"})
    )

    assert outcome.ok is False


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (400, "Invalid voice 'Nobody' for this model"),
        (404, "google/nope-tts is not a valid model ID"),
        (401, "pcm"),
    ],
)
async def test_a_refusal_that_does_not_name_the_format_is_not_retried(status: int, message: str) -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(_body(request))
        return httpx.Response(status, json={"error": {"message": message}})

    outcome = await PROBES["openai_speech"].run(
        _ctx("openrouter-tts", "google/gemini-3.8-flash-tts", handler, fields={"voice": "Kore"})
    )

    assert outcome.ok is False
    assert len(seen) == 1


async def test_a_second_format_refusal_fails_without_a_third_request() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(_body(request))
        return httpx.Response(400, json={"error": {"message": "unsupported response_format"}})

    outcome = await PROBES["openai_speech"].run(
        _ctx("openrouter-tts", "google/gemini-3.8-flash-tts", handler, fields={"voice": "Kore"})
    )

    assert outcome.ok is False
    assert [b["response_format"] for b in seen] == ["mp3", "pcm"]


async def test_deepgram_speak_names_the_model_in_the_query() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _audio(request)

    outcome = await PROBES["deepgram_speak"].run(_ctx("deepgram-tts", "aura-2-thalia-en", handler))

    assert outcome.ok is True
    assert seen[0].url.params["model"] == "aura-2-thalia-en"
    assert _body(seen[0]) == {"text": "Hello."}


async def test_elevenlabs_tts_needs_the_voice_and_puts_it_in_the_path() -> None:
    with pytest.raises(ProbeInputError):
        await PROBES["elevenlabs_tts"].run(_ctx("elevenlabs-tts", "eleven_turbo_v2_5", _audio))

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _audio(request)

    outcome = await PROBES["elevenlabs_tts"].run(
        _ctx("elevenlabs-tts", "eleven_turbo_v2_5", handler, fields={"voice_id": "21m00Tcm4TlvDq8ikWAM"})
    )
    assert outcome.ok is True
    assert seen[0].url.path == "/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM"
    assert _body(seen[0]) == {"text": "Hello.", "model_id": "eleven_turbo_v2_5"}


async def test_cartesia_tts_sends_the_transcript_and_voice() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _audio(request)

    outcome = await PROBES["cartesia_tts"].run(
        _ctx("cartesia-tts", "sonic-3", handler, fields={"voice": "a0e99841-438c-4a64-b679-ae501e7d6091"})
    )

    assert outcome.ok is True
    body = _body(seen[0])
    assert body["transcript"] == "Hello." and body["model_id"] == "sonic-3"
    assert body["voice"] == {"mode": "id", "id": "a0e99841-438c-4a64-b679-ae501e7d6091"}


# ------------------------------------------------------------------------ embeddings
async def test_openai_embeddings_embed_ping_and_report_the_dimension() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"data": [{"embedding": [0.1] * 1536}], "usage": {"prompt_tokens": 1}}
        )

    outcome = await PROBES["openai_embeddings"].run(
        _ctx("openai-embedding", "text-embedding-3-small", handler)
    )

    assert outcome.ok is True
    assert _body(seen[0]) == {"model": "text-embedding-3-small", "input": "ping"}
    assert outcome.message == "one vector of dimension 1536"
    assert outcome.detected.context_tokens is None


# -------------------------------------------------------------------------- realtime
class FakeWs:
    """A recorded websocket peer: the frames it answers with, and what it was sent."""

    def __init__(self, replies: list[dict[str, Any]], *, silent: bool = False) -> None:
        self.replies = list(replies)
        self.silent = silent
        self.sent: list[Mapping[str, Any]] = []
        self.opened: list[tuple[str, dict[str, str]]] = []

    @asynccontextmanager
    async def connect(self, url: str, headers: Mapping[str, str]) -> AsyncIterator[FakeWs]:
        self.opened.append((url, dict(headers)))
        yield self

    async def send_json(self, data: Mapping[str, Any]) -> None:
        self.sent.append(data)

    async def receive_json(self) -> dict[str, Any]:
        if self.silent:
            import asyncio

            await asyncio.sleep(3600)
        if not self.replies:
            raise ConnectionError("closed")
        return self.replies.pop(0)


def _no_http(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"a realtime probe made an HTTP request: {request.url}")


async def test_openai_realtime_completes_on_session_created_without_audio() -> None:
    ws = FakeWs([{"type": "session.created", "session": {"id": "s1"}}])

    outcome = await PROBES["openai_realtime_ws"].run(_ctx("openai-realtime", "gpt-realtime", _no_http, ws=ws))

    assert outcome.ok is True and outcome.detected.audio_in is True and outcome.detected.audio_out is True
    url, headers = ws.opened[0]
    assert url == "wss://api.openai.com/v1/realtime?model=gpt-realtime"
    assert headers["Authorization"] == f"Bearer {PLACEHOLDER_KEY}"
    assert ws.sent == [], "no audio, no frame: the server speaks first"


async def test_gemini_live_sends_setup_and_waits_for_setup_complete() -> None:
    ws = FakeWs([{"setupComplete": {}}])

    outcome = await PROBES["gemini_live_ws"].run(_ctx("google-realtime", "gemini-3.8-live", _no_http, ws=ws))

    assert outcome.ok is True
    url, headers = ws.opened[0]
    assert PLACEHOLDER_KEY not in url
    assert headers["x-goog-api-key"] == PLACEHOLDER_KEY
    assert ws.sent == [
        {"setup": {"model": "models/gemini-3.8-live", "generationConfig": {"responseModalities": ["AUDIO"]}}}
    ]


async def test_a_vendor_error_frame_fails_the_handshake() -> None:
    ws = FakeWs([{"type": "error", "error": {"message": "model not found"}}])

    outcome = await PROBES["xai_realtime_ws"].run(_ctx("xai-realtime", "grok-nope", _no_http, ws=ws))

    assert outcome.ok is False
    assert outcome.message is not None and "model not found" in outcome.message


async def test_silence_fails_the_handshake_after_the_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(realtime_probes, "HANDSHAKE_TIMEOUT_S", 0.05)

    outcome = await PROBES["openai_realtime_ws"].run(
        _ctx("openai-realtime", "gpt-realtime", _no_http, ws=FakeWs([], silent=True))
    )

    assert outcome.ok is False
    assert outcome.message is not None and "no handshake reply" in outcome.message


# --------------------------------------------------------------------------- avatars
async def test_bey_gets_the_avatar_and_never_posts_a_session() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "b9be11b8-89fb-4227-8f86-4a881393cbdb"})

    outcome = await PROBES["bey_avatar_get"].run(
        _ctx("bey-avatar", "b9be11b8-89fb-4227-8f86-4a881393cbdb", handler)
    )

    assert outcome.ok is True
    assert [r.method for r in seen] == ["GET"]
    assert seen[0].url.path == "/v1/avatars/b9be11b8-89fb-4227-8f86-4a881393cbdb"
    assert "session" not in str(seen[0].url)


def _simli_faces(request: httpx.Request) -> httpx.Response:
    # The `GET /faces` shape of https://api.simli.ai/openapi.yaml: the account's own faces.
    assert request.method == "GET"
    assert request.url.path == "/faces"
    face = {"owner_id": "owner-1", "simli_version": 1, "created_at": "2026-09-01T00:00:00Z"}
    return httpx.Response(
        200, json=[{**face, "id": "face-a", "updated_at": "x"}, {**face, "id": "face-b", "updated_at": "x"}]
    )


async def test_simli_passes_a_face_in_the_accounts_own_list() -> None:
    outcome = await PROBES["simli_face_member"].run(_ctx("simli-avatar", "face-b", _simli_faces))

    assert outcome.ok is True


async def test_simli_reads_an_unlisted_face_as_inconclusive_not_failed() -> None:
    """Ask #67: preset faces (the Survey agent's "Tina") are not in the account's list."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _simli_faces(request)

    outcome = await PROBES["simli_face_member"].run(
        _ctx("simli-avatar", "cace3ef7-a4c4-425d-a8cf-a5358eb0c427", handler)
    )

    assert outcome.ok is None
    assert outcome.results[0].ok is None
    assert outcome.message is not None
    assert "can't be verified without starting a session" in outcome.message
    assert "2 listed" in outcome.message
    assert [r.method for r in seen] == ["GET"], "never a session"


async def test_simli_an_empty_list_is_inconclusive_and_a_refused_key_fails() -> None:
    def empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    def refused(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "Invalid API key"})

    assert (await PROBES["simli_face_member"].run(_ctx("simli-avatar", "face-z", empty))).ok is None
    assert (await PROBES["simli_face_member"].run(_ctx("simli-avatar", "face-z", refused))).ok is False
