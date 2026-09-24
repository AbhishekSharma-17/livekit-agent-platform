"""Credential CRUD — the only place secrets enter the platform.

Secrets are write-only: they are encrypted immediately (:mod:`lkap_api.vault`)
and every response carries a :attr:`CredentialOut.fingerprint` instead. No
route in this module can return plaintext; the worker gets decrypted values
only from ``/internal/v1/sessions/{id}/resolved``.

Workspace scoping (V2-02, asks #25): every admin handler takes ``ctx: AdminCtxDep``;
reads filter on ``ctx.workspace_id``, inserts set it, and a row of another
workspace is a 404.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import APIRouter, Query, Response, status
from lkap_contracts.api_models import (
    CredentialCreate,
    CredentialOut,
    CredentialPage,
    CredentialTestResult,
    CredentialUpdate,
)
from lkap_contracts.providers import ProviderSpec, credential_home, get
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api import credential_tests
from lkap_api.auth.deps import WorkspaceContext
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import Agent, Credential, Tool, utcnow
from lkap_api.deps import AdminCtxDep, DbDep, HttpClientDep, VaultDep
from lkap_api.errors import ConflictError, NotFoundError, UnprocessableEntityError
from lkap_api.logging import get_logger
from lkap_api.vault import Vault, fingerprint

log = get_logger(__name__)

router = APIRouter(prefix="/v1/credentials", tags=["credentials"])

#: How to make one cheap authenticated call per vendor, keyed by registry id.
_TEST_CALLS: dict[str, tuple[str, str]] = {
    "openai-realtime": ("https://api.openai.com/v1/models", "bearer"),
    "openai-llm": ("https://api.openai.com/v1/models", "bearer"),
    "openai-tts": ("https://api.openai.com/v1/models", "bearer"),
    "openai-image-gen": ("https://api.openai.com/v1/models", "bearer"),
    "openai-embedding": ("https://api.openai.com/v1/models", "bearer"),
    "google-realtime": ("https://generativelanguage.googleapis.com/v1beta/models", "google"),
    "google-llm": ("https://generativelanguage.googleapis.com/v1beta/models", "google"),
    "google-image-gen": ("https://generativelanguage.googleapis.com/v1beta/models", "google"),
    "deepgram-stt": ("https://api.deepgram.com/v1/projects", "deepgram"),
    "cartesia-tts": ("https://api.cartesia.ai/voices", "cartesia"),
    "elevenlabs-tts": ("https://api.elevenlabs.io/v1/user", "elevenlabs"),
}


def _to_out(row: Credential) -> CredentialOut:
    return CredentialOut(
        id=row.id,
        provider_id=row.provider_id,
        label=row.label,
        fingerprint=row.fingerprint,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _spec_for(provider_id: str) -> ProviderSpec:
    try:
        return get(provider_id)
    except KeyError as exc:
        raise UnprocessableEntityError(f"unknown provider '{provider_id}'") from exc


def _check_secrets(spec: ProviderSpec, secrets: dict[str, str]) -> None:
    """Reject empty bags and missing required secret fields (never echoes values)."""
    if not secrets:
        raise UnprocessableEntityError(f"provider '{spec.id}' requires at least one secret value")
    missing = [f.name for f in spec.secret_fields if f.required and not secrets.get(f.name)]
    if missing:
        raise UnprocessableEntityError(f"provider '{spec.id}' requires secret field(s): {', '.join(missing)}")


def _primary_field(spec: ProviderSpec) -> str | None:
    return spec.secret_fields[0].name if spec.secret_fields else None


async def _load(db: AsyncSession, ctx: WorkspaceContext, credential_id: str) -> Credential:
    """Load a credential of the caller's workspace (404 for any other workspace)."""
    row = await db.scalar(
        select(Credential).where(Credential.id == credential_id, Credential.workspace_id == ctx.workspace_id)
    )
    if row is None:
        raise NotFoundError(f"unknown credential '{credential_id}'")
    return row


@router.post(
    "",
    response_model=CredentialOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a credential",
    description="Encrypts the secret bag with `LKAP_MASTER_KEY`; the values are never returned.",
)
async def create_credential(
    payload: CredentialCreate, db: DbDep, vault: VaultDep, ctx: AdminCtxDep
) -> CredentialOut:
    """Store a new encrypted credential in the caller's workspace.

    A provider with a credential home (R-V4-7) has its row stored under the
    home, after the secrets are checked against the requested provider's own
    (identical) `secret_fields`: a key added from `openrouter-stt` is the
    `openrouter-llm` key every OpenRouter provider shares.
    """
    spec = _spec_for(payload.provider_id)
    _check_secrets(spec, payload.secrets)
    row = Credential(
        workspace_id=ctx.workspace_id,
        provider_id=credential_home(spec),
        label=payload.label,
        ciphertext=vault.encrypt(payload.secrets),
        fingerprint=fingerprint(payload.secrets, primary_field=_primary_field(spec)),
    )
    db.add(row)
    await db.flush()
    log.info("credential_created", credential_id=row.id, provider_id=row.provider_id)
    return _to_out(row)


@router.get(
    "",
    response_model=CredentialPage,
    summary="List credentials",
    description="All stored credentials (fingerprints only), optionally filtered by provider.",
)
async def list_credentials(
    db: DbDep,
    ctx: AdminCtxDep,
    provider_id: str | None = Query(
        default=None,
        description="Filter by registry provider id; a provider that shares another's key (every "
        "OpenRouter entry) lists the rows stored under that credential home",
    ),
) -> CredentialPage:
    """Return the workspace's credentials, newest first."""
    stmt = select(Credential).where(Credential.workspace_id == ctx.workspace_id)
    count_stmt = (
        select(func.count()).select_from(Credential).where(Credential.workspace_id == ctx.workspace_id)
    )
    if provider_id:
        home = credential_home(provider_id)
        stmt = stmt.where(Credential.provider_id == home)
        count_stmt = count_stmt.where(Credential.provider_id == home)
    rows = (await db.execute(stmt.order_by(Credential.created_at.desc()))).scalars().all()
    total = (await db.execute(count_stmt)).scalar_one()
    return CredentialPage(items=[_to_out(r) for r in rows], total=total)


@router.get(
    "/{credential_id}",
    response_model=CredentialOut,
    summary="Get a credential",
    description="Metadata and fingerprint for one credential; never the secret values.",
)
async def get_credential(credential_id: str, db: DbDep, ctx: AdminCtxDep) -> CredentialOut:
    """Return one credential's metadata."""
    return _to_out(await _load(db, ctx, credential_id))


@router.put(
    "/{credential_id}",
    response_model=CredentialOut,
    summary="Update a credential",
    description="Renames the credential and, when `secrets` is present, re-encrypts it.",
)
async def update_credential(
    credential_id: str,
    payload: CredentialUpdate,
    db: DbDep,
    vault: VaultDep,
    ctx: AdminCtxDep,
) -> CredentialOut:
    """Update label and/or secrets; omitting `secrets` keeps the stored values."""
    row = await _load(db, ctx, credential_id)
    if payload.provider_id and credential_home(payload.provider_id) != row.provider_id:
        raise UnprocessableEntityError("a credential's provider cannot be changed; create a new one")
    spec = _spec_for(row.provider_id)
    if payload.label is not None:
        row.label = payload.label
    if payload.secrets is not None:
        _check_secrets(spec, payload.secrets)
        row.ciphertext = vault.encrypt(payload.secrets)
        row.fingerprint = fingerprint(payload.secrets, primary_field=_primary_field(spec))
    row.updated_at = utcnow()
    await db.flush()
    log.info("credential_updated", credential_id=row.id, secrets_rotated=payload.secrets is not None)
    return _to_out(row)


async def _references(db: AsyncSession, workspace_id: str, credential_id: str) -> list[str]:
    """Return human-readable references to a credential (agents and tools of its workspace)."""
    found: list[str] = []
    agents = (await db.execute(select(Agent).where(Agent.workspace_id == workspace_id))).scalars().all()
    for agent in agents:
        pipeline = agent.config.get("pipeline", {}) if isinstance(agent.config, dict) else {}
        for slot, ref in pipeline.items():
            if isinstance(ref, dict) and ref.get("credential_id") == credential_id:
                found.append(f"agent '{agent.slug}' ({slot})")
    tools = (await db.execute(select(Tool).where(Tool.workspace_id == workspace_id))).scalars().all()
    for tool in tools:
        definition = tool.definition if isinstance(tool.definition, dict) else {}
        if definition.get("credential_id") == credential_id:
            found.append(f"tool '{tool.name}'")
    return found


@router.delete(
    "/{credential_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a credential",
    description="Fails with 409 while an agent pipeline slot or a tool still references it.",
)
async def delete_credential(credential_id: str, db: DbDep, ctx: AdminCtxDep) -> Response:
    """Delete a credential that nothing references."""
    row = await _load(db, ctx, credential_id)
    refs = await _references(db, ctx.workspace_id, credential_id)
    if refs:
        raise ConflictError("credential is still referenced", details={"references": refs})
    await db.delete(row)
    log.info("credential_deleted", credential_id=credential_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _auth_request(style: str, secrets: dict[str, str]) -> dict[str, Any]:
    """Build the per-vendor auth headers for a credential test call."""
    key = secrets.get("api_key", "")
    match style:
        case "bearer":
            return {"headers": {"Authorization": f"Bearer {key}"}}
        case "google":
            return {"headers": {"x-goog-api-key": key}}
        case "deepgram":
            return {"headers": {"Authorization": f"Token {key}"}}
        case "cartesia":
            return {"headers": {"X-API-Key": key, "Cartesia-Version": "2024-06-10"}}
        case "elevenlabs":
            return {"headers": {"xi-api-key": key}}
        case _:  # pragma: no cover - table is closed
            return {"headers": {}}


@router.post(
    "/{credential_id}/test",
    response_model=CredentialTestResult,
    summary="Test a credential",
    description=(
        "Makes one cheap authenticated call to the vendor. Providers without a known "
        "check (avatars, tool secret bags) report `ok=true` with a note. For a provider "
        "with a `lkap_api.credential_tests` adapter (V2-06), the result is cached for "
        "10 minutes on the credential row and includes a `catalog_preview`."
    ),
)
async def test_credential(
    credential_id: str,
    db: DbDep,
    vault: VaultDep,
    client: HttpClientDep,
    ctx: AdminCtxDep,
) -> CredentialTestResult:
    """Verify a stored credential against its vendor."""
    row = await _load(db, ctx, credential_id)
    spec = _spec_for(row.provider_id)

    if credential_tests.has_adapter(spec) and credential_tests.is_cache_fresh(row.last_test_at):
        return credential_tests.cached_result(
            ok=bool(row.last_test_ok), message=row.last_test_message or "", checked_at=row.last_test_at
        )

    secrets = vault.decrypt(row.ciphertext)
    result = await credential_tests.run(spec, secrets, client)
    if result is not None:
        row.last_test_at = result.checked_at
        row.last_test_ok = result.ok
        row.last_test_message = result.message
        await db.flush()
        return result

    call = _TEST_CALLS.get(spec.id)
    if call is None:
        return CredentialTestResult(ok=True, message=f"no automated test implemented for '{spec.id}'")
    url, style = call
    try:
        response = await client.get(url, timeout=10.0, **_auth_request(style, secrets))
    except httpx.HTTPError as exc:
        log.warning("credential_test_failed", credential_id=credential_id, provider_id=spec.id)
        return CredentialTestResult(ok=False, message=f"request failed: {type(exc).__name__}")
    ok = response.status_code < 400
    log.info(
        "credential_tested",
        credential_id=credential_id,
        provider_id=spec.id,
        status_code=response.status_code,
    )
    return CredentialTestResult(ok=ok, message=f"{spec.vendor} responded with HTTP {response.status_code}")


def _bootstrap_label(provider_id: str) -> str:
    return f"{provider_id} (bootstrap)"


async def seed_bootstrap_credentials(db: Any, vault: Vault, raw_json: str | None) -> int:
    """Seed credentials from ``LKAP_BOOTSTRAP_CREDENTIALS_JSON`` (dev convenience).

    A provider that already has at least one credential is skipped, so restarts
    are idempotent and never overwrite an admin's key. Seeded credentials belong
    to the ``default`` workspace (CONTRACTS-V2 §6).

    Args:
        db: An open :class:`~sqlalchemy.ext.asyncio.AsyncSession`.
        vault: The configured vault.
        raw_json: The env var value, ``{provider_id: {field: value}}``.

    Returns:
        How many credentials were created.
    """
    if not raw_json:
        return 0
    parsed: dict[str, dict[str, str]] = json.loads(raw_json)
    created = 0
    for provider_id, secrets in parsed.items():
        try:
            spec = get(provider_id)
        except KeyError:
            log.warning("bootstrap_unknown_provider", provider_id=provider_id)
            continue
        home = credential_home(spec)
        existing = (
            await db.execute(
                select(Credential.id).where(
                    Credential.workspace_id == DEFAULT_WORKSPACE_ID, Credential.provider_id == home
                )
            )
        ).first()
        if existing is not None:
            continue
        db.add(
            Credential(
                workspace_id=DEFAULT_WORKSPACE_ID,
                provider_id=home,
                label=_bootstrap_label(home),
                ciphertext=vault.encrypt(secrets),
                fingerprint=fingerprint(secrets, primary_field=_primary_field(spec)),
            )
        )
        created += 1
        log.info("bootstrap_credential_created", provider_id=spec.id)
    await db.flush()
    return created
