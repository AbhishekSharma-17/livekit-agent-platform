"""Knowledge connections at run time: from a row to a working store or re-ranker (V5-20).

:class:`ConnectionRuntime` is built per database session (by
:func:`lkap_api.kb.store.resolve_store`, so every caller of the store gets it
for free) and answers two questions:

* **Which store holds this knowledge base?** :meth:`ConnectionRuntime.store_for_kb`
  reads the knowledge base's ``connection_id`` and builds that connection's
  store (``None`` = the platform's own store). It **fails closed**: a knowledge
  base bound to a connection that is gone, has no key, or belongs to another
  workspace raises :class:`~lkap_api.knowledge_connections.http.ConnectorError`;
  it never falls back to the platform store, so a document is never silently
  written to (or searched in) the wrong place.
* **Which hosted re-ranker is ``connection:<id>``?** :meth:`ConnectionRuntime.reranker`
  builds it, refusing a connection of another workspace than the knowledge
  bases being searched.

Keys are decrypted from the vault only here, only when a store or re-ranker is
built, and never logged. Every vendor call goes through one guarded
``httpx.AsyncClient`` per event loop and network policy
(:func:`http_client`); tests swap the transport with :func:`use_transport`.
"""

from __future__ import annotations

import asyncio
import contextlib
import weakref
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from lkap_contracts.api_models import RERANKER_CONNECTION_KINDS, VECTOR_STORE_CONNECTION_KINDS
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import Credential, KnowledgeBase, KnowledgeConnection
from lkap_api.kb.rerankers.base import HostedReranker
from lkap_api.kb.rerankers.cohere import CohereReranker
from lkap_api.kb.rerankers.voyage import VoyageReranker
from lkap_api.kb.stores.pinecone import PineconeStore
from lkap_api.kb.stores.qdrant import QdrantStore, sparse_encoder
from lkap_api.kb.stores.weaviate import WeaviateStore
from lkap_api.knowledge_connections.http import ConnectorError
from lkap_api.knowledge_connections.settings import (
    AnySettings,
    CohereRerankSettings,
    PineconeSettings,
    QdrantSettings,
    VoyageRerankSettings,
    WeaviateSettings,
    load_settings,
)
from lkap_api.logging import get_logger
from lkap_api.net_guard import NetPolicy, guarded_http_client, policy_from_settings
from lkap_api.settings import Settings
from lkap_api.vault import Vault

log = get_logger(__name__)

#: Knowledge-connector stores and re-rankers (the three store classes plus the two re-rankers).
KnowledgeStore = QdrantStore | PineconeStore | WeaviateStore

# ------------------------------------------------------------------------------ the HTTP client
_CLIENTS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[NetPolicy, httpx.AsyncClient]] = (
    weakref.WeakKeyDictionary()
)
_TRANSPORT_OVERRIDE: list[httpx.AsyncBaseTransport] = []


def http_client(policy: NetPolicy) -> httpx.AsyncClient:
    """The process's guarded client for ``policy`` on the running event loop (created once).

    Redirects are never followed and every connection is checked against the
    resolved address (:class:`~lkap_api.net_guard.GuardedTransport`).
    """
    if _TRANSPORT_OVERRIDE:
        return httpx.AsyncClient(transport=_TRANSPORT_OVERRIDE[-1], follow_redirects=False)
    loop = asyncio.get_running_loop()
    clients = _CLIENTS.setdefault(loop, {})
    client = clients.get(policy)
    if client is None or client.is_closed:
        client = guarded_http_client(policy)
        clients[policy] = client
    return client


@contextlib.contextmanager
def use_transport(transport: httpx.AsyncBaseTransport) -> Iterator[None]:
    """Route every knowledge-connector call through ``transport`` (tests: an ``httpx.MockTransport``)."""
    _TRANSPORT_OVERRIDE.append(transport)
    try:
        yield
    finally:
        _TRANSPORT_OVERRIDE.pop()


# ------------------------------------------------------------------------------ loading
@dataclass(slots=True, frozen=True)
class LoadedConnection:
    """A connection row with its parsed settings and (decrypted) key."""

    id: str
    workspace_id: str
    kind: str
    settings: AnySettings
    api_key: str | None


async def load_connection(
    session: AsyncSession, vault: Vault, connection_id: str, *, workspace_id: str | None
) -> LoadedConnection:
    """Load and decrypt one connection.

    Args:
        session: Any session (the read is marked cross-workspace; ``workspace_id`` scopes it).
        vault: Decrypts the key.
        connection_id: The connection.
        workspace_id: When given, the connection must belong to it.

    Raises:
        ConnectorError: The connection does not exist (in that workspace) or
            its key is gone or unreadable.
    """
    statement = select(KnowledgeConnection).where(KnowledgeConnection.id == connection_id)
    if workspace_id is not None:
        statement = statement.where(KnowledgeConnection.workspace_id == workspace_id)
    else:
        statement = statement.execution_options(**{CROSS_WORKSPACE_OPTION: True})
    row = (await session.execute(statement)).scalar_one_or_none()
    if row is None:
        raise ConnectorError(f"knowledge connection '{connection_id}' does not exist", not_found=True)
    api_key: str | None = None
    if row.credential_id is not None:
        credential = (
            await session.execute(
                select(Credential).where(
                    Credential.id == row.credential_id, Credential.workspace_id == row.workspace_id
                )
            )
        ).scalar_one_or_none()
        if credential is None:
            raise ConnectorError(f"the key of knowledge connection '{row.name}' was deleted", auth=True)
        try:
            secrets = vault.decrypt(credential.ciphertext)
        except Exception as exc:  # noqa: BLE001 - never echo vault internals
            raise ConnectorError(
                f"the key of knowledge connection '{row.name}' cannot be read", auth=True
            ) from exc
        api_key = str(secrets.get("api_key") or "") or None
    return LoadedConnection(
        id=row.id,
        workspace_id=row.workspace_id,
        kind=row.kind,
        settings=load_settings(row.kind, dict(row.settings or {})),
        api_key=api_key,
    )


def build_store(
    loaded: LoadedConnection, client: httpx.AsyncClient, *, models_dir: str | None = None
) -> KnowledgeStore:
    """The vector store of a vector-store connection.

    Args:
        loaded: The connection.
        client: The guarded client every call goes through.
        models_dir: Where the Qdrant BM25 model is cached (native hybrid only).

    Raises:
        ConnectorError: The connection is a re-ranker, or needs a key it lacks.
    """
    settings = loaded.settings
    match settings:
        case QdrantSettings():
            sparse = sparse_encoder(models_dir or "models") if settings.native_hybrid else None
            return QdrantStore(
                client, settings, api_key=loaded.api_key, workspace_id=loaded.workspace_id, sparse=sparse
            )
        case PineconeSettings():
            if not loaded.api_key:
                raise ConnectorError("this Pinecone connection has no key", auth=True)
            return PineconeStore(client, settings, api_key=loaded.api_key)
        case WeaviateSettings():
            return WeaviateStore(client, settings, api_key=loaded.api_key)
        case _:
            raise ConnectorError(f"a '{loaded.kind}' connection is not a vector store")


def build_reranker(loaded: LoadedConnection, client: httpx.AsyncClient) -> HostedReranker:
    """The hosted re-ranker of a re-ranker connection.

    Raises:
        ConnectorError: The connection is a vector store, or has no key.
    """
    settings = loaded.settings
    if loaded.kind not in RERANKER_CONNECTION_KINDS:
        raise ConnectorError(f"a '{loaded.kind}' connection is not a re-ranking service")
    if not loaded.api_key:
        raise ConnectorError("this re-ranking connection has no key", auth=True)
    match settings:
        case CohereRerankSettings():
            return CohereReranker(client, api_key=loaded.api_key, model=settings.model)
        case VoyageRerankSettings():
            return VoyageReranker(client, api_key=loaded.api_key, model=settings.model)
        case _:  # pragma: no cover - the kind check above is exhaustive
            raise ConnectorError(f"a '{loaded.kind}' connection is not a re-ranking service")


# ------------------------------------------------------------------------------ the runtime
@dataclass(slots=True, frozen=True)
class _KbBinding:
    workspace_id: str
    connection_id: str | None


class ConnectionRuntime:
    """Builds the stores and re-rankers of one session's knowledge connections (see the module docstring)."""

    def __init__(self, settings: Settings, session: AsyncSession, vault: Vault | None = None) -> None:
        """Bind the runtime to a session; the vault defaults to one over ``LKAP_MASTER_KEY``."""
        self._settings = settings
        self._session = session
        self._vault = vault
        self._bindings: dict[str, _KbBinding | None] = {}
        self._stores: dict[str, KnowledgeStore] = {}

    @property
    def vault(self) -> Vault:
        """The vault the keys are decrypted with."""
        if self._vault is None:
            self._vault = Vault(self._settings.master_key)
        return self._vault

    def client(self) -> httpx.AsyncClient:
        """The guarded client for this process's network policy."""
        return http_client(policy_from_settings(self._settings))

    async def _binding(self, kb_id: str) -> _KbBinding | None:
        if kb_id not in self._bindings:
            statement = (
                select(KnowledgeBase.workspace_id, KnowledgeBase.connection_id)
                .where(KnowledgeBase.id == kb_id)
                .execution_options(**{CROSS_WORKSPACE_OPTION: True})
            )
            row = (await self._session.execute(statement)).first()
            self._bindings[kb_id] = (
                _KbBinding(workspace_id=str(row[0]), connection_id=row[1]) if row is not None else None
            )
        return self._bindings[kb_id]

    async def connection_id_for_kb(self, kb_id: str) -> str | None:
        """The connection a knowledge base is stored through (``None``: the platform's store, or no KB)."""
        binding = await self._binding(kb_id)
        return binding.connection_id if binding is not None else None

    async def store_for_connection(self, connection_id: str, *, workspace_id: str) -> KnowledgeStore:
        """The store of one vector-store connection of ``workspace_id`` (built once per runtime)."""
        store = self._stores.get(connection_id)
        if store is None:
            loaded = await load_connection(
                self._session, self.vault, connection_id, workspace_id=workspace_id
            )
            if loaded.kind not in VECTOR_STORE_CONNECTION_KINDS:
                raise ConnectorError(f"knowledge connection '{connection_id}' is not a vector store")
            store = build_store(
                loaded, self.client(), models_dir=str(Path(self._settings.data_dir) / "models")
            )
            self._stores[connection_id] = store
        return store

    async def store_for_kb(self, kb_id: str) -> KnowledgeStore | None:
        """The connection store holding ``kb_id``, or ``None`` for the platform's own store.

        Raises:
            ConnectorError: The knowledge base is bound to a connection that
                cannot be used (fail closed; never the platform store instead).
        """
        binding = await self._binding(kb_id)
        if binding is None or binding.connection_id is None:
            return None
        return await self.store_for_connection(binding.connection_id, workspace_id=binding.workspace_id)

    async def reranker(self, connection_id: str, *, kb_ids: list[str]) -> HostedReranker:
        """The hosted re-ranker ``connection:<id>`` names, for a search over ``kb_ids``.

        The connection must belong to the workspace of every knowledge base
        searched (a worker search carries no workspace; the knowledge bases do).

        Raises:
            ConnectorError: Unknown connection, another workspace's, not a re-ranker, or no key.
        """
        workspaces = {binding.workspace_id for kb_id in kb_ids if (binding := await self._binding(kb_id))}
        if len(workspaces) != 1:
            raise ConnectorError("a re-ranking service needs knowledge bases of one workspace")
        loaded = await load_connection(
            self._session, self.vault, connection_id, workspace_id=next(iter(workspaces))
        )
        return build_reranker(loaded, self.client())

    def snapshot(self) -> dict[str, Any]:
        """What is cached (tests and the debug log)."""
        return {"bindings": len(self._bindings), "stores": sorted(self._stores)}
