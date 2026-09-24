"""Tests for `lkap_agent.providers.image_gen` — mocked at the SDK boundary
(`client=`) per the module's `ImageGen` seam. No network, no vendor keys.
"""

from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace
from typing import Any

import pytest

from lkap_agent.providers.image_gen import GoogleImageGen, ImageGenError, OpenAIImageGen, OpenRouterImageGen


def _google_response(*, data: bytes = b"\x89PNG-bytes", mime: str = "image/png") -> Any:
    inline = SimpleNamespace(data=data, mime_type=mime)
    part = SimpleNamespace(inline_data=inline)
    content = SimpleNamespace(parts=[part])
    candidate = SimpleNamespace(content=content)
    return SimpleNamespace(candidates=[candidate])


class _FakeGoogleClient:
    """Duck-types `google.genai.Client`'s `.aio.models.generate_content(...)`."""

    def __init__(self, response: Any = None, *, delay: float = 0.0, error: Exception | None = None) -> None:
        self._response = response
        self._delay = delay
        self._error = error
        self.calls: list[dict[str, Any]] = []

        async def generate_content(**kwargs: Any) -> Any:
            self.calls.append(kwargs)
            if self._delay:
                await asyncio.sleep(self._delay)
            if self._error is not None:
                raise self._error
            return self._response

        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))


class TestGoogleImageGen:
    async def test_generate_returns_first_inline_image(self) -> None:
        client = _FakeGoogleClient(response=_google_response(data=b"abc", mime="image/png"))
        provider = GoogleImageGen(api_key="unused", model="gemini-3.1-flash-image", client=client)

        image_bytes, mime = await provider.generate("draw a cat")

        assert image_bytes == b"abc"
        assert mime == "image/png"
        assert client.calls[0]["model"] == "gemini-3.1-flash-image"
        assert client.calls[0]["contents"] == "draw a cat"

    async def test_generate_raises_on_empty_response(self) -> None:
        client = _FakeGoogleClient(response=SimpleNamespace(candidates=[]))
        provider = GoogleImageGen(api_key="unused", client=client)

        with pytest.raises(ImageGenError, match="no image"):
            await provider.generate("draw a cat")

    async def test_generate_raises_on_timeout(self) -> None:
        client = _FakeGoogleClient(response=_google_response(), delay=0.2)
        provider = GoogleImageGen(api_key="unused", client=client)

        with pytest.raises(ImageGenError, match="timed out"):
            await provider.generate("draw a cat", timeout_s=0.01)

    async def test_generate_wraps_sdk_errors(self) -> None:
        client = _FakeGoogleClient(error=RuntimeError("boom"))
        provider = GoogleImageGen(api_key="unused", client=client)

        with pytest.raises(ImageGenError, match="boom"):
            await provider.generate("draw a cat")

    def test_requires_api_key_without_client(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            GoogleImageGen(api_key="")


def _openai_response(*, b64: str | None) -> Any:
    return SimpleNamespace(data=[SimpleNamespace(b64_json=b64)])


class _FakeOpenAIClient:
    """Duck-types `openai.AsyncOpenAI`'s `.images.generate(...)`."""

    def __init__(self, response: Any = None, *, delay: float = 0.0, error: Exception | None = None) -> None:
        self._response = response
        self._delay = delay
        self._error = error
        self.calls: list[dict[str, Any]] = []

        async def generate(**kwargs: Any) -> Any:
            self.calls.append(kwargs)
            if self._delay:
                await asyncio.sleep(self._delay)
            if self._error is not None:
                raise self._error
            return self._response

        self.images = SimpleNamespace(generate=generate)


class TestOpenAIImageGen:
    async def test_generate_decodes_b64_json(self) -> None:
        raw = b"png-bytes"
        client = _FakeOpenAIClient(response=_openai_response(b64=base64.b64encode(raw).decode()))
        provider = OpenAIImageGen(api_key="unused", model="gpt-image-1", size="1024x1024", client=client)

        image_bytes, mime = await provider.generate("draw a cat")

        assert image_bytes == raw
        assert mime == "image/png"
        assert client.calls[0]["model"] == "gpt-image-1"
        assert client.calls[0]["size"] == "1024x1024"
        # gpt-image-1 has no response_format/url option — never request one
        assert "response_format" not in client.calls[0]

    async def test_generate_raises_when_no_image_data(self) -> None:
        client = _FakeOpenAIClient(response=_openai_response(b64=None))
        provider = OpenAIImageGen(api_key="unused", client=client)

        with pytest.raises(ImageGenError, match="no image data"):
            await provider.generate("draw a cat")

    async def test_generate_raises_on_timeout(self) -> None:
        client = _FakeOpenAIClient(response=_openai_response(b64="AA=="), delay=0.2)
        provider = OpenAIImageGen(api_key="unused", client=client)

        with pytest.raises(ImageGenError, match="timed out"):
            await provider.generate("draw a cat", timeout_s=0.01)

    async def test_generate_wraps_sdk_errors(self) -> None:
        client = _FakeOpenAIClient(error=RuntimeError("rate limited"))
        provider = OpenAIImageGen(api_key="unused", client=client)

        with pytest.raises(ImageGenError, match="rate limited"):
            await provider.generate("draw a cat")

    def test_requires_api_key_without_client(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            OpenAIImageGen(api_key="")


class _FakeOpenRouterClient:
    """Duck-types `AsyncOpenAI.post(...)` returning an `httpx.Response`-like object."""

    def __init__(self, payload: Any = None, *, delay: float = 0.0, error: Exception | None = None) -> None:
        self._payload = payload
        self._delay = delay
        self._error = error
        self.calls: list[dict[str, Any]] = []

    async def post(self, path: str, **kwargs: Any) -> Any:
        self.calls.append({"path": path, **kwargs})
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return SimpleNamespace(json=lambda: self._payload)


class TestOpenRouterImageGen:
    async def test_generate_posts_the_unified_images_body_and_decodes_it(self) -> None:
        raw = b"jpeg-bytes"
        client = _FakeOpenRouterClient(
            {"data": [{"b64_json": base64.b64encode(raw).decode(), "media_type": "image/jpeg"}]}
        )
        provider = OpenRouterImageGen(
            api_key="unused", model="openai/gpt-image-1", resolution="4K", aspect_ratio="3:2", client=client
        )

        assert await provider.generate("draw a cat") == (raw, "image/jpeg")
        call = client.calls[0]
        assert call["path"] == "/images"
        assert call["body"] == {
            "model": "openai/gpt-image-1",
            "prompt": "draw a cat",
            "resolution": "4K",
            "aspect_ratio": "3:2",
            "n": 1,
        }
        assert "size" not in call["body"], "OpenRouter takes resolution/aspect_ratio, not OpenAI's size"

    async def test_generate_defaults_the_mime_type_to_png(self) -> None:
        client = _FakeOpenRouterClient({"data": [{"b64_json": "AA=="}]})
        provider = OpenRouterImageGen(api_key="unused", client=client)

        _, mime = await provider.generate("draw a cat")

        assert mime == "image/png"

    async def test_generate_raises_when_no_image_data(self) -> None:
        provider = OpenRouterImageGen(api_key="unused", client=_FakeOpenRouterClient({"data": []}))

        with pytest.raises(ImageGenError, match="no image data"):
            await provider.generate("draw a cat")

    async def test_generate_raises_on_timeout(self) -> None:
        client = _FakeOpenRouterClient({"data": [{"b64_json": "AA=="}]}, delay=0.2)
        provider = OpenRouterImageGen(api_key="unused", client=client)

        with pytest.raises(ImageGenError, match="timed out"):
            await provider.generate("draw a cat", timeout_s=0.01)

    async def test_generate_wraps_sdk_errors(self) -> None:
        provider = OpenRouterImageGen(
            api_key="unused", client=_FakeOpenRouterClient(error=RuntimeError("402"))
        )

        with pytest.raises(ImageGenError, match="402"):
            await provider.generate("draw a cat")

    def test_requires_api_key_without_client(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            OpenRouterImageGen(api_key="")
