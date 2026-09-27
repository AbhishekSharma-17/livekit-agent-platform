"""``/v1/knowledge-connections``: bring-your-own vector stores and hosted re-rankers (V5-20).

Permissions (declared per route with :func:`lkap_api.auth.deps.require`):
reads need ``builder`` + ``providers:read`` (a builder picks a connection when
creating a knowledge base or choosing a re-ranker); creating, changing,
deleting and testing need ``admin`` + ``providers:write``, like the keys they
use. ``Test connection`` is limited to :data:`~lkap_api.knowledge_connections.service.TESTS_PER_MIN`
calls per workspace per minute. Keys are added and rotated through
``/v1/credentials`` (the kind's registry entry); nothing here takes or returns
a secret.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from lkap_contracts.api_models import (
    KnowledgeConnectionCreate,
    KnowledgeConnectionOut,
    KnowledgeConnectionPage,
    KnowledgeConnectionTestOut,
    KnowledgeConnectionUpdate,
)

from lkap_api.auth.deps import WorkspaceContext, require
from lkap_api.auth.ratelimit import RateLimiterDep, enforce
from lkap_api.deps import DbDep, SettingsDep, VaultDep
from lkap_api.kb.embed import resolve_embedder
from lkap_api.knowledge_connections import service

router = APIRouter(prefix="/v1/knowledge-connections", tags=["knowledge connections"])

ReadCtx = Annotated[WorkspaceContext, Depends(require("builder", "providers:read"))]
WriteCtx = Annotated[WorkspaceContext, Depends(require("admin", "providers:write"))]


@router.get(
    "",
    response_model=KnowledgeConnectionPage,
    summary="List knowledge connections",
    description=(
        "The workspace's vector stores (Qdrant, Pinecone, Weaviate) and re-ranking services (Cohere, "
        "Voyage AI), with each one's status, last test result, capabilities, key fingerprint and how "
        "many knowledge bases it stores."
    ),
)
async def list_connections(db: DbDep, ctx: ReadCtx) -> KnowledgeConnectionPage:
    """List the workspace's knowledge connections."""
    return await service.list_connections(db, ctx)


@router.post(
    "",
    response_model=KnowledgeConnectionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a knowledge connection",
    description=(
        "`settings` are the kind's non-secret fields (its registry entry's `fields`: url, collection or "
        "index, cloud and region, model); `credential_id` is a key of the kind's provider stored under "
        "`/v1/credentials`. A url must be https and pass the outbound network guard. The connection "
        "starts `unverified`; run the test to check it."
    ),
)
async def create_connection(
    payload: KnowledgeConnectionCreate, db: DbDep, settings: SettingsDep, ctx: WriteCtx
) -> KnowledgeConnectionOut:
    """Create a knowledge connection."""
    return await service.create_connection(db, settings, ctx, payload)


@router.get(
    "/{connection_id}",
    response_model=KnowledgeConnectionOut,
    summary="Get a knowledge connection",
    description="One connection of the workspace; the key is shown only as its fingerprint.",
)
async def get_connection(connection_id: str, db: DbDep, ctx: ReadCtx) -> KnowledgeConnectionOut:
    """Get one knowledge connection."""
    return await service.get_connection(db, ctx, connection_id)


@router.put(
    "/{connection_id}",
    response_model=KnowledgeConnectionOut,
    summary="Update a knowledge connection",
    description=(
        "Changes the name, the settings or the key (only the fields sent). The url, collection or index "
        "cannot change while knowledge bases are stored through the connection (409). A change to the "
        "settings or the key sets the status back to `unverified`."
    ),
)
async def update_connection(
    connection_id: str, payload: KnowledgeConnectionUpdate, db: DbDep, settings: SettingsDep, ctx: WriteCtx
) -> KnowledgeConnectionOut:
    """Update a knowledge connection."""
    return await service.update_connection(db, settings, ctx, connection_id, payload)


@router.delete(
    "/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a knowledge connection",
    description=(
        "Fails with 409, naming them, while knowledge bases are stored through it. The data in the "
        "vendor's service is left as it is."
    ),
)
async def delete_connection(connection_id: str, db: DbDep, ctx: WriteCtx) -> Response:
    """Delete a knowledge connection nothing is stored through."""
    await service.delete_connection(db, ctx, connection_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{connection_id}/test",
    response_model=KnowledgeConnectionTestOut,
    summary="Test a knowledge connection",
    description=(
        "Calls the service with the stored key. A vector store lists its collections or indexes and "
        "checks the width of the one this connection uses against the embedding width knowledge bases "
        "are built with (a mismatch is `ok=false` with a message naming it). A re-ranking service checks "
        "the key. The outcome is recorded as the connection's status. At most 10 tests per workspace "
        "per minute."
    ),
)
async def test_connection(
    connection_id: str,
    db: DbDep,
    vault: VaultDep,
    settings: SettingsDep,
    ctx: WriteCtx,
    limiter: RateLimiterDep,
) -> KnowledgeConnectionTestOut:
    """Test a knowledge connection and record the outcome."""
    await enforce(
        limiter,
        f"knowledge_connection_test:{ctx.workspace_id}",
        capacity=service.TESTS_PER_MIN,
        per_seconds=60,
        what=f"{service.TESTS_PER_MIN} connection tests per minute",
    )
    try:
        expected = (await resolve_embedder(settings, db, vault)).dimension
    except Exception:  # noqa: BLE001 - the width check is skipped, the test still runs
        expected = None
    return await service.test_connection(db, vault, settings, ctx, connection_id, expected_dimension=expected)
