"""Knowledge connections: create, read, update, delete and test (V5-20, K §5.2/§5.3).

Rules the routes rely on:

* **Secrets are write-only.** A connection references a vault credential of
  its kind's provider (``credential_id``); the row returns only that key's
  fingerprint. Keys are decrypted only to build a store or re-ranker
  (:mod:`~lkap_api.knowledge_connections.runtime`) and never logged, audited
  or echoed; vendor errors are scrubbed before they reach ``last_error``.
* **Workspace-bound.** Every read and write filters on the caller's workspace;
  the key must be of the same workspace; a knowledge base may only be bound to
  a vector-store connection of its own workspace (:func:`bind_for_kb`).
* **Location is fixed while in use.** The settings that decide where vectors
  live (url, collection, index) cannot change while knowledge bases are
  stored through the connection (409), and a connection with knowledge bases
  cannot be deleted (409 naming them).
* **Test connection** lists the collections or indexes the key can see, checks
  the width of the one the connection uses against the embedder knowledge bases
  are built with (a mismatch is an error naming the collection or index), and
  records ``status``, ``last_error``, ``last_checked_at`` and ``capabilities``
  on the row. Re-rankers check the key (Cohere's free key check; one tiny
  Voyage re-rank). Every test and change writes an audit row carrying the
  service's host, never the key.
* **Managed search (V5-45).** A Ragie connection's test lists the partitions
  the key can see (the first 100) in ``collections``; a knowledge base of
  ``kind="external"`` binds to it with a partition (:func:`bind_for_kb`), and a
  managed knowledge base can never be stored through it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from lkap_contracts.api_models import (
    EXTERNAL_RETRIEVER_CONNECTION_KINDS,
    KNOWLEDGE_CONNECTION_PROVIDER_IDS,
    RERANKER_CONNECTION_KINDS,
    VECTOR_STORE_CONNECTION_KINDS,
    KnowledgeConnectionCapabilities,
    KnowledgeConnectionCreate,
    KnowledgeConnectionOut,
    KnowledgeConnectionPage,
    KnowledgeConnectionTestOut,
    KnowledgeConnectionUpdate,
)
from lkap_contracts.providers import credential_home
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.auth import audit
from lkap_api.auth.deps import WorkspaceContext
from lkap_api.db.models import Credential, KnowledgeBase, KnowledgeConnection, utcnow
from lkap_api.errors import ConflictError, NotFoundError, UnprocessableEntityError
from lkap_api.kb.external.ragie import RagieApi
from lkap_api.kb.rerankers.cohere import CohereReranker
from lkap_api.kb.rerankers.voyage import VoyageReranker
from lkap_api.kb.stores.pinecone import PineconeStore
from lkap_api.kb.stores.qdrant import QdrantStore, dense_config
from lkap_api.kb.stores.weaviate import WeaviateStore
from lkap_api.knowledge_connections.http import ConnectorError
from lkap_api.knowledge_connections.runtime import (
    LoadedConnection,
    build_reranker,
    build_store,
    http_client,
    load_connection,
)
from lkap_api.knowledge_connections.settings import (
    KEY_REQUIRED,
    LOCATION_FIELDS,
    AnySettings,
    check_external_ref,
    external_ref,
    load_settings,
    parse_settings,
    public_settings,
    target_name,
)
from lkap_api.logging import get_logger
from lkap_api.net_guard import policy_from_settings
from lkap_api.settings import Settings
from lkap_api.vault import Vault

log = get_logger(__name__)

#: How many ``Test connection`` calls a workspace may make per minute.
TESTS_PER_MIN = 10
#: How many knowledge base names a 409 lists.
MAX_NAMED_KBS = 20


# ------------------------------------------------------------------------------ helpers
def _host(settings: dict[str, Any]) -> str | None:
    url = settings.get("url")
    return urlsplit(url).hostname if isinstance(url, str) else None


def _audit(
    db: AsyncSession, ctx: WorkspaceContext, action: str, row: KnowledgeConnection, **payload: Any
) -> None:
    audit.record(
        db,
        workspace_id=ctx.workspace_id,
        actor_type=ctx.actor.actor_type,
        actor_id=ctx.actor.id,
        action=action,
        target_type="knowledge_connection",
        target_id=row.id,
        payload={"kind": row.kind, "host": _host(dict(row.settings or {})), **payload},
    )


async def _load(db: AsyncSession, ctx: WorkspaceContext, connection_id: str) -> KnowledgeConnection:
    row = (
        await db.execute(
            select(KnowledgeConnection).where(
                KnowledgeConnection.id == connection_id, KnowledgeConnection.workspace_id == ctx.workspace_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"unknown knowledge connection '{connection_id}'")
    return row


async def _credential(db: AsyncSession, ctx: WorkspaceContext, kind: str, credential_id: str) -> Credential:
    row = (
        await db.execute(
            select(Credential).where(
                Credential.id == credential_id, Credential.workspace_id == ctx.workspace_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise UnprocessableEntityError(
            f"unknown credential '{credential_id}'", details={"field": "credential_id"}
        )
    expected = credential_home(KNOWLEDGE_CONNECTION_PROVIDER_IDS[kind])
    if row.provider_id != expected:
        raise UnprocessableEntityError(
            f"credential '{credential_id}' is a '{row.provider_id}' key; this connection needs a "
            f"'{expected}' key",
            details={"field": "credential_id"},
        )
    return row


async def _kbs_of(db: AsyncSession, ctx: WorkspaceContext, connection_id: str) -> list[KnowledgeBase]:
    rows = await db.execute(
        select(KnowledgeBase)
        .where(KnowledgeBase.workspace_id == ctx.workspace_id, KnowledgeBase.connection_id == connection_id)
        .order_by(KnowledgeBase.name)
    )
    return list(rows.scalars())


async def _fingerprints(db: AsyncSession, ctx: WorkspaceContext, ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    rows = await db.execute(
        select(Credential.id, Credential.fingerprint).where(
            Credential.workspace_id == ctx.workspace_id, Credential.id.in_(ids)
        )
    )
    return {str(cid): str(fp) for cid, fp in rows.all()}


def _to_out(row: KnowledgeConnection, *, fingerprint: str | None, kb_count: int) -> KnowledgeConnectionOut:
    return KnowledgeConnectionOut(
        id=row.id,
        name=row.name,
        kind=row.kind,
        provider_id=KNOWLEDGE_CONNECTION_PROVIDER_IDS.get(row.kind, row.kind),
        settings=dict(row.settings or {}),
        credential_id=row.credential_id,
        credential_fingerprint=fingerprint,
        status=row.status,
        last_checked_at=row.last_checked_at,
        last_error=row.last_error,
        capabilities=KnowledgeConnectionCapabilities.model_validate(row.capabilities or {}),
        knowledge_base_count=kb_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _out(db: AsyncSession, ctx: WorkspaceContext, row: KnowledgeConnection) -> KnowledgeConnectionOut:
    fingerprints = await _fingerprints(db, ctx, {row.credential_id} if row.credential_id else set())
    count = len(await _kbs_of(db, ctx, row.id))
    return _to_out(row, fingerprint=fingerprints.get(row.credential_id or ""), kb_count=count)


def _declared_capabilities(kind: str, settings: AnySettings) -> KnowledgeConnectionCapabilities:
    """What the connection does before any test (a test adds the dimension and version)."""
    if kind in RERANKER_CONNECTION_KINDS:
        return KnowledgeConnectionCapabilities(rerank=True)
    if kind in EXTERNAL_RETRIEVER_CONNECTION_KINDS:
        # Ragie fuses keyword and meaning matches, filters on metadata and returns the text itself.
        return KnowledgeConnectionCapabilities(
            managed_search=True, hybrid=True, filters=True, stores_text=True, namespaces=True
        )
    native = bool(getattr(settings, "native_hybrid", False))
    return KnowledgeConnectionCapabilities(
        hybrid=native, filters=True, stores_text=False, namespaces=kind in {"pinecone", "weaviate"}
    )


# ------------------------------------------------------------------------------ reads
async def list_connections(db: AsyncSession, ctx: WorkspaceContext) -> KnowledgeConnectionPage:
    """Every knowledge connection of the workspace, by name."""
    rows = list(
        (
            await db.execute(
                select(KnowledgeConnection)
                .where(KnowledgeConnection.workspace_id == ctx.workspace_id)
                .order_by(KnowledgeConnection.name, KnowledgeConnection.id)
            )
        ).scalars()
    )
    fingerprints = await _fingerprints(db, ctx, {row.credential_id for row in rows if row.credential_id})
    counts: dict[str, int] = {
        str(connection_id): int(count)
        for connection_id, count in (
            await db.execute(
                select(KnowledgeBase.connection_id, func.count(KnowledgeBase.id))
                .where(
                    KnowledgeBase.workspace_id == ctx.workspace_id, KnowledgeBase.connection_id.is_not(None)
                )
                .group_by(KnowledgeBase.connection_id)
            )
        ).all()
    }
    items = [
        _to_out(
            row,
            fingerprint=fingerprints.get(row.credential_id or ""),
            kb_count=int(counts.get(row.id, 0)),
        )
        for row in rows
    ]
    return KnowledgeConnectionPage(items=items, total=len(items))


async def get_connection(
    db: AsyncSession, ctx: WorkspaceContext, connection_id: str
) -> KnowledgeConnectionOut:
    """One connection of the workspace (404 for another workspace's)."""
    return await _out(db, ctx, await _load(db, ctx, connection_id))


# ------------------------------------------------------------------------------ writes
async def create_connection(
    db: AsyncSession, settings: Settings, ctx: WorkspaceContext, payload: KnowledgeConnectionCreate
) -> KnowledgeConnectionOut:
    """Validate and store a new connection (``unverified`` until tested)."""
    parsed = parse_settings(payload.kind, payload.settings, policy_from_settings(settings))
    if payload.credential_id is not None:
        await _credential(db, ctx, payload.kind, payload.credential_id)
    elif payload.kind in KEY_REQUIRED:
        raise UnprocessableEntityError(
            "this kind of connection needs a key (credential_id)", details={"field": "credential_id"}
        )
    row = KnowledgeConnection(
        workspace_id=ctx.workspace_id,
        name=payload.name.strip(),
        kind=payload.kind,
        settings=public_settings(parsed),
        credential_id=payload.credential_id,
        status="unverified",
        capabilities=_declared_capabilities(payload.kind, parsed).model_dump(mode="json"),
    )
    db.add(row)
    await db.flush()
    _audit(db, ctx, "knowledge_connection.create", row)
    log.info("knowledge_connection_created", connection_id=row.id, kind=row.kind)
    return await _out(db, ctx, row)


async def update_connection(
    db: AsyncSession,
    settings: Settings,
    ctx: WorkspaceContext,
    connection_id: str,
    payload: KnowledgeConnectionUpdate,
) -> KnowledgeConnectionOut:
    """Change the name, settings or key; a change to settings or key resets the status to ``unverified``."""
    row = await _load(db, ctx, connection_id)
    changed: list[str] = []
    if payload.name is not None:
        row.name = payload.name.strip()
        changed.append("name")
    if payload.settings is not None:
        parsed = parse_settings(row.kind, payload.settings, policy_from_settings(settings))
        new = public_settings(parsed)
        old = dict(row.settings or {})
        moved = sorted(
            field for field in LOCATION_FIELDS.get(row.kind, frozenset()) if old.get(field) != new.get(field)
        )
        if moved:
            kbs = await _kbs_of(db, ctx, row.id)
            if kbs:
                raise ConflictError(
                    f"'{', '.join(moved)}' cannot change while knowledge bases are stored through this "
                    "connection; move them first",
                    details={"fields": moved, "knowledge_bases": [kb.name for kb in kbs[:MAX_NAMED_KBS]]},
                )
        row.settings = new
        row.capabilities = _declared_capabilities(row.kind, parsed).model_dump(mode="json")
        changed.append("settings")
    if "credential_id" in payload.model_fields_set:
        if payload.credential_id is not None:
            await _credential(db, ctx, row.kind, payload.credential_id)
        elif row.kind in KEY_REQUIRED:
            raise UnprocessableEntityError(
                "this kind of connection needs a key (credential_id)", details={"field": "credential_id"}
            )
        row.credential_id = payload.credential_id
        changed.append("credential_id")
    if "settings" in changed or "credential_id" in changed:
        row.status = "unverified"
        row.last_error = None
    row.updated_at = utcnow()
    await db.flush()
    _audit(db, ctx, "knowledge_connection.update", row, fields=changed)
    return await _out(db, ctx, row)


async def delete_connection(db: AsyncSession, ctx: WorkspaceContext, connection_id: str) -> None:
    """Delete a connection nothing is stored through (409 naming the knowledge bases otherwise).

    Agents whose ``knowledge.rerank`` names a re-ranker connection are not
    blocked: their searches report ``rerank_failed`` and keep the fused order.
    """
    row = await _load(db, ctx, connection_id)
    kbs = await _kbs_of(db, ctx, row.id)
    if kbs:
        names = [kb.name for kb in kbs[:MAX_NAMED_KBS]]
        raise ConflictError(
            f"knowledge connection '{row.name}' still stores {len(kbs)} knowledge base(s): "
            f"{', '.join(names)}",
            details={"knowledge_bases": [{"id": kb.id, "name": kb.name} for kb in kbs[:MAX_NAMED_KBS]]},
        )
    _audit(db, ctx, "knowledge_connection.delete", row)
    await db.delete(row)
    await db.flush()


# ------------------------------------------------------------------------------ test
@dataclass(slots=True)
class _Probe:
    message: str
    collections: list[str]
    target: str | None = None
    target_exists: bool | None = None
    dimension_found: int | None = None
    version: str | None = None
    error: str | None = None


async def _probe_store(store: object, expected: int | None) -> _Probe:
    match store:
        case QdrantStore():
            collections = await store.list_collections()
            version = await store.version()
            info = await store.collection_info()
            width = dense_config(info)[0] if info is not None else None
            probe = _Probe("", collections, store.collection, info is not None, width, version)
            label = f"Qdrant collection '{store.collection}'"
        case PineconeStore():
            indexes = await store.list_indexes()
            names = sorted(str(item.get("name")) for item in indexes if item.get("name"))
            mine = next((item for item in indexes if item.get("name") == store.index), None)
            width = mine.get("dimension") if mine is not None else None
            probe = _Probe(
                "", names, store.index, mine is not None, width if isinstance(width, int) else None
            )
            metric = mine.get("metric") if mine is not None else None
            if isinstance(metric, str) and metric != "cosine":
                probe.error = f"Pinecone index '{store.index}' uses the {metric} metric; it must use cosine"
            label = f"Pinecone index '{store.index}'"
        case WeaviateStore():
            collections = await store.list_collections()
            version = await store.version()
            schema = await store.schema()
            probe = _Probe("", collections, store.collection, schema is not None, None, version)
            if schema is not None:
                config = schema.get("multiTenancyConfig")
                if not (isinstance(config, dict) and config.get("enabled") is True):
                    probe.error = (
                        f"Weaviate collection '{store.collection}' does not have multi-tenancy enabled; "
                        "use a new collection name and the platform creates it"
                    )
            label = f"Weaviate collection '{store.collection}'"
        case _:  # pragma: no cover - build_store returns one of the three
            raise ConnectorError("not a vector store")
    if probe.error is None and probe.dimension_found is not None and expected is not None:
        if probe.dimension_found != expected:
            probe.error = (
                f"{label} holds {probe.dimension_found}-dimension vectors, but knowledge bases here are "
                f"built with {expected}-dimension vectors; pick another collection or index, or change "
                "the embedder"
            )
    if probe.error is None:
        if probe.target_exists:
            holds = (
                f"{probe.dimension_found}-dimension vectors" if probe.dimension_found else "any vector width"
            )
            probe.message = f"Connected. {label} exists and holds {holds}."
        else:
            probe.message = (
                f"Connected. {label} does not exist yet; it is created with the first knowledge base."
            )
    return probe


async def _probe_ragie(loaded: LoadedConnection, client: httpx.AsyncClient) -> _Probe:
    if not loaded.api_key:
        raise ConnectorError("this Ragie connection has no key", auth=True)
    partitions, total = await RagieApi(client, api_key=loaded.api_key).list_partitions()
    names = [str(item["name"]) for item in partitions if isinstance(item.get("name"), str)]
    count = total if total is not None else len(names)
    if not names:
        return _Probe("Connected. The key works; Ragie has no partitions yet.", [])
    shown = "" if count <= len(names) else f" (showing the first {len(names)})"
    return _Probe(f"Connected. Ragie has {count} partition(s){shown}: {', '.join(names[:10])}.", names)


async def test_connection(
    db: AsyncSession,
    vault: Vault,
    settings: Settings,
    ctx: WorkspaceContext,
    connection_id: str,
    *,
    expected_dimension: int | None,
) -> KnowledgeConnectionTestOut:
    """Call the service, record the outcome on the row and return it (never raises for a vendor failure)."""
    row = await _load(db, ctx, connection_id)
    parsed = load_settings(row.kind, dict(row.settings or {}))
    capabilities = _declared_capabilities(row.kind, parsed)
    checked_at = utcnow()
    client = http_client(policy_from_settings(settings))
    probe = _Probe("", [])
    try:
        loaded = await load_connection(db, vault, row.id, workspace_id=ctx.workspace_id)
        if row.kind in VECTOR_STORE_CONNECTION_KINDS:
            probe = await _probe_store(build_store(loaded, client), expected_dimension)
            capabilities.dimension = probe.dimension_found
            capabilities.version = probe.version
        elif row.kind in EXTERNAL_RETRIEVER_CONNECTION_KINDS:  # V5-45
            probe = await _probe_ragie(loaded, client)
        else:
            reranker = build_reranker(loaded, client)
            assert isinstance(reranker, CohereReranker | VoyageReranker)
            probe = _Probe(await reranker.check(), [])
    except ConnectorError as exc:
        probe.error = exc.message
    ok = probe.error is None
    row.status = "ok" if ok else "error"
    row.last_error = None if ok else probe.error
    row.last_checked_at = checked_at
    row.capabilities = capabilities.model_dump(mode="json")
    await db.flush()
    _audit(db, ctx, "knowledge_connection.test", row, ok=ok)
    log.info("knowledge_connection_tested", connection_id=row.id, kind=row.kind, ok=ok)
    return KnowledgeConnectionTestOut(
        ok=ok,
        status=row.status,
        message=probe.message if ok else (probe.error or "the test failed"),
        collections=probe.collections,
        target=probe.target if probe.target is not None else target_name(parsed),
        target_exists=probe.target_exists,
        dimension_expected=expected_dimension if row.kind in VECTOR_STORE_CONNECTION_KINDS else None,
        dimension_found=probe.dimension_found,
        capabilities=capabilities,
        checked_at=checked_at,
    )


# ------------------------------------------------------------------------------ knowledge bases
@dataclass(slots=True, frozen=True)
class KbBinding:
    """What a new knowledge base records when it is stored through a connection."""

    connection_id: str
    external_ref: str | None


async def bind_for_kb(
    db: AsyncSession,
    workspace_id: str,
    connection_id: str,
    kb_id: str,
    *,
    kb_kind: str = "managed",
    requested_ref: str | None = None,
) -> KbBinding:
    """Check that a knowledge base of ``workspace_id`` may be stored through ``connection_id``.

    A ``managed`` knowledge base needs a vector store (its location is derived
    here); an ``external`` one (V5-45) needs a managed search service and names
    where it reads (``requested_ref``, a Ragie partition).

    Raises:
        UnprocessableEntityError: Unknown connection (in this workspace), the wrong
            kind of connection, or an invalid or missing partition.
    """
    row = (
        await db.execute(
            select(KnowledgeConnection).where(
                KnowledgeConnection.id == connection_id, KnowledgeConnection.workspace_id == workspace_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise UnprocessableEntityError(
            f"unknown knowledge connection '{connection_id}'", details={"field": "connection_id"}
        )
    if kb_kind == "external":
        if row.kind not in EXTERNAL_RETRIEVER_CONNECTION_KINDS:
            raise UnprocessableEntityError(
                f"knowledge connection '{row.name}' is not a managed search service; a managed search "
                "knowledge base needs a Ragie connection",
                details={"field": "connection_id"},
            )
        return KbBinding(connection_id=row.id, external_ref=check_external_ref(row.kind, requested_ref))
    if row.kind in EXTERNAL_RETRIEVER_CONNECTION_KINDS:
        raise UnprocessableEntityError(
            f"knowledge connection '{row.name}' is a managed search service: it holds its own documents. "
            "Create the knowledge base as managed search (kind 'external') with a partition instead",
            details={"field": "connection_id"},
        )
    if row.kind not in VECTOR_STORE_CONNECTION_KINDS:
        raise UnprocessableEntityError(
            f"knowledge connection '{row.name}' is a re-ranking service, not a place to store knowledge",
            details={"field": "connection_id"},
        )
    parsed = load_settings(row.kind, dict(row.settings or {}))
    return KbBinding(connection_id=row.id, external_ref=external_ref(row.kind, parsed, kb_id))
