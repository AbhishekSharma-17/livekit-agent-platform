"""OpenRouter's catalog and credential-test adapter (docs/v4/OPENROUTER.md D-V4-11, R-V4-9).

OpenRouter's ``GET /api/v1/models`` is public: a bogus bearer token still gets
``200``, so listing models alone would report a wrong key as "OK". Every fetch
therefore probes ``GET /api/v1/key`` first (it answers ``401`` to a missing or
wrong key) and only then lists the models, filtered to the modality the
registry entry serves. A failed probe raises before any ``/models`` call.

One adapter class serves all five OpenRouter entries; each registration differs
only in its ``/models`` filter and, for TTS, in flattening every speech model's
``supported_voices`` into a ``voices`` list.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from lkap_contracts.api_models import CatalogItem
from lkap_contracts.providers import OPENROUTER_BASE_URL, CatalogKind

from lkap_api.catalogs.base import (
    DEFAULT_TIMEOUT_S,
    CatalogAdapterError,
    UnsupportedCatalogKind,
    bearer_auth,
    parse_items,
)

#: Model fields kept in `CatalogItem.meta` (the rest, e.g. long descriptions, is dropped).
_META_KEYS: tuple[str, ...] = (
    "id",
    "name",
    "pricing",
    "context_length",
    "architecture",
    "supported_parameters",
    "supported_voices",
    "top_provider",
    "per_request_limits",
)


@dataclass(frozen=True, slots=True)
class OpenRouterCatalogAdapter:
    """Probe ``/key``, then list ``/models`` with a modality filter.

    Attributes:
        vendor: Display name, used in `CredentialTestResult.message`.
        filter: The ``/models`` query string, e.g. ``"supported_parameters=tools"``.
        voices: Also serve ``kind="voices"`` by flattening ``supported_voices``.
        base_url: OpenRouter's API root.
        timeout_s: Per-request timeout.
    """

    vendor: str = "OpenRouter"
    filter: str = ""
    voices: bool = False
    base_url: str = OPENROUTER_BASE_URL
    timeout_s: float = DEFAULT_TIMEOUT_S

    @property
    def models_url(self) -> str:
        """The filtered ``/models`` URL this registration lists."""
        return f"{self.base_url}/models?{self.filter}" if self.filter else f"{self.base_url}/models"

    async def fetch(
        self, *, client: httpx.AsyncClient, secrets: Mapping[str, str], kind: CatalogKind
    ) -> list[CatalogItem]:
        """Check the key, then return the models (or their voices) for ``kind``.

        Raises:
            UnsupportedCatalogKind: For a kind this registration does not list.
            CatalogAdapterError: If the key is rejected, or either request fails.
        """
        if kind != "models" and not (kind == "voices" and self.voices):
            raise UnsupportedCatalogKind(f"{self.vendor} has no '{kind}' catalog")
        headers = bearer_auth(secrets)
        try:
            probe = await client.get(f"{self.base_url}/key", headers=headers, timeout=self.timeout_s)
        except httpx.HTTPError as exc:
            raise CatalogAdapterError(f"{self.vendor} request failed: {type(exc).__name__}") from exc
        if not probe.is_success:
            raise CatalogAdapterError(f"{self.vendor} rejected the key (HTTP {probe.status_code})")
        try:
            response = await client.get(self.models_url, headers=headers, timeout=self.timeout_s)
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            raise CatalogAdapterError(f"{self.vendor} request failed: {type(exc).__name__}") from exc
        except ValueError as exc:  # pragma: no cover - non-JSON vendor body
            raise CatalogAdapterError(f"{self.vendor} returned a non-JSON body") from exc
        models = [
            item.model_copy(update={"meta": _trim(item.meta)})
            for item in parse_items(body, label_keys=("name",))
        ]
        return _voices(models) if kind == "voices" else models


def _trim(meta: dict[str, Any]) -> dict[str, Any]:
    return {key: meta[key] for key in _META_KEYS if key in meta}


def _voice_id(raw: object) -> str | None:
    if isinstance(raw, str):
        return raw or None
    if isinstance(raw, dict):
        for key in ("id", "voice_id", "name"):
            value = raw.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _voices(models: list[CatalogItem]) -> list[CatalogItem]:
    """One item per (model, voice): ``id`` is the voice, the label names the model."""
    items: list[CatalogItem] = []
    for model in models:
        raw_voices = model.meta.get("supported_voices")
        if not isinstance(raw_voices, list):
            continue
        for raw in raw_voices:
            voice = _voice_id(raw)
            if voice is None:
                continue
            items.append(CatalogItem(id=voice, label=f"{voice} · {model.label}", meta={"model": model.id}))
    return items


#: The five registrations, keyed by `ProviderSpec.test` / `catalog.adapter`.
OPENROUTER_ADAPTERS: dict[str, OpenRouterCatalogAdapter] = {
    "openrouter_llm_models": OpenRouterCatalogAdapter(filter="supported_parameters=tools"),
    "openrouter_stt_models": OpenRouterCatalogAdapter(filter="output_modalities=transcription"),
    "openrouter_tts_models": OpenRouterCatalogAdapter(filter="output_modalities=speech", voices=True),
    "openrouter_embedding_models": OpenRouterCatalogAdapter(filter="output_modalities=embeddings"),
    "openrouter_image_models": OpenRouterCatalogAdapter(filter="output_modalities=image"),
}

__all__ = ["OPENROUTER_ADAPTERS", "OpenRouterCatalogAdapter"]
