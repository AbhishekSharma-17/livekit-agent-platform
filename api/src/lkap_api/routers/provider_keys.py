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
from lkap_contracts.providers import (
    MCP_OAUTH_PROVIDER_ID,
    ProviderSpec,
    credential_family,
    credential_home,
    get,
)
from lkap_contracts.tool_providers import TOOL_PROVIDER_ACCOUNT
from lkap_contracts.tools import McpOAuthAuth
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api import credential_tests
from lkap_api.auth.deps import WorkspaceContext
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import Agent, Credential, Tool, utcnow
from lkap_api.deps import AdminCtxDep, DbDep, HttpClientDep, SettingsDep, VaultDep
from lkap_api.errors import ConflictError, NotFoundError, UnprocessableEntityError
from lkap_api.logging import get_logger
from lkap_api.mcp_oauth import revoke as mcp_oauth_revoke
from lkap_api.mcp_oauth.tokens import oauth_definition
from lkap_api.memory.identity import MEMORY_KEY_PROVIDER_ID
from lkap_api.settings import Settings
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
        last_test_at=row.last_test_at,
        last_test_ok=row.last_test_ok,
        last_test_message=row.last_test_message,
        last_used_at=row.last_used_at,
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


#: The message for a hand-made sign-in bag (S5-15): only the OAuth callback writes one.
_SIGN_IN_ONLY = (
    "an MCP sign-in credential is created by signing in to the tool "
    "(POST /v1/tools/{id}/oauth/start), never by hand"
)


def _refuse_hand_made_sign_in(provider_id: str) -> None:
    """S5-15: an ``mcp-oauth`` bag carries token and endpoint urls only the callback may write."""
    if provider_id == MCP_OAUTH_PROVIDER_ID:
        raise UnprocessableEntityError(_SIGN_IN_ONLY)


#: Rows the platform manages itself: never listed, fetched, edited, tested or deleted as keys.
HIDDEN_PROVIDER_IDS: frozenset[str] = frozenset({TOOL_PROVIDER_ACCOUNT, MEMORY_KEY_PROVIDER_ID})


def _refuse_connection_row(row: Credential) -> None:
    """S5-7: a connected app is managed (and disconnected) through the Apps routes only."""
    if row.provider_id == MEMORY_KEY_PROVIDER_ID:
        # V5-40 (ask #257): deleting it would make every caller memory unreachable.
        raise ConflictError(
            "the caller-memory key is managed by the platform; purge memories with POST /v1/memory/purge",
            details={"credential_id": row.id},
        )
    if row.provider_id == TOOL_PROVIDER_ACCOUNT:
        raise ConflictError(
            "a connected app is removed through its connection, not as a key: "
            f"DELETE /v1/tool-providers/composio/connections/{row.id}",
            details={"connection_id": row.id},
        )


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
    _refuse_hand_made_sign_in(spec.id)
    if spec.id in HIDDEN_PROVIDER_IDS:
        raise UnprocessableEntityError(
            f"'{spec.id}' keys are managed by the platform and cannot be added by hand"
        )
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
        description="Filter by registry provider id; a provider that shares its vendor's key (every "
        "OpenRouter entry, Deepgram's transcribers and voice, …) lists every row of that key family",
    ),
) -> CredentialPage:
    """Return the workspace's credentials, newest first.

    Connected apps (``tool-provider-account`` rows) are not keys and are never listed
    here (S5-7); ``GET /v1/tool-providers/composio/connections`` lists them.
    """
    visible = (
        Credential.workspace_id == ctx.workspace_id,
        Credential.provider_id.not_in(HIDDEN_PROVIDER_IDS),
    )
    stmt = select(Credential).where(*visible)
    count_stmt = select(func.count()).select_from(Credential).where(*visible)
    if provider_id:
        # V6-32: the whole family, so a row stored under any member (before the vendor's entries
        # shared one home) is still offered to every member.
        family = credential_family(provider_id)
        stmt = stmt.where(Credential.provider_id.in_(family))
        count_stmt = count_stmt.where(Credential.provider_id.in_(family))
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
    """Return one credential's metadata (a connected app is a 404 here, S5-7)."""
    row = await _load(db, ctx, credential_id)
    if row.provider_id in HIDDEN_PROVIDER_IDS:
        raise NotFoundError(f"unknown credential '{credential_id}'")
    return _to_out(row)


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
    _refuse_connection_row(row)
    if payload.provider_id and credential_home(payload.provider_id) != credential_home(row.provider_id):
        raise UnprocessableEntityError("a credential's provider cannot be changed; create a new one")
    if payload.secrets is not None:
        _refuse_hand_made_sign_in(row.provider_id)
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
        raw_auth = definition.get("auth")
        auth: dict[str, Any] = raw_auth if isinstance(raw_auth, dict) else {}
        # S5-7: a provider tool references its connected app as `connection_id`.
        if credential_id in (
            definition.get("credential_id"),
            definition.get("connection_id"),
            auth.get("credential_id"),
        ):
            found.append(f"tool '{tool.name}'")
    return found


@router.delete(
    "/{credential_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a credential",
    description=(
        "Fails with 409 while an agent pipeline slot or a tool still references it, and for a "
        "connected app (remove that through `DELETE /v1/tool-providers/composio/connections/{id}`). "
        "Deleting an MCP sign-in revokes it at the provider first, like the tool's Disconnect."
    ),
)
async def delete_credential(
    credential_id: str,
    db: DbDep,
    vault: VaultDep,
    client: HttpClientDep,
    settings: SettingsDep,
    ctx: AdminCtxDep,
) -> Response:
    """Delete a credential that nothing references."""
    row = await _load(db, ctx, credential_id)
    _refuse_connection_row(row)
    refs = await _references(db, ctx.workspace_id, credential_id)
    if row.provider_id == MCP_OAUTH_PROVIDER_ID:
        await _delete_sign_in(db, vault, client, settings, ctx, row, refs)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    if refs:
        raise ConflictError("credential is still referenced", details={"references": refs})
    await db.delete(row)
    log.info("credential_deleted", credential_id=credential_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _delete_sign_in(
    db: AsyncSession,
    vault: Vault,
    client: httpx.AsyncClient,
    settings: Settings,
    ctx: WorkspaceContext,
    row: Credential,
    refs: list[str],
) -> None:
    """Delete an ``mcp-oauth`` credential the way the tool's Disconnect does (ask #139).

    The sign-in's own tool (named by the bag's ``tool_id`` and referencing this row) is
    disconnected: revoked at the provider (RFC 7009), its dynamic client deleted when
    unused, the credential deleted, the reference cleared, ``mcp_oauth.revoked`` audited.
    Any other reference still blocks the delete (409). A credential whose tool is gone
    is revoked and deleted on its own.
    """
    bag = vault.decrypt(row.ciphertext)
    tool = await db.scalar(
        select(Tool).where(Tool.id == str(bag.get("tool_id", "")), Tool.workspace_id == ctx.workspace_id)
    )
    definition = oauth_definition(tool) if tool is not None else None
    owned = (
        tool is not None
        and definition is not None
        and isinstance(definition.auth, McpOAuthAuth)
        and definition.auth.credential_id == row.id
    )
    others = [ref for ref in refs if not (owned and tool is not None and ref == f"tool '{tool.name}'")]
    if others:
        raise ConflictError("credential is still referenced", details={"references": others})
    if owned and tool is not None:
        await mcp_oauth_revoke.disconnect_tool(db, vault, client, settings, ctx, tool, trigger="revoke")
    still_there = await db.scalar(
        select(Credential.id).where(Credential.id == row.id, Credential.workspace_id == ctx.workspace_id)
    )
    if still_there is not None:
        outcome = await mcp_oauth_revoke.revoke_credential(
            db, vault, client, settings, workspace_id=ctx.workspace_id, credential=row, bag=bag
        )
        mcp_oauth_revoke.record_revoked(
            db, ctx, str(bag.get("tool_id", "")), credential_id=row.id, bag=bag, outcome=outcome
        )
    log.info("credential_deleted", credential_id=row.id, provider_id=MCP_OAUTH_PROVIDER_ID)


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
    """Verify a stored credential against its vendor and record the outcome on the row.

    V6-32: every outcome is recorded (``last_test_at``/``last_test_ok``/``last_test_message``,
    returned on ``CredentialOut``), not only an adapter's, so the key list shows the test run
    right after adding a key. A provider with no automatic test is recorded with
    ``last_test_ok = None``. The check is the first of the key family (the row's own entry, then
    the home, then the others) that has one: a Deepgram key stored under ``deepgram-tts`` is
    checked the way a ``deepgram-stt`` key is.
    """
    row = await _load(db, ctx, credential_id)
    if row.provider_id in HIDDEN_PROVIDER_IDS:
        raise NotFoundError(f"unknown credential '{credential_id}'")
    spec = _test_spec(_spec_for(row.provider_id))

    if credential_tests.has_adapter(spec) and credential_tests.is_cache_fresh(row.last_test_at):
        return credential_tests.cached_result(
            ok=bool(row.last_test_ok), message=row.last_test_message or "", checked_at=row.last_test_at
        )

    secrets = vault.decrypt(row.ciphertext)
    result = await credential_tests.run(spec, secrets, client)
    if result is None:
        result = await _table_test(spec, secrets, client, credential_id=credential_id)
    row.last_test_at = result.checked_at or utcnow()
    row.last_test_ok = None if _is_untestable(spec) else result.ok
    row.last_test_message = result.message
    await db.flush()
    return result


def _is_untestable(spec: ProviderSpec) -> bool:
    return not credential_tests.has_adapter(spec) and spec.id not in _TEST_CALLS


def _test_spec(spec: ProviderSpec) -> ProviderSpec:
    """The family member whose check tests this key (the entry itself when it has one)."""
    home = credential_home(spec)
    others = sorted(credential_family(spec) - {spec.id, home})
    for provider_id in [spec.id, home, *others]:
        candidate = get(provider_id)
        if not _is_untestable(candidate):
            return candidate
    return spec


async def _table_test(
    spec: ProviderSpec, secrets: dict[str, str], client: httpx.AsyncClient, *, credential_id: str
) -> CredentialTestResult:
    """The ``_TEST_CALLS`` check (one authenticated GET), or the "no automated test" answer."""
    checked_at = utcnow()
    call = _TEST_CALLS.get(spec.id)
    if call is None:
        return CredentialTestResult(
            ok=True, message=f"no automated test implemented for '{spec.id}'", checked_at=checked_at
        )
    url, style = call
    try:
        response = await client.get(url, timeout=10.0, **_auth_request(style, secrets))
    except httpx.HTTPError as exc:
        log.warning("credential_test_failed", credential_id=credential_id, provider_id=spec.id)
        return CredentialTestResult(
            ok=False, message=f"request failed: {type(exc).__name__}", checked_at=checked_at
        )
    ok = response.status_code < 400
    log.info(
        "credential_tested",
        credential_id=credential_id,
        provider_id=spec.id,
        status_code=response.status_code,
    )
    return CredentialTestResult(
        ok=ok, message=f"{spec.vendor} responded with HTTP {response.status_code}", checked_at=checked_at
    )


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
                    Credential.workspace_id == DEFAULT_WORKSPACE_ID,
                    Credential.provider_id.in_(credential_family(spec)),
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
