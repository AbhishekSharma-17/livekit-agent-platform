"""Text embedders behind a swappable :class:`Embedder` Protocol.

Default is :class:`FastEmbedEmbedder` (local ONNX, no vendor key, no network
after the model is cached). :class:`OpenAIEmbedder` is an optional path,
selected via ``LKAP_EMBEDDER=<provider_id>:<credential_id>`` (``openai-embedding``
or ``openrouter-embedding``; the legacy ``openai:<credential_id>`` still works),
that calls an OpenAI-shaped REST API directly over ``httpx`` so the api does not need the ``openai`` SDK
as a dependency. :class:`FakeEmbedder` is for offline tests: a deterministic,
dependency-free bag-of-tokens embedding whose cosine similarity is high for
texts sharing tokens.

``LKAP_EMBEDDER`` picks one process-wide embedder used for every knowledge
base; ``KnowledgeBase.embedder_id`` records which one *created* a KB for
display/audit but does not switch implementations per KB (mixing embedders
inside one vector table would mix incompatible dimensions).
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

import httpx
from lkap_contracts import providers as provider_registry
from lkap_contracts.providers import ProviderSpec
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import Credential
from lkap_api.errors import UnprocessableEntityError
from lkap_api.logging import get_logger
from lkap_api.settings import Settings
from lkap_api.vault import Vault

log = get_logger(__name__)

FASTEMBED_MODEL = "BAAI/bge-small-en-v1.5"
FASTEMBED_DIMENSION = 384
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
OPENAI_EMBEDDING_DIMENSION = 1536
FAKE_EMBEDDER_DIMENSION = 32

_TOKEN_RE = re.compile(r"[a-z0-9]+")


@runtime_checkable
class Embedder(Protocol):
    """Turns text into fixed-length vectors for the vector store."""

    dimension: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one embedding vector per input text, in order."""
        ...


class FastEmbedEmbedder:
    """Local ONNX embedder (``fastembed``), model cached under ``LKAP_DATA_DIR/models``.

    The model is loaded lazily on first use (not at construction) so building
    a :class:`FastEmbedEmbedder` never touches disk or the network by itself;
    offline tests that never call :meth:`embed` stay network-free even if this
    object is constructed.
    """

    dimension = FASTEMBED_DIMENSION

    def __init__(self, cache_dir: str | Path, *, model_name: str = FASTEMBED_MODEL) -> None:
        """Create the embedder.

        Args:
            cache_dir: Directory the ONNX model weights are cached under.
            model_name: The fastembed model id.
        """
        self._cache_dir = str(cache_dir)
        self._model_name = model_name
        self._model: object | None = None
        self._lock = asyncio.Lock()

    async def _get_model(self) -> object:
        if self._model is None:
            async with self._lock:
                if self._model is None:
                    from fastembed import TextEmbedding

                    log.info("fastembed_model_loading", model=self._model_name, cache_dir=self._cache_dir)
                    self._model = await asyncio.to_thread(
                        TextEmbedding, model_name=self._model_name, cache_dir=self._cache_dir
                    )
        return self._model

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed ``texts`` with the local ONNX model, off the event loop."""
        if not texts:
            return []
        model = await self._get_model()
        vectors = await asyncio.to_thread(lambda: list(model.embed(list(texts))))  # type: ignore[attr-defined]
        return [vector.tolist() for vector in vectors]


class OpenAIEmbedder:
    """Calls the OpenAI ``/embeddings`` REST endpoint directly over ``httpx``.

    The api does not depend on the ``openai`` SDK; this is a thin, testable
    HTTP client so the optional path stays lightweight.
    """

    dimension = OPENAI_EMBEDDING_DIMENSION

    def __init__(
        self,
        api_key: str,
        *,
        model: str = OPENAI_EMBEDDING_MODEL,
        base_url: str = "https://api.openai.com/v1",
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 30,
    ) -> None:
        """Create the embedder.

        Args:
            api_key: The decrypted OpenAI API key.
            model: The embedding model id.
            base_url: API base URL (overridable for testing/proxies).
            client: An injected client (tests); a short-lived one is opened otherwise.
            timeout_s: Request timeout.
        """
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._timeout_s = timeout_s

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed ``texts`` via the OpenAI embeddings endpoint."""
        if not texts:
            return []
        payload = {"model": self._model, "input": list(texts)}
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if self._client is not None:
            response = await self._client.post(
                f"{self._base_url}/embeddings", json=payload, headers=headers, timeout=self._timeout_s
            )
            response.raise_for_status()
            data = response.json()
        else:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.post(f"{self._base_url}/embeddings", json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        ordered = sorted(data["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in ordered]


class FakeEmbedder:
    """Deterministic, dependency-free embedder for offline tests.

    Hashes tokens into a fixed-size bag-of-words vector so that texts sharing
    vocabulary score higher on cosine similarity than unrelated texts — enough
    signal for "upload -> search returns the matching chunk" tests without any
    model download.
    """

    dimension = FAKE_EMBEDDER_DIMENSION

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return a hashed bag-of-tokens vector per text."""
        return [self._vector(text) for text in texts]

    @classmethod
    def _vector(cls, text: str) -> list[float]:
        vector = [0.0] * cls.dimension
        for token in _TOKEN_RE.findall(text.lower()):
            index = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16) % cls.dimension
            vector[index] += 1.0
        norm = math.sqrt(sum(component * component for component in vector)) or 1.0
        return [component / norm for component in vector]


_FASTEMBED_CACHE: dict[str, FastEmbedEmbedder] = {}


def get_fastembed_embedder(data_dir: str | Path) -> FastEmbedEmbedder:
    """Return the process-wide :class:`FastEmbedEmbedder`, cached by cache dir.

    Args:
        data_dir: ``LKAP_DATA_DIR``; the model cache lives at ``{data_dir}/models``.
    """
    cache_dir = str(Path(data_dir) / "models")
    if cache_dir not in _FASTEMBED_CACHE:
        _FASTEMBED_CACHE[cache_dir] = FastEmbedEmbedder(cache_dir)
    return _FASTEMBED_CACHE[cache_dir]


def clear_embedder_cache() -> None:
    """Drop cached embedder instances (tests only)."""
    _FASTEMBED_CACHE.clear()


#: The dotted class path of every registry embedding entry :func:`resolve_embedder`
#: can build with a credential (`openai-embedding`, `openrouter-embedding`).
OPENAI_EMBEDDER_CLASS = "lkap_api.kb.embed.OpenAIEmbedder"

#: The legacy ``LKAP_EMBEDDER=openai:<credential_id>`` scheme and the entry it means.
_LEGACY_OPENAI_SCHEME = "openai"
_LEGACY_OPENAI_PROVIDER = "openai-embedding"


def _openai_shaped_embedding_spec(provider_id: str) -> ProviderSpec | None:
    """The registry entry for ``provider_id`` if it is an OpenAI-shaped embedding provider."""
    try:
        spec = provider_registry.get(provider_id)
    except KeyError:
        return None
    if spec.kind != "embedding" or spec.python_class != OPENAI_EMBEDDER_CLASS:
        return None
    if spec.availability != "available":
        return None
    return spec


def _base_url_default(spec: ProviderSpec) -> str | None:
    for field in spec.fields:
        if field.name == "base_url" and isinstance(field.default, str) and field.default:
            return field.default
    return None


async def resolve_embedder(settings: Settings, db: AsyncSession, vault: Vault) -> Embedder:
    """Build the active :class:`Embedder` from ``LKAP_EMBEDDER``.

    Accepted values (D-V4-13):

    - ``fastembed`` (the default): the local :class:`FastEmbedEmbedder`.
    - ``<provider_id>:<credential_id>``: any available registry entry of kind
      ``embedding`` whose ``python_class`` is :class:`OpenAIEmbedder`
      (``openai-embedding``, ``openrouter-embedding``). It is built with the
      entry's ``default_model`` and its ``base_url`` field default, and the
      credential must be stored under the entry's credential home.
    - ``openai:<credential_id>``: the legacy form, kept as it always behaved
      (``openai-embedding``'s model and URL, credential looked up by id only).

    Args:
        settings: Service settings (``embedder`` holds ``LKAP_EMBEDDER``).
        db: Session used to look up the credential.
        vault: Decrypts the credential's secret bag.

    Returns:
        A :class:`FastEmbedEmbedder` or :class:`OpenAIEmbedder`.

    Raises:
        UnprocessableEntityError: If ``LKAP_EMBEDDER`` names an unsupported
            scheme or provider, an unknown credential, or a credential stored
            under another provider.
    """
    value = (settings.embedder or "fastembed").strip()
    if value in {"", "fastembed"}:
        return get_fastembed_embedder(settings.data_dir)
    scheme, separator, credential_id = value.partition(":")
    legacy = scheme == _LEGACY_OPENAI_SCHEME
    spec = _openai_shaped_embedding_spec(_LEGACY_OPENAI_PROVIDER if legacy else scheme)
    if not separator or not credential_id or spec is None:
        raise UnprocessableEntityError(f"unsupported LKAP_EMBEDDER value '{value}'")
    # LKAP_EMBEDDER is platform configuration, so the credential it names is
    # looked up by id in whichever workspace holds it (deliberately cross-workspace).
    credential = (
        await db.execute(
            select(Credential)
            .where(Credential.id == credential_id)
            .execution_options(**{CROSS_WORKSPACE_OPTION: True})
        )
    ).scalar_one_or_none()
    if credential is None:
        raise UnprocessableEntityError(f"LKAP_EMBEDDER references unknown credential '{credential_id}'")
    home = provider_registry.credential_home(spec)
    if not legacy and credential.provider_id != home:
        raise UnprocessableEntityError(
            f"LKAP_EMBEDDER credential '{credential_id}' belongs to provider "
            f"'{credential.provider_id}', not '{home}'"
        )
    secrets = vault.decrypt(credential.ciphertext)
    base_url = _base_url_default(spec)
    model = spec.default_model or OPENAI_EMBEDDING_MODEL
    if base_url is None:
        return OpenAIEmbedder(api_key=secrets["api_key"], model=model)
    return OpenAIEmbedder(api_key=secrets["api_key"], model=model, base_url=base_url)
