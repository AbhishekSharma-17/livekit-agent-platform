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

V5-01 (PLAN-V5, K §2 P0-7): model names come from settings, never module
constants — ``LKAP_EMBED_MODEL`` picks the local fastembed model and
``LKAP_RERANK_MODEL`` the local reranker (read by V5-04). Every embedder
reports ``dimension`` and ``model_id``; a knowledge base records both when it
is created (``knowledge_bases.dimension`` / ``.embedder_model``) and
:func:`check_kb_embedder` refuses a query or an ingest whose embedder no
longer matches (``422 kb_embedder_mismatch``). A knowledge base created
before V5-01 has neither recorded (``NULL``) and is never refused: it keeps
working exactly as before and adopts the values on its next successful ingest.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final, Protocol, runtime_checkable

import httpx
from lkap_contracts import providers as provider_registry
from lkap_contracts.providers import ProviderSpec
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import Credential, KnowledgeBase
from lkap_api.errors import UnprocessableEntityError
from lkap_api.logging import get_logger
from lkap_api.settings import DEFAULT_EMBED_MODEL, Settings
from lkap_api.vault import Vault

log = get_logger(__name__)

#: The fallback when no model is named at all; ``Settings.embed_model``
#: (``LKAP_EMBED_MODEL``) is the real source. Kept so code that builds an
#: embedder without settings (tests, scripts) gets the documented model.
FASTEMBED_MODEL = DEFAULT_EMBED_MODEL
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
FAKE_EMBEDDER_DIMENSION = 32
FAKE_EMBEDDER_MODEL_ID = "fake-hashed-tokens-32"

#: Output widths of the OpenAI-shaped models the registry ships (a routed
#: ``openai/<model>`` id resolves through its last segment). Any other model's
#: width is learned from its first response (``OpenAIEmbedder.dimension`` is
#: ``None`` until then).
KNOWN_OPENAI_DIMENSIONS: Final[dict[str, int]] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")
#: The heuristic token counter's pattern: a word or one punctuation mark,
#: roughly one WordPiece token each (a lower bound for long rare words).
_APPROX_TOKEN_RE = re.compile(r"\w+|[^\w\s]")

#: Counts the tokens of one text with an embedder's tokenizer.
TokenCounter = Callable[[str], int]


@runtime_checkable
class Embedder(Protocol):
    """Turns text into fixed-length vectors for the vector store.

    ``dimension`` is the vector width (``None`` only while a remote model's
    width is still unknown, before its first response); ``model_id`` names the
    model so a knowledge base can record which one built it.
    """

    @property
    def dimension(self) -> int | None:
        """The output vector width, when known."""
        ...

    @property
    def model_id(self) -> str:
        """The embedding model's id (``BAAI/bge-small-en-v1.5``, ``text-embedding-3-small``, ...)."""
        ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one embedding vector per input text, in order."""
        ...


def approx_token_count(text: str) -> int:
    """A dependency-free token estimate: one per word or punctuation mark.

    Used by the chunker when the embedder has no tokenizer of its own (the
    OpenAI-shaped path and :class:`FakeEmbedder`). WordPiece and BPE both split
    rare words further, so this undercounts slightly; the chunk budget is a
    target, not a hard model limit (bge-small truncates at 512 tokens).
    """
    return len(_APPROX_TOKEN_RE.findall(text))


async def token_counter_for(embedder: Embedder) -> TokenCounter:
    """The token counter the chunker measures its budget with, for ``embedder``.

    An embedder exposing ``async token_counter() -> TokenCounter`` (the local
    fastembed model, whose tokenizer loads with the model) is used as is; any
    other embedder falls back to :func:`approx_token_count`.
    """
    factory = getattr(embedder, "token_counter", None)
    if factory is None:
        return approx_token_count
    counter: TokenCounter = await factory()
    return counter


class FastEmbedEmbedder:
    """Local ONNX embedder (``fastembed``), model cached under ``LKAP_DATA_DIR/models``.

    The model is loaded lazily on first use (not at construction) so building
    a :class:`FastEmbedEmbedder` never touches disk or the network by itself;
    offline tests that never call :meth:`embed` stay network-free even if this
    object is constructed.
    """

    def __init__(self, cache_dir: str | Path, *, model_name: str = FASTEMBED_MODEL) -> None:
        """Create the embedder.

        The output width comes from fastembed's model registry, which needs
        neither the weights nor the network.

        Args:
            cache_dir: Directory the ONNX model weights are cached under.
            model_name: The fastembed model id (``LKAP_EMBED_MODEL``).

        Raises:
            UnprocessableEntityError: ``model_name`` is not a fastembed text model.
        """
        self._cache_dir = str(cache_dir)
        self._model_name = model_name
        self._dimension = fastembed_dimension(model_name)
        self._model: object | None = None
        self._lock = asyncio.Lock()

    @property
    def dimension(self) -> int:
        """The model's output width (384 for ``BAAI/bge-small-en-v1.5``)."""
        return self._dimension

    @property
    def model_id(self) -> str:
        """The fastembed model id."""
        return self._model_name

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

    async def warm(self) -> None:
        """Load (and on a cold cache, download) the model now instead of on first use.

        V4-18 (R-V4-65): called by the api lifespan at startup and by the
        ``kb_ingest`` job before its ingest session opens, so the download
        never sits inside a database transaction.

        Raises:
            Exception: Whatever the model load raised (a failed download).
        """
        await self._get_model()

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed ``texts`` with the local ONNX model, off the event loop."""
        if not texts:
            return []
        model = await self._get_model()
        vectors = await asyncio.to_thread(lambda: list(model.embed(list(texts))))  # type: ignore[attr-defined]
        return [vector.tolist() for vector in vectors]

    async def token_counter(self) -> TokenCounter:
        """A counter using the model's own tokenizer (loads the model once).

        fastembed counts the ``[CLS]``/``[SEP]`` specials on every call, so the
        count of an empty text is subtracted. Its tokenizer truncates at the
        model's window (512), which still reads as "over budget" for any chunk
        budget below that.
        """
        model = await self._get_model()
        count = model.token_count  # type: ignore[attr-defined]
        specials = int(count(""))

        def counter(text: str) -> int:
            return max(int(count(text)) - specials, 0)

        return counter


class OpenAIEmbedder:
    """Calls the OpenAI ``/embeddings`` REST endpoint directly over ``httpx``.

    The api does not depend on the ``openai`` SDK; this is a thin, testable
    HTTP client so the optional path stays lightweight.
    """

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
        self._dimension: int | None = KNOWN_OPENAI_DIMENSIONS.get(model.rsplit("/", 1)[-1])

    @property
    def dimension(self) -> int | None:
        """The output width: known for the registry's models, else learned from the first response."""
        return self._dimension

    @property
    def model_id(self) -> str:
        """The model id sent to the endpoint (``openai/text-embedding-3-small`` through a router)."""
        return self._model

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
        vectors: list[list[float]] = [item["embedding"] for item in ordered]
        if self._dimension is None and vectors:
            self._dimension = len(vectors[0])
        return vectors


class FakeEmbedder:
    """Deterministic, dependency-free embedder for offline tests.

    Hashes tokens into a fixed-size bag-of-words vector so that texts sharing
    vocabulary score higher on cosine similarity than unrelated texts — enough
    signal for "upload -> search returns the matching chunk" tests without any
    model download.
    """

    dimension = FAKE_EMBEDDER_DIMENSION
    model_id = FAKE_EMBEDDER_MODEL_ID

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


def fastembed_dimension(model_name: str) -> int:
    """The output width of fastembed text model ``model_name``, from its registry (no download).

    Raises:
        UnprocessableEntityError: The name is not one of fastembed's text embedding models.
    """
    from fastembed import TextEmbedding

    try:
        return int(TextEmbedding.get_embedding_size(model_name))
    except ValueError as exc:
        raise UnprocessableEntityError(
            f"LKAP_EMBED_MODEL '{model_name}' is not a fastembed text embedding model",
            details={"field": "LKAP_EMBED_MODEL"},
        ) from exc


_FASTEMBED_CACHE: dict[tuple[str, str], FastEmbedEmbedder] = {}


def get_fastembed_embedder(data_dir: str | Path, model_name: str = FASTEMBED_MODEL) -> FastEmbedEmbedder:
    """Return the process-wide :class:`FastEmbedEmbedder`, cached by cache dir and model.

    Args:
        data_dir: ``LKAP_DATA_DIR``; the model cache lives at ``{data_dir}/models``.
        model_name: The fastembed model id (``LKAP_EMBED_MODEL``).
    """
    cache_dir = str(Path(data_dir) / "models")
    key = (cache_dir, model_name)
    if key not in _FASTEMBED_CACHE:
        _FASTEMBED_CACHE[key] = FastEmbedEmbedder(cache_dir, model_name=model_name)
    return _FASTEMBED_CACHE[key]


def clear_embedder_cache() -> None:
    """Drop cached embedder instances (tests only)."""
    _FASTEMBED_CACHE.clear()


def uses_fastembed(settings: Settings) -> bool:
    """Whether ``LKAP_EMBEDDER`` selects the local fastembed model (the default)."""
    return (settings.embedder or "fastembed").strip() in {"", "fastembed"}


async def warm_default_embedder(settings: Settings) -> None:
    """Load the process-wide fastembed model once; never raises (V4-18, R-V4-65).

    The api lifespan runs this as a fire-and-forget task at startup, so the
    first knowledge ingest (a starter's seeds, an upload) finds the model
    loaded. A remote embedder (``LKAP_EMBEDDER=<provider>:<credential>``) has
    nothing to load and is skipped. A failure (no network on a cold cache, a
    bad ``LKAP_EMBED_MODEL``) is logged as ``fastembed_warmup_failed``; the
    ingest job retries the load before its own first write.
    """
    if not uses_fastembed(settings):
        return
    model = settings.embed_model or FASTEMBED_MODEL
    try:
        await get_fastembed_embedder(settings.data_dir, model).warm()
    except Exception as exc:  # noqa: BLE001 - a warm-up must never fail startup
        log.warning("fastembed_warmup_failed", model=model, error_type=type(exc).__name__)
        return
    log.info("fastembed_warmup_done", model=model)


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

    - ``fastembed`` (the default): the local :class:`FastEmbedEmbedder`
      running ``LKAP_EMBED_MODEL`` (default ``BAAI/bge-small-en-v1.5``).
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
    if uses_fastembed(settings):
        return get_fastembed_embedder(settings.data_dir, settings.embed_model or FASTEMBED_MODEL)
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


# --------------------------------------------------------------------------- per-KB embedder check (V5-01)
class KbEmbedderMismatchError(UnprocessableEntityError):
    """422 — a knowledge base was built by another embedder than the one configured now."""

    code = "kb_embedder_mismatch"


def kb_embedder_mismatch(kb: KnowledgeBase, embedder: Embedder) -> str | None:
    """Why ``embedder`` cannot serve ``kb``, or ``None`` when it can.

    Only what the knowledge base recorded is compared: a ``NULL`` dimension or
    model (every knowledge base created before V5-01) is never a mismatch, so a
    pre-existing knowledge base keeps working unchanged. A remote embedder whose
    width is not known yet is compared by model only.
    """
    if kb.dimension is not None and embedder.dimension is not None and kb.dimension != embedder.dimension:
        return (
            f"knowledge base '{kb.name}' holds {kb.dimension}-dimension vectors but the configured "
            f"embedder '{embedder.model_id}' produces {embedder.dimension}"
        )
    if kb.embedder_model is not None and kb.embedder_model != embedder.model_id:
        return (
            f"knowledge base '{kb.name}' was built with embedder '{kb.embedder_model}' but the "
            f"configured embedder is '{embedder.model_id}'"
        )
    return None


def check_kb_embedder(kb: KnowledgeBase, embedder: Embedder) -> None:
    """Refuse ``embedder`` on ``kb`` when they do not match (see :func:`kb_embedder_mismatch`).

    Raises:
        KbEmbedderMismatchError: 422 ``kb_embedder_mismatch``; the message and
            ``details`` name the knowledge base (never a secret).
    """
    reason = kb_embedder_mismatch(kb, embedder)
    if reason is None:
        return
    raise KbEmbedderMismatchError(
        f"{reason}; re-create the knowledge base or configure the embedder it was built with",
        details={
            "kb_id": kb.id,
            "kb_name": kb.name,
            "kb_dimension": kb.dimension,
            "kb_embedder_model": kb.embedder_model,
            "embedder_dimension": embedder.dimension,
            "embedder_model": embedder.model_id,
        },
    )


def record_kb_embedder(kb: KnowledgeBase, embedder: Embedder) -> None:
    """Record ``embedder``'s model and width on ``kb`` where they are still unset."""
    if kb.dimension is None and embedder.dimension is not None:
        kb.dimension = embedder.dimension
    if kb.embedder_model is None:
        kb.embedder_model = embedder.model_id
