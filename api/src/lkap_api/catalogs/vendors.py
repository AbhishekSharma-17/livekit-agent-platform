"""V4-07 vendor catalog adapters (docs/v4/CUSTOM-MODELS.md §1.2, D-V4-25).

Gemini, Deepgram (public), xAI, Cerebras, Together, Inworld and Rime (public).
Where a vendor's list is a plain "GET with auth" the generic
:class:`~lkap_api.catalogs.base.HttpCatalogAdapter` serves it; the three whose
bodies need reshaping (Gemini's ``models/`` prefix, Deepgram's two-list
envelope, Rime's per-model voice map) have a small class here.

Public lists (``public=True``) are fetched with no secret at all and may be a
catalog but never a credential test (R-V4-9); their names are exactly
:data:`lkap_contracts.providers.PUBLIC_CATALOG_ADAPTERS`.

Not here, by decision: Azure voices (the url needs the slot's ``speech_region``,
which the provider-level catalog route cannot carry), Bedrock (SigV4), Fireworks
(account-scoped), LiveKit Inference (no documented list) — asks, not adapters.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from lkap_contracts.api_models import CatalogItem
from lkap_contracts.providers import CatalogKind, PageSpec

from lkap_api.catalogs.base import (
    DEFAULT_TIMEOUT_S,
    AuthBuilder,
    CatalogAdapter,
    HttpCatalogAdapter,
    UnsupportedCatalogKind,
    bearer_auth,
    fetch_pages,
    get_json,
    parse_items,
)

GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
DEEPGRAM_MODELS_URL = "https://api.deepgram.com/v1/models"
RIME_VOICES_URL = "https://users.rime.ai/data/voices/all-v2.json"
XAI_MODELS_URL = "https://api.x.ai/v1/language-models"
CEREBRAS_MODELS_URL = "https://api.cerebras.ai/v1/models"
TOGETHER_MODELS_URL = "https://api.together.xyz/v1/models"
INWORLD_VOICES_URL = "https://api.inworld.ai/voices/v1/voices"


def _gemini_auth(secrets: Mapping[str, str]) -> dict[str, str]:
    return {"x-goog-api-key": secrets.get("api_key", "")}


def _inworld_auth(secrets: Mapping[str, str]) -> dict[str, str]:
    # Inworld issues the key already Base64-encoded for Basic auth; it is sent as is.
    return {"Authorization": f"Basic {secrets.get('api_key', '')}"}


@dataclass(frozen=True, slots=True)
class GeminiCatalogAdapter:
    """``GET /v1beta/models`` with ``x-goog-api-key``; ids lose their ``models/`` prefix.

    ``meta`` keeps the raw item, so ``supportedGenerationMethods`` feeds the
    registry's :class:`~lkap_contracts.providers.CatalogFilter` (``generateContent``
    for ``google-llm``, ``bidiGenerateContent`` for ``google-realtime``).
    """

    vendor: str = "Google"
    url: str = GEMINI_MODELS_URL
    timeout_s: float = DEFAULT_TIMEOUT_S
    public: bool = False

    async def fetch(
        self,
        *,
        client: httpx.AsyncClient,
        secrets: Mapping[str, str],
        kind: CatalogKind,
        page: PageSpec | None = None,
        first_page_only: bool = False,
    ) -> list[CatalogItem]:
        """List Gemini models, following ``nextPageToken``."""
        if kind != "models":
            raise UnsupportedCatalogKind(f"{self.vendor} has no '{kind}' catalog")
        bodies = await fetch_pages(
            client,
            self.url,
            vendor=self.vendor,
            headers=_gemini_auth(secrets),
            page=page,
            first_page_only=first_page_only,
            timeout_s=self.timeout_s,
        )
        items: list[CatalogItem] = []
        for body in bodies:
            for item in parse_items(body, id_keys=("name",), label_keys=("displayName",)):
                model_id = item.id.removeprefix("models/")
                label = item.label if item.label != item.id else model_id
                items.append(item.model_copy(update={"id": model_id, "label": label}))
        return items


def _deepgram_label(raw: dict[str, Any], canonical: str, envelope: str) -> str:
    if envelope == "tts":
        metadata = raw.get("metadata")
        display = metadata.get("display_name") if isinstance(metadata, dict) else None
        languages = raw.get("languages")
        language = languages[0] if isinstance(languages, list) and languages else None
        name = str(display or raw.get("name") or canonical)
        return f"{name} ({language})" if language else name
    return canonical


@dataclass(frozen=True, slots=True)
class DeepgramCatalogAdapter:
    """Deepgram's public ``GET /v1/models`` (``{stt: [...], tts: [...]}``), one side of it.

    The id is ``canonical_name`` (``nova-3-general``, ``aura-2-thalia-en``);
    the STT side lists every version of a model, so ids are de-duplicated
    (first occurrence wins). Public: no key is sent (R-V4-9: never a test).
    """

    envelope: Literal["stt", "tts"]
    vendor: str = "Deepgram"
    url: str = DEEPGRAM_MODELS_URL
    timeout_s: float = DEFAULT_TIMEOUT_S
    public: bool = True

    async def fetch(
        self,
        *,
        client: httpx.AsyncClient,
        secrets: Mapping[str, str],
        kind: CatalogKind,
        page: PageSpec | None = None,
        first_page_only: bool = False,
    ) -> list[CatalogItem]:
        """List one side of Deepgram's model list (no pagination; one response)."""
        if kind != "models":
            raise UnsupportedCatalogKind(f"{self.vendor} has no '{kind}' catalog")
        body = await get_json(client, self.url, vendor=self.vendor, headers={}, timeout_s=self.timeout_s)
        raw_items = body.get(self.envelope) if isinstance(body, dict) else None
        items: list[CatalogItem] = []
        seen: set[str] = set()
        for raw in raw_items if isinstance(raw_items, list) else []:
            if not isinstance(raw, dict):
                continue
            canonical = raw.get("canonical_name") or raw.get("name")
            if not isinstance(canonical, str) or not canonical or canonical in seen:
                continue
            seen.add(canonical)
            items.append(
                CatalogItem(id=canonical, label=_deepgram_label(raw, canonical, self.envelope), meta=raw)
            )
        return items


@dataclass(frozen=True, slots=True)
class RimeCatalogAdapter:
    """Rime's public voice map ``{model: {language: [voice, ...]}}``.

    ``voices``: one item per ``(model, voice)`` with ``meta.model`` and the
    voice's languages (the catalog route's ``model=`` filter picks one model's
    voices); ``models``: one item per model key. Public: no key is sent.
    """

    vendor: str = "Rime"
    url: str = RIME_VOICES_URL
    timeout_s: float = DEFAULT_TIMEOUT_S
    public: bool = True

    async def fetch(
        self,
        *,
        client: httpx.AsyncClient,
        secrets: Mapping[str, str],
        kind: CatalogKind,
        page: PageSpec | None = None,
        first_page_only: bool = False,
    ) -> list[CatalogItem]:
        """List Rime's models or its ``(model, voice)`` pairs."""
        if kind not in ("voices", "models"):
            raise UnsupportedCatalogKind(f"{self.vendor} has no '{kind}' catalog")
        body = await get_json(client, self.url, vendor=self.vendor, headers={}, timeout_s=self.timeout_s)
        by_model = body if isinstance(body, dict) else {}
        if kind == "models":
            return [CatalogItem(id=str(model), label=str(model)) for model in by_model]
        items: list[CatalogItem] = []
        for model, languages in by_model.items():
            if not isinstance(languages, dict):
                continue
            voices: dict[str, list[str]] = {}
            for language, names in languages.items():
                for name in names if isinstance(names, list) else []:
                    if isinstance(name, str) and name:
                        voices.setdefault(name, []).append(str(language))
            items.extend(
                CatalogItem(
                    id=voice, label=f"{voice} · {model}", meta={"model": str(model), "languages": langs}
                )
                for voice, langs in voices.items()
            )
        return items


def _plain(vendor: str, url: str, kind: CatalogKind, auth: AuthBuilder, **kwargs: Any) -> HttpCatalogAdapter:
    return HttpCatalogAdapter(vendor=vendor, endpoints={kind: url}, auth=auth, **kwargs)


#: The V4-07 registrations, keyed by `CatalogSpec.adapter` (and `ProviderSpec.test` where keyed).
VENDOR_ADAPTERS: dict[str, CatalogAdapter] = {
    "gemini_models": GeminiCatalogAdapter(),
    "deepgram_stt_models": DeepgramCatalogAdapter(envelope="stt"),
    "deepgram_tts_models": DeepgramCatalogAdapter(envelope="tts"),
    "rime_voices": RimeCatalogAdapter(),
    "xai_models": _plain("xAI", XAI_MODELS_URL, "models", bearer_auth),
    "cerebras_models": _plain("Cerebras", CEREBRAS_MODELS_URL, "models", bearer_auth),
    # Registered for `openai-compatible-llm` users; no registry entry names Together yet.
    "together_models": _plain(
        "Together", TOGETHER_MODELS_URL, "models", bearer_auth, label_keys=("display_name",)
    ),
    "inworld_voices": _plain(
        "Inworld",
        INWORLD_VOICES_URL,
        "voices",
        _inworld_auth,
        id_keys=("voiceId",),
        label_keys=("displayName",),
    ),
}

__all__ = [
    "VENDOR_ADAPTERS",
    "DeepgramCatalogAdapter",
    "GeminiCatalogAdapter",
    "RimeCatalogAdapter",
]
