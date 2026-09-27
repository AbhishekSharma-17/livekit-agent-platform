"""The non-secret settings of each knowledge connection kind, and where a knowledge base lives (V5-20).

The console renders these from the kind's registry entry (``fields``); the api
validates them here, strictly (unknown keys are refused). The key is never a
setting: it is a vault credential of the kind's provider.

**Urls** (Qdrant, Weaviate) pass :func:`lkap_api.net_guard.check_url` at save
time (no private, loopback or metadata destination unless the operator allowed
it with ``LKAP_NET_ALLOW_PRIVATE_HOSTS``; the dev default allows ``localhost``,
so a local Docker Qdrant works) and must be ``https``. Plain ``http`` is
accepted only for a host the operator's allowlist names (a service on the
operator's own network). No user info, query or fragment. The connect-time
guard (:class:`~lkap_api.net_guard.GuardedTransport`) is the authoritative
check; this is the cheap early one. Knowledge connections never get the
self-hosted LiveKit widening (``NetPolicy.for_self_hosted``).

**Where a knowledge base lives** (``knowledge_bases.external_ref``) is derived
here from the connection and the knowledge base id, never taken from a client:

* Qdrant: one collection per connection, every point carrying ``kb_id`` and
  ``workspace_id`` in its payload (a tenant-indexed keyword field); a query
  always filters on both.
* Pinecone: one index per connection, one namespace per knowledge base
  (``kb_<id>``).
* Weaviate: one multi-tenant collection per connection, one tenant per
  knowledge base (``kb_<id>``).

Settings that decide that location (url, collection, index) cannot change while
knowledge bases are stored through the connection (the service answers 409).
"""

from __future__ import annotations

import ipaddress
from typing import Annotated, Any, Final, Literal
from urllib.parse import urlsplit, urlunsplit

from lkap_contracts.api_models import KnowledgeConnectionKind
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from lkap_api.errors import UnprocessableEntityError
from lkap_api.net_guard import NetPolicy, check_url

#: Model ids: the registry's id alphabet, short.
ModelId = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9._\-]{1,100}$")]


class _Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class QdrantSettings(_Settings):
    """A Qdrant cluster (Cloud or self-hosted)."""

    url: str
    collection: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")] = "lkap_knowledge"
    native_hybrid: bool = False


class PineconeSettings(_Settings):
    """A Pinecone serverless index (created on first use when it does not exist)."""

    index: Annotated[str, StringConstraints(pattern=r"^[a-z0-9](?:[a-z0-9-]{0,43}[a-z0-9])?$")]
    cloud: Literal["aws", "gcp", "azure"] = "aws"
    region: Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]{2,40}$")] = "us-east-1"


class WeaviateSettings(_Settings):
    """A Weaviate cluster (Cloud or self-hosted) with a multi-tenant collection."""

    url: str
    collection: Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Za-z0-9_]{0,63}$")] = "LkapKnowledge"
    native_hybrid: bool = False


class CohereRerankSettings(_Settings):
    """Cohere's re-rank endpoint."""

    model: ModelId = "rerank-v4.0-fast"


class VoyageRerankSettings(_Settings):
    """Voyage AI's re-rank endpoint."""

    model: ModelId = "rerank-2.5-lite"


AnySettings = (
    QdrantSettings | PineconeSettings | WeaviateSettings | CohereRerankSettings | VoyageRerankSettings
)

SETTINGS_MODELS: Final[dict[str, type[_Settings]]] = {
    "qdrant": QdrantSettings,
    "pinecone": PineconeSettings,
    "weaviate": WeaviateSettings,
    "cohere_rerank": CohereRerankSettings,
    "voyage_rerank": VoyageRerankSettings,
}

#: Settings that decide where a knowledge base's vectors are; frozen while knowledge bases exist.
LOCATION_FIELDS: Final[dict[str, frozenset[str]]] = {
    "qdrant": frozenset({"url", "collection"}),
    "pinecone": frozenset({"index"}),
    "weaviate": frozenset({"url", "collection"}),
    "cohere_rerank": frozenset(),
    "voyage_rerank": frozenset(),
}

#: Kinds that need a key; Qdrant and Weaviate may run without one (a local or private cluster).
KEY_REQUIRED: Final[frozenset[str]] = frozenset({"pinecone", "cohere_rerank", "voyage_rerank"})


def _http_allowed(host: str, policy: NetPolicy) -> bool:
    """Plain http only for a host the operator allowlisted (by name or network)."""
    if policy.host_exempt(host):
        return True
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return policy.address_exempt(address)


def normalise_url(raw: str, policy: NetPolicy, *, field: str = "settings.url") -> str:
    """Check a service url and return it without a trailing slash.

    Raises:
        UnprocessableEntityError: The url is malformed, not https (outside an
            operator-allowed host), carries credentials, a query or a fragment,
            or points somewhere the network guard refuses.
    """
    value = raw.strip()
    parsed = urlsplit(value)
    details = {"field": field}
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise UnprocessableEntityError("the url must be an absolute https address", details=details)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise UnprocessableEntityError(
            "the url must not carry a user name, password, query or fragment", details=details
        )
    if parsed.scheme == "http" and not _http_allowed(parsed.hostname, policy):
        raise UnprocessableEntityError(
            "the url must use https (plain http only for a host the operator allows with "
            "LKAP_NET_ALLOW_PRIVATE_HOSTS)",
            details=details,
        )
    problem = check_url(value, policy)
    if problem is not None:
        raise UnprocessableEntityError(problem, details={**details, "reason": "blocked_destination"})
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def parse_settings(
    kind: KnowledgeConnectionKind | str, raw: dict[str, Any], policy: NetPolicy
) -> AnySettings:
    """Validate ``raw`` for ``kind`` (urls checked against ``policy``).

    Raises:
        UnprocessableEntityError: Unknown kind, unknown or invalid field, or a refused url.
    """
    model = SETTINGS_MODELS.get(kind)
    if model is None:
        raise UnprocessableEntityError(f"unknown knowledge connection kind '{kind}'")
    data = dict(raw)
    if "url" in data and isinstance(data["url"], str):
        data["url"] = normalise_url(data["url"], policy)
    try:
        return model.model_validate(data)  # type: ignore[return-value]
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first.get("loc", ()))
        raise UnprocessableEntityError(
            f"invalid setting '{location or kind}': {first.get('msg', 'invalid value')}",
            details={"field": f"settings.{location}" if location else "settings"},
        ) from exc


def load_settings(kind: str, raw: dict[str, Any]) -> AnySettings:
    """Read stored settings back (validated at save; no network policy needed here)."""
    model = SETTINGS_MODELS[kind]
    return model.model_validate(raw)  # type: ignore[return-value]


def kb_namespace(kb_id: str) -> str:
    """The Pinecone namespace / Weaviate tenant of a knowledge base."""
    return f"kb_{kb_id}"


def external_ref(kind: str, settings: AnySettings, kb_id: str) -> str | None:
    """Where ``kb_id``'s vectors live in the connection (``knowledge_bases.external_ref``)."""
    match settings:
        case QdrantSettings():
            return settings.collection
        case PineconeSettings():
            return f"{settings.index}/{kb_namespace(kb_id)}"
        case WeaviateSettings():
            return f"{settings.collection}/{kb_namespace(kb_id)}"
        case _:
            return None


def target_name(settings: AnySettings) -> str | None:
    """The collection or index a vector-store connection uses (``None`` for a re-ranker)."""
    match settings:
        case QdrantSettings() | WeaviateSettings():
            return settings.collection
        case PineconeSettings():
            return settings.index
        case _:
            return None


def public_settings(settings: AnySettings) -> dict[str, Any]:
    """The settings as stored and returned (every default filled in)."""
    return settings.model_dump(mode="json")
