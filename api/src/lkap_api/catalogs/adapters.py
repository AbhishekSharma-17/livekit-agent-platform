"""The Phase-1 vendor catalog adapters (ARCHITECTURE-V2 D-V2-9).

Registered under the same name the registry uses for both `ProviderSpec.catalog.adapter`
and `ProviderSpec.test` (they are the same adapter — see :mod:`lkap_api.catalogs.base`).
13 names are wired into `contracts/src/lkap_contracts/providers.py` today (V2-05's file);
`did_avatars` is built and tested but not yet referenced by any registry entry — D-ID has
a confirmed `GET /clips/presenters` list API (research catalog §2.2) but V2-05 has not
set `catalog=`/`test=` on `did-avatar` yet (flagged in `docs/v2/_asks.md`).

Endpoints and auth-header shapes come from `docs/research-v2/livekit-plugins-catalog.md`
§1 (STT/LLM/TTS) and §2.2 (avatars); cells the research doc could not confirm from a
vendor source are marked there as UNVERIFIED, and the parsing here is deliberately
lenient (`base.parse_items`) so an unverified payload shape degrades to "fewer items
parsed", never to a crash (CONTRACTS-V2: a failed/odd vendor call must never break the
providers page).
"""

from __future__ import annotations

from collections.abc import Mapping

from lkap_api.catalogs.base import AuthBuilder, CatalogAdapter, HttpCatalogAdapter, bearer_auth


def _header(name: str) -> AuthBuilder:
    def _auth(secrets: Mapping[str, str]) -> dict[str, str]:
        return {name: secrets.get("api_key", "")}

    return _auth


def _anthropic_auth(secrets: Mapping[str, str]) -> dict[str, str]:
    return {"x-api-key": secrets.get("api_key", ""), "anthropic-version": "2023-06-01"}


def _cartesia_auth(secrets: Mapping[str, str]) -> dict[str, str]:
    return {"X-API-Key": secrets.get("api_key", ""), "Cartesia-Version": "2024-06-10"}


#: Every adapter this package implements, keyed by `ProviderSpec.test` / `catalog.adapter`.
ADAPTERS: dict[str, CatalogAdapter] = {
    # ------------------------------------------------------------------ models (LLM/STT)
    "openai_models": HttpCatalogAdapter(
        vendor="OpenAI",
        endpoints={"models": "https://api.openai.com/v1/models"},
        auth=bearer_auth,
    ),
    "anthropic_models": HttpCatalogAdapter(
        vendor="Anthropic",
        endpoints={"models": "https://api.anthropic.com/v1/models"},
        auth=_anthropic_auth,
    ),
    "groq_models": HttpCatalogAdapter(
        vendor="Groq",
        endpoints={"models": "https://api.groq.com/openai/v1/models"},
        auth=bearer_auth,
    ),
    "mistral_models": HttpCatalogAdapter(
        vendor="Mistral",
        endpoints={"models": "https://api.mistral.ai/v1/models"},
        auth=bearer_auth,
    ),
    # ------------------------------------------------------------------------------- TTS
    "cartesia_voices": HttpCatalogAdapter(
        vendor="Cartesia",
        endpoints={"voices": "https://api.cartesia.ai/voices"},
        auth=_cartesia_auth,
        id_keys=("id",),
        label_keys=("name",),
    ),
    "elevenlabs_voices": HttpCatalogAdapter(
        vendor="ElevenLabs",
        endpoints={
            "voices": "https://api.elevenlabs.io/v1/voices",
            "models": "https://api.elevenlabs.io/v1/models",
        },
        auth=_header("xi-api-key"),
        id_keys=("voice_id", "model_id"),
        label_keys=("name",),
    ),
    "hume_voices": HttpCatalogAdapter(
        vendor="Hume",
        endpoints={"voices": "https://api.hume.ai/v0/tts/voices"},
        auth=_header("X-Hume-Api-Key"),
        id_keys=("id", "voice_id"),
        label_keys=("name",),
    ),
    "speechify_voices": HttpCatalogAdapter(
        vendor="Speechify",
        endpoints={"voices": "https://api.sws.speechify.com/v1/voices"},
        auth=bearer_auth,
        id_keys=("id",),
        label_keys=("display_name", "name"),
    ),
    # --------------------------------------------------------------------------- avatars
    "bey_avatars": HttpCatalogAdapter(
        vendor="Beyond Presence",
        endpoints={"avatars": "https://api.bey.dev/v1/avatars"},
        auth=_header("x-api-key"),
        id_keys=("id", "avatar_id"),
        label_keys=("name",),
    ),
    "tavus_faces_pals": HttpCatalogAdapter(
        vendor="Tavus",
        endpoints={
            "avatars": "https://tavusapi.com/v2/faces",
            "personas": "https://tavusapi.com/v2/pals",
        },
        auth=_header("x-api-key"),
        id_keys=("face_id", "pal_id"),
        label_keys=("face_name", "pal_name"),
    ),
    "simli_faces": HttpCatalogAdapter(
        vendor="Simli",
        endpoints={"avatars": "https://api.simli.ai/faces"},
        auth=_header("x-simli-api-key"),
        id_keys=("face_id",),
        label_keys=("face_name", "name"),
    ),
    "anam_avatars": HttpCatalogAdapter(
        vendor="Anam",
        endpoints={
            "avatars": "https://api.anam.ai/v1/avatars",
            "personas": "https://api.anam.ai/v1/personas",
        },
        auth=bearer_auth,
        id_keys=("avatarId",),
        label_keys=("name",),
    ),
    "liveavatar_avatars": HttpCatalogAdapter(
        vendor="LiveAvatar",
        endpoints={"avatars": "https://api.liveavatar.com/v1/avatars"},
        auth=_header("X-API-KEY"),
        id_keys=("avatar_id",),
        label_keys=("name",),
    ),
    # ------------------------------------- not yet wired into the registry (asks log)
    "did_avatars": HttpCatalogAdapter(
        vendor="D-ID",
        endpoints={"avatars": "https://api.d-id.com/clips/presenters"},
        auth=bearer_auth,
        id_keys=("presenter_id",),
        label_keys=("name",),
    ),
}


def get_adapter(name: str) -> CatalogAdapter | None:
    """Return the adapter registered as ``name`` (``ProviderSpec.test``/``catalog.adapter``)."""
    return ADAPTERS.get(name)
