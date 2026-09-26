"""OpenRouter speech adapters (docs/v5/_briefs/openrouter-voice-diagnosis.md).

The ``openrouter-tts`` registry entry points its ``python_class`` here instead of
at ``livekit.plugins.openai.TTS``. OpenRouter's ``/audio/speech`` is
OpenAI-compatible on the request side but differs on the response side in two
ways the stock 1.8.3 plugin gets wrong:

1. **Format.** The plugin always asks for ``response_format="mp3"``. OpenRouter's
   Gemini TTS models accept only ``pcm`` (every such request is a 400:
   ``Gemini TTS only supports response_format="pcm"``), while Mistral's Voxtral
   accepts only ``mp3``. :func:`response_format_for` picks per model; ``pcm`` is
   OpenRouter's own default and needs no decoding.
2. **Sample rate.** OpenRouter answers PCM with ``Content-Type:
   audio/pcm;rate=<hz>;channels=<n>``. The plugin drops the parameters and labels
   every byte 24 kHz mono; PCM at any other rate would then play too slow or too
   fast (a pitched, "robotic" voice). :func:`parse_audio_content_type` reads them,
   and the stream labels its frames with the rate the vendor actually sent (the
   voice pipeline resamples to the output rate, livekit-agents 1.8.3
   ``voice/generation.py:631``). It does not remix channels, and the room's
   source is mono, so multi-channel PCM is downmixed (:class:`PcmDownmixer`).

It also never sends ``stream_format``: OpenRouter returns raw audio bytes, never
the SSE stream that parameter selects on OpenAI, and it takes the request id from
OpenRouter's ``X-Generation-Id`` header.

The class is still non-streaming (one request per sentence, ``streaming=False``):
OpenRouter has no incremental text-in/audio-out endpoint.
"""

from __future__ import annotations

import sys
from array import array
from dataclasses import dataclass
from typing import Any, Final

import httpx
import openai
from livekit.agents import APIConnectionError, APIConnectOptions, APIStatusError, APITimeoutError, tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr
from livekit.agents.utils import is_given
from livekit.plugins.openai import tts as openai_tts

from lkap_agent.logging import get_logger

__all__ = [
    "DEFAULT_PCM_SAMPLE_RATE",
    "AudioFormat",
    "OpenRouterTTS",
    "PcmDownmixer",
    "parse_audio_content_type",
    "response_format_for",
]

logger = get_logger(__name__)

#: The rate OpenAI-style PCM is at when a server names none (OpenAI and Gemini both use 24 kHz).
DEFAULT_PCM_SAMPLE_RATE: Final[int] = 24_000
DEFAULT_NUM_CHANNELS: Final[int] = 1

#: OpenRouter TTS models that reject ``pcm`` (the OpenRouter TTS tutorial: Voxtral "accepts MP3 only").
_MP3_ONLY_MODEL_PREFIXES: Final[tuple[str, ...]] = ("mistralai/voxtral",)

_MIME_FOR_FORMAT: Final[dict[str, str]] = {"pcm": "audio/pcm", "mp3": "audio/mpeg"}


def response_format_for(model: str) -> str:
    """The ``response_format`` an OpenRouter TTS model accepts.

    Args:
        model: The OpenRouter model id, e.g. ``google/gemini-3.8-flash-tts``.

    Returns:
        ``"mp3"`` for the models that reject PCM, else ``"pcm"`` (OpenRouter's
        default, required by Gemini TTS, and the lowest-latency choice).
    """
    return "mp3" if model.lower().startswith(_MP3_ONLY_MODEL_PREFIXES) else "pcm"


@dataclass(frozen=True, slots=True)
class AudioFormat:
    """How to read one ``/audio/speech`` response body."""

    mime_type: str
    sample_rate: int
    num_channels: int


def _positive_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def parse_audio_content_type(content_type: str | None, *, requested_format: str) -> AudioFormat:
    """Read the media type, sample rate and channel count from a response ``Content-Type``.

    Args:
        content_type: The raw header, e.g. ``audio/pcm;rate=16000;channels=1`` or ``audio/mpeg``.
        requested_format: The ``response_format`` the request asked for; used when the
            header is missing or names a type the decoder does not know.

    Returns:
        The format to initialise the audio emitter with. PCM without a ``rate``
        parameter is taken as 24 kHz; an encoded format (mp3…) keeps 24 kHz as
        the decode target, since the decoder resamples to it.
    """
    media, _, raw_params = (content_type or "").partition(";")
    media = media.strip().lower()
    params: dict[str, str] = {}
    for part in raw_params.split(";"):
        key, sep, value = part.partition("=")
        if sep:
            params[key.strip().lower()] = value.strip().strip('"')

    if media not in openai_tts.DECODABLE_CONTENT_TYPES:
        media = _MIME_FOR_FORMAT.get(requested_format, f"audio/{requested_format}")

    if not media.startswith("audio/pcm"):
        return AudioFormat(
            mime_type=media, sample_rate=DEFAULT_PCM_SAMPLE_RATE, num_channels=DEFAULT_NUM_CHANNELS
        )

    rate = _positive_int(params.get("rate"))
    if rate is None:
        logger.debug("pcm response without a rate parameter; assuming 24 kHz", content_type=content_type)
    channels = _positive_int(params.get("channels"))
    return AudioFormat(
        mime_type="audio/pcm",
        sample_rate=rate or DEFAULT_PCM_SAMPLE_RATE,
        num_channels=channels or DEFAULT_NUM_CHANNELS,
    )


class PcmDownmixer:
    """Averages interleaved 16-bit little-endian PCM channels to mono, across chunk boundaries."""

    def __init__(self, num_channels: int) -> None:
        """Create a downmixer.

        Args:
            num_channels: Channels interleaved in the input (2 or more).
        """
        self._num_channels = num_channels
        self._frame_bytes = 2 * num_channels
        self._pending = b""

    def push(self, chunk: bytes) -> bytes:
        """Return the mono samples for every complete input frame seen so far."""
        data = self._pending + chunk
        usable = len(data) - len(data) % self._frame_bytes
        self._pending = data[usable:]
        samples = array("h", data[:usable])
        if sys.byteorder == "big":
            samples.byteswap()
        channels = self._num_channels
        mono = array(
            "h", (sum(samples[i : i + channels]) // channels for i in range(0, len(samples), channels))
        )
        if sys.byteorder == "big":
            mono.byteswap()
        return mono.tobytes()


class OpenRouterTTS(openai_tts.TTS):
    """``livekit.plugins.openai.TTS`` with OpenRouter's response format and rate handling."""

    def __init__(
        self,
        *,
        model: str,
        voice: str = openai_tts.DEFAULT_VOICE,
        speed: float = 1.0,
        instructions: NotGivenOr[str] = NOT_GIVEN,
        base_url: NotGivenOr[str] = NOT_GIVEN,
        api_key: NotGivenOr[str] = NOT_GIVEN,
        client: openai.AsyncClient | None = None,
        response_format: NotGivenOr[str] = NOT_GIVEN,
    ) -> None:
        """Build the TTS; the arguments are ``livekit.plugins.openai.TTS``'s.

        Args:
            model: The OpenRouter model id.
            voice: A voice the model lists.
            speed: Speaking rate; OpenRouter ignores it for providers without one.
            instructions: Optional style instructions.
            base_url: OpenRouter's API root.
            api_key: The OpenRouter key.
            client: A pre-built client (tests).
            response_format: Overrides :func:`response_format_for`.
        """
        super().__init__(
            model=model,
            voice=voice,
            speed=speed,
            instructions=instructions,
            base_url=base_url,
            api_key=api_key,
            client=client,
            response_format=response_format if is_given(response_format) else response_format_for(model),
        )

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> tts.ChunkedStream:
        """Synthesise one sentence."""
        return _ChunkedStream(tts=self, input_text=text, conn_options=conn_options)


class _ChunkedStream(tts.ChunkedStream):
    """One ``POST /audio/speech``: raw bytes in the format and rate the response declares."""

    def __init__(self, *, tts: OpenRouterTTS, input_text: str, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._or_tts = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        opts = self._or_tts._opts
        request: Any = self._or_tts._client.audio.speech.with_streaming_response.create(
            input=self.input_text,
            model=opts.model,
            voice=opts.voice,
            response_format=opts.response_format,  # type: ignore[arg-type]
            speed=opts.speed,
            instructions=opts.instructions or openai.omit,
            timeout=httpx.Timeout(30, connect=self._conn_options.timeout),
        )
        try:
            async with request as response:
                audio_format = parse_audio_content_type(
                    response.headers.get("content-type"), requested_format=str(opts.response_format)
                )
                # The room's audio source is mono and the pipeline only resamples, so
                # multi-channel PCM is downmixed here rather than labelled as it came.
                downmix = PcmDownmixer(audio_format.num_channels) if audio_format.num_channels > 1 else None
                output_emitter.initialize(
                    request_id=response.headers.get("x-generation-id") or response.request_id or "",
                    sample_rate=audio_format.sample_rate,
                    num_channels=1 if downmix is not None else audio_format.num_channels,
                    mime_type=audio_format.mime_type,
                )
                async for chunk in response.iter_bytes():
                    data = downmix.push(chunk) if downmix is not None else chunk
                    if data:
                        output_emitter.push(data)
            output_emitter.flush()
        except openai.APITimeoutError:
            raise APITimeoutError() from None
        except openai.APIStatusError as exc:
            raise APIStatusError(
                exc.message, status_code=exc.status_code, request_id=exc.request_id, body=exc.body
            ) from None
        except Exception as exc:
            raise APIConnectionError() from exc
