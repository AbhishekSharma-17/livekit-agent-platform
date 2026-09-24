"""Image-generation providers behind `packs.base.ImageGen`.

Registry entries `google-image-gen` / `openai-image-gen` (docs/CONTRACTS.md
§4) and `openrouter-image-gen` (docs/v4/OPENROUTER.md §2.6) point their
`python_class` here. `ProviderFactory` (W1-AGENT-CORE)
constructs these the same way as every other provider: credentials and
`model` arrive as explicit constructor kwargs, never via process env
(docs/ARCHITECTURE.md §6 factory rule).

Both providers are configurable end-to-end — neither vendor is hardcoded
anywhere else in the platform, and `PackSessionContext.image_gen` is typed
`ImageGen | None` precisely so a missing/invalid credential degrades to "no
image generation this session" instead of crashing the worker. That
degradation happens at two points: construction (`ValueError` on an empty
`api_key`, which the factory/pack-seeder is expected to catch) and per-call
(`ImageGenError` on timeout or an empty response), so a pack tool can always
catch `ImageGenError` and tell the user gracefully rather than the session
dying mid-call.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

from lkap_agent.logging import get_logger

_log = get_logger(__name__)

DEFAULT_TIMEOUT_S = 40.0


class ImageGenError(Exception):
    """Raised when an image-generation call fails, times out, or returns no image."""


class GoogleImageGen:
    """`packs.base.ImageGen` over `google-genai` (Gemini image generation).

    Port of the prior prototype's `_draw_incident_sketch` extraction
    (`first_inline_image` over
    `generate_content(response_modalities=["IMAGE"])`, verified against the
    installed `google-genai` SDK: `genai.Client(api_key=...)`,
    `client.aio.models.generate_content(...)`,
    `types.GenerateContentConfig(response_modalities=["IMAGE"])`).
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gemini-3.1-flash-image",
        client: Any | None = None,
    ) -> None:
        """Build a Gemini image-generation client.

        Args:
            api_key: Google API key, decrypted from the credential vault.
            model: Model id, e.g. `"gemini-3.1-flash-image"`.
            client: Test seam — an object exposing
                `.aio.models.generate_content(...)`. Constructed from
                `api_key` when omitted.

        Raises:
            ValueError: If `api_key` is empty and no `client` is supplied.
        """
        if client is not None:
            self._client = client
        else:
            if not api_key:
                raise ValueError("GoogleImageGen requires a non-empty api_key")
            from google import genai

            self._client = genai.Client(api_key=api_key)
        self._model = model

    async def generate(self, prompt: str, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> tuple[bytes, str]:
        """Generate one image from `prompt`.

        Args:
            prompt: The full image prompt (style + scene already composed by the caller).
            timeout_s: Seconds to wait before raising `ImageGenError`.

        Returns:
            `(image_bytes, mime_type)`.

        Raises:
            ImageGenError: On timeout, an SDK/API error, or a response with no inline image.
        """
        from google.genai import types

        try:
            response = await asyncio.wait_for(
                self._client.aio.models.generate_content(
                    model=self._model,
                    contents=prompt,
                    config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
                ),
                timeout=timeout_s,
            )
        except TimeoutError as exc:
            _log.warning("image_gen.timeout", provider="google", timeout_s=timeout_s)
            raise ImageGenError(f"Google image generation timed out after {timeout_s}s") from exc
        except ImageGenError:
            raise
        except Exception as exc:  # SDK/transport errors vary; surface uniformly
            _log.warning("image_gen.error", provider="google", error=str(exc))
            raise ImageGenError(f"Google image generation failed: {exc}") from exc

        image = _first_inline_image(response)
        if image is None:
            raise ImageGenError("Google image generation returned no image")
        return image


def _first_inline_image(response: Any) -> tuple[bytes, str] | None:
    """Return the first inline image part of a `generate_content` response, if any."""
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        parts = (getattr(content, "parts", None) or []) if content is not None else []
        for part in parts:
            inline = getattr(part, "inline_data", None)
            if inline is not None and inline.data:
                return bytes(inline.data), inline.mime_type or "image/png"
    return None


class OpenAIImageGen:
    """`packs.base.ImageGen` over the OpenAI Images API.

    `openai>=2,<3` (pinned by `agent/pyproject.toml`; see its DEVIATION note
    on `CONTRACTS.md` §2 — `livekit-agents==1.8.2` itself requires
    `openai>=2,<3`) exposes `AsyncOpenAI().images.generate(...)`, verified in
    the installed SDK. `gpt-image-1` always returns base64 image data (it has
    no `response_format`/hosted-URL option), so this reads
    `response.data[0].b64_json` unconditionally.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-image-1",
        size: str = "1024x1024",
        client: Any | None = None,
    ) -> None:
        """Build an OpenAI image-generation client.

        Args:
            api_key: OpenAI API key, decrypted from the credential vault.
            model: Model id, e.g. `"gpt-image-1"`.
            size: Image size string, e.g. `"1024x1024"`.
            client: Test seam — an object exposing `.images.generate(...)`.
                Constructed from `api_key` when omitted.

        Raises:
            ValueError: If `api_key` is empty and no `client` is supplied.
        """
        if client is not None:
            self._client = client
        else:
            if not api_key:
                raise ValueError("OpenAIImageGen requires a non-empty api_key")
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=api_key)
        self._model = model
        self._size = size

    async def generate(self, prompt: str, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> tuple[bytes, str]:
        """Generate one image from `prompt`.

        Args:
            prompt: The full image prompt.
            timeout_s: Seconds to wait before raising `ImageGenError`.

        Returns:
            `(image_bytes, mime_type)` — always PNG, matching `gpt-image-1`'s output.

        Raises:
            ImageGenError: On timeout, an SDK/API error, or a response with no image data.
        """
        try:
            response = await asyncio.wait_for(
                self._client.images.generate(
                    model=self._model,
                    prompt=prompt,
                    size=self._size,
                    timeout=timeout_s,
                ),
                timeout=timeout_s,
            )
        except TimeoutError as exc:
            _log.warning("image_gen.timeout", provider="openai", timeout_s=timeout_s)
            raise ImageGenError(f"OpenAI image generation timed out after {timeout_s}s") from exc
        except ImageGenError:
            raise
        except Exception as exc:
            _log.warning("image_gen.error", provider="openai", error=str(exc))
            raise ImageGenError(f"OpenAI image generation failed: {exc}") from exc

        data = getattr(response, "data", None) or []
        b64 = getattr(data[0], "b64_json", None) if data else None
        if not b64:
            raise ImageGenError("OpenAI image generation returned no image data")
        return base64.b64decode(b64), "image/png"


#: OpenRouter's API root (the registry's `OPENROUTER_BASE_URL`).
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterImageGen:
    """`packs.base.ImageGen` over OpenRouter's unified Images API (docs/v4/OPENROUTER.md §2.6).

    OpenRouter's image endpoint is ``POST /api/v1/images`` (not OpenAI's
    ``/images/generations``) with ``resolution``/``aspect_ratio`` instead of
    ``size``, so ``AsyncOpenAI().images.generate`` cannot reach it. This class
    uses the SDK's generic ``post`` (no new dependency) and reads
    ``data[0].b64_json`` / ``data[0].media_type`` from the JSON response.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "openai/gpt-image-1",
        resolution: str = "1K",
        aspect_ratio: str = "1:1",
        site_url: str | None = None,
        app_name: str | None = "LKAP",
        base_url: str = OPENROUTER_BASE_URL,
        client: Any | None = None,
    ) -> None:
        """Build an OpenRouter image-generation client.

        Args:
            api_key: OpenRouter API key, decrypted from the credential vault.
            model: Model id, e.g. `"openai/gpt-image-1"`.
            resolution: `"512"`, `"1K"`, `"2K"` or `"4K"`.
            aspect_ratio: e.g. `"1:1"`, `"16:9"`.
            site_url: Sent as `HTTP-Referer` (OpenRouter app attribution) when set.
            app_name: Sent as `X-Title` when set.
            base_url: OpenRouter's API root.
            client: Test seam — an `AsyncOpenAI`-like object exposing `.post(...)`.
                Constructed from `api_key` when omitted.

        Raises:
            ValueError: If `api_key` is empty and no `client` is supplied.
        """
        if client is not None:
            self._client = client
        else:
            if not api_key:
                raise ValueError("OpenRouterImageGen requires a non-empty api_key")
            from openai import AsyncOpenAI

            headers: dict[str, str] = {}
            if site_url:
                headers["HTTP-Referer"] = site_url
            if app_name:
                headers["X-Title"] = app_name
            self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, default_headers=headers)
        self._model = model
        self._resolution = resolution
        self._aspect_ratio = aspect_ratio

    async def generate(self, prompt: str, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> tuple[bytes, str]:
        """Generate one image from `prompt`.

        Args:
            prompt: The full image prompt.
            timeout_s: Seconds to wait before raising `ImageGenError`.

        Returns:
            `(image_bytes, mime_type)`; the MIME type is OpenRouter's `media_type`.

        Raises:
            ImageGenError: On timeout, an SDK/API error, or a response with no image data.
        """
        import httpx

        body = {
            "model": self._model,
            "prompt": prompt,
            "resolution": self._resolution,
            "aspect_ratio": self._aspect_ratio,
            "n": 1,
        }
        try:
            response = await asyncio.wait_for(
                self._client.post(
                    "/images", body=body, cast_to=httpx.Response, options={"timeout": timeout_s}
                ),
                timeout=timeout_s,
            )
            payload = response.json()
        except TimeoutError as exc:
            _log.warning("image_gen.timeout", provider="openrouter", timeout_s=timeout_s)
            raise ImageGenError(f"OpenRouter image generation timed out after {timeout_s}s") from exc
        except ImageGenError:
            raise
        except Exception as exc:
            _log.warning("image_gen.error", provider="openrouter", error=str(exc))
            raise ImageGenError(f"OpenRouter image generation failed: {exc}") from exc

        data = payload.get("data") if isinstance(payload, dict) else None
        first = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
        b64 = first.get("b64_json")
        if not b64:
            raise ImageGenError("OpenRouter image generation returned no image data")
        return base64.b64decode(b64), str(first.get("media_type") or "image/png")
