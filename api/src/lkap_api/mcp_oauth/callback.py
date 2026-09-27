"""The sign-in callback: ``GET /v1/oauth/mcp/callback`` (V5-14, research-v4 tools §4.3.3, §4.3.9).

Unauthenticated (it is the provider's browser redirect), so nothing in the query is
trusted alone. In order:

1. ``state`` is hashed and its flow row loaded; the stored hash is compared with
   :func:`hmac.compare_digest`.
2. The row is **claimed** (``consumed_at`` set where it was ``NULL``) and that is
   committed in its own transaction before anything else, so a ``state`` works once
   even when a later step fails; an already-claimed row is refused.
3. Expiry (ten minutes from ``oauth/start``).
3a. The browser binding (R-V5-14): a flow a person started (``actor_type`` other than
   ``api_key``) needs the ``lkap_mcp_oauth`` cookie ``oauth/start`` set, compared in
   constant time with the hash kept in the flow's encrypted bag; otherwise
   ``browser_mismatch`` (consumed, audited). ``LKAP_MCP_OAUTH_ALLOW_UNBOUND=true`` turns
   the check off for split-origin deployments.
4. RFC 9207, as the MCP spec tabulates it: when ``iss`` is present it must equal the
   issuer recorded at start, byte for byte (no case folding, no trailing-slash or
   percent-encoding normalisation); when it is absent and the provider advertised
   ``authorization_response_iss_parameter_supported``, the response is refused. On a
   mismatch the ``error`` parameters are not acted on.
5. Only then ``error`` (the admin declined, or the provider refused) and ``code``.
6. The tool must still exist in the flow's workspace, still use OAuth, and its url
   must still be covered by the flow's ``resource``.
7. The code is exchanged at the recorded token endpoint (``authorization_code`` + the
   PKCE verifier + ``redirect_uri`` + ``resource``, client authentication when the
   client has a secret), through the guard, no redirects, bounded.
8. The ``mcp-oauth`` credential is written through the vault (``expires_at`` absolute)
   and the tool's ``auth.credential_id`` points at it; audit ``mcp_oauth.callback_ok``.

Every refusal of an identified sign-in is audited as ``mcp_oauth.callback_rejected``
with a reason and no value from the query; a ``state`` that names no sign-in
(``malformed_state``, ``unknown_state``) writes only a log line, so an unauthenticated
client cannot grow the audit table (S5-21, with the route's per-client rate limit). An
unexpected failure after the claim is ``internal`` (audited), never a bare 500 (S5-18).
A ``state`` that names no live sign-in (unknown, already used, expired, malformed) is
answered ``400`` without a token exchange; every other outcome
lands the browser on the console's tools page with ``oauth=ok`` or ``oauth=error`` and
nothing else.
"""

from __future__ import annotations

import datetime as dt
import hmac
import re
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import quote, urlencode

import httpx
from lkap_contracts.providers import MCP_OAUTH_PROVIDER_ID
from lkap_contracts.tools import McpOAuthAuth, McpServerDefinition
from mcp.shared.auth import OAuthToken
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.auth import audit
from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import Credential, McpOauthClient, McpOauthFlow, Tool, utcnow
from lkap_api.db.session import Database
from lkap_api.logging import get_logger
from lkap_api.mcp_oauth.credential import STATUS_ACTIVE, binds_tool, fingerprint_of
from lkap_api.mcp_oauth.discovery import resource_covers
from lkap_api.mcp_oauth.flows import MAX_BINDER_CHARS, MAX_STATE_CHARS, hash_binder, hash_state
from lkap_api.mcp_oauth.http import McpOauthError, UrlPolicy, fetch, host_of, require_url, token_lifetime
from lkap_api.mcp_oauth.registration import client_secrets
from lkap_api.settings import Settings
from lkap_api.vault import Vault

log = get_logger(__name__)

#: Where the console lists tools (the callback's redirect target).
CONSOLE_TOOLS_PATH: Final[str] = "/console/tools"
#: The longest authorization code accepted.
MAX_CODE_CHARS: Final[int] = 4096
#: The longest access or refresh token stored.
MAX_TOKEN_CHARS: Final[int] = 16_000
#: A provider's ``error`` code is recorded in the audit row only when it looks like one.
_ERROR_CODE = re.compile(r"^[a-z_]{1,64}$")

#: Refusals where the ``state`` itself is not a live sign-in (unknown, reused, expired, malformed).
BAD_STATE_REASONS: Final[frozenset[str]] = frozenset(
    {"malformed_state", "unknown_state", "already_used", "expired"}
)

_TOOL_DEFINITION: TypeAdapter[McpServerDefinition] = TypeAdapter(McpServerDefinition)


@dataclass(frozen=True)
class CallbackOutcome:
    """What the callback decided (the route turns it into the redirect)."""

    ok: bool
    reason: str

    @property
    def bad_state(self) -> bool:
        """The ``state`` named no live sign-in: answered with a 400, not a redirect."""
        return self.reason in BAD_STATE_REASONS


@dataclass(frozen=True)
class _Flow:
    """A claimed flow row, detached from its session."""

    workspace_id: str
    tool_id: str
    actor_type: str
    actor_id: str | None
    verifier_ciphertext: bytes
    issuer: str
    iss_parameter_supported: bool
    resource: str
    token_endpoint: str
    revocation_endpoint: str | None
    registration: str
    client_id: str
    client_row_id: str | None
    token_endpoint_auth_method: str
    scopes: str | None
    redirect_uri: str
    expires_at: dt.datetime

    @classmethod
    def of(cls, row: McpOauthFlow) -> _Flow:
        return cls(
            workspace_id=row.workspace_id,
            tool_id=row.tool_id,
            actor_type=row.actor_type,
            actor_id=row.actor_id,
            verifier_ciphertext=row.verifier_ciphertext,
            issuer=row.issuer,
            iss_parameter_supported=row.iss_parameter_supported,
            resource=row.resource,
            token_endpoint=row.token_endpoint,
            revocation_endpoint=row.revocation_endpoint,
            registration=row.registration,
            client_id=row.client_id,
            client_row_id=row.client_row_id,
            token_endpoint_auth_method=row.token_endpoint_auth_method,
            scopes=row.scopes,
            redirect_uri=row.redirect_uri,
            expires_at=row.expires_at,
        )


def console_redirect(settings: Settings, outcome: CallbackOutcome) -> str:
    """The console's tools page with ``oauth=ok|error`` only (no id, no token material)."""
    base = (settings.web_base_url or (settings.web_origins[0] if settings.web_origins else "")).rstrip("/")
    return f"{base}{CONSOLE_TOOLS_PATH}?{urlencode({'oauth': 'ok' if outcome.ok else 'error'})}"


def _actor_type(flow: _Flow | None) -> audit.ActorType:
    match flow.actor_type if flow is not None else None:
        case "user":
            return "user"
        case "api_key":
            return "api_key"
        case _:
            return "system"


def _audit(
    db: AsyncSession, flow: _Flow | None, action: str, *, reason: str | None = None, **payload: Any
) -> None:
    if reason is not None:
        payload["reason"] = reason
    audit.record(
        db,
        workspace_id=flow.workspace_id if flow is not None else None,
        actor_type=_actor_type(flow),
        actor_id=flow.actor_id if flow is not None else None,
        action=action,
        target_type="tool",
        target_id=flow.tool_id if flow is not None else None,
        payload=payload,
    )


#: Refusals that name no sign-in: logged, never audited (S5-21).
_UNAUDITED_REASONS: Final[frozenset[str]] = frozenset({"malformed_state", "unknown_state"})


async def _reject(database: Database, flow: _Flow | None, reason: str, **payload: Any) -> CallbackOutcome:
    if reason not in _UNAUDITED_REASONS:
        async with database.session() as db:
            _audit(db, flow, "mcp_oauth.callback_rejected", reason=reason, **payload)
    log.info("mcp_oauth_callback_rejected", reason=reason, tool_id=flow.tool_id if flow else None)
    return CallbackOutcome(False, reason)


def _browser_bound(flow: _Flow, settings: Settings) -> bool:
    """Whether this flow must be finished by the browser that started it (R-V5-14)."""
    return flow.actor_type != "api_key" and not settings.mcp_oauth_allow_unbound


def _binder_matches(flow: _Flow, vault: Vault, binder: str | None) -> bool:
    expected = vault.decrypt(flow.verifier_ciphertext).get("binder_sha256")
    if not isinstance(expected, str) or not expected or not binder or len(binder) > MAX_BINDER_CHARS:
        return False
    return hmac.compare_digest(expected.encode("ascii"), hash_binder(binder).encode("ascii"))


async def _claim(database: Database, state: str, now: dt.datetime) -> _Flow | str:
    """Load and claim the flow in one committed transaction; a reason string on refusal."""
    digest = hash_state(state)
    async with database.session() as db:
        row = await db.scalar(
            select(McpOauthFlow)
            .where(McpOauthFlow.state_hash == digest)
            # The callback is unauthenticated: the flow row itself names the workspace.
            .execution_options(**{CROSS_WORKSPACE_OPTION: True})
        )
        if row is None or not hmac.compare_digest(row.state_hash.encode("ascii"), digest.encode("ascii")):
            return "unknown_state"
        claimed = await db.execute(
            update(McpOauthFlow)
            .where(McpOauthFlow.state_hash == digest, McpOauthFlow.consumed_at.is_(None))
            .values(consumed_at=now)
            .execution_options(synchronize_session=False)
        )
        if int(getattr(claimed, "rowcount", 0) or 0) != 1:
            return "already_used"
        return _Flow.of(row)


def _issuer_problem(flow: _Flow, iss: str | None) -> str | None:
    """RFC 9207 §2.4 as the MCP spec tabulates it (simple string comparison)."""
    if iss is not None:
        return None if iss == flow.issuer else "issuer_mismatch"
    return "issuer_missing" if flow.iss_parameter_supported else None


def _oauth_definition(row: Tool | None, flow: _Flow) -> McpServerDefinition | None:
    if row is None or row.kind != "mcp":
        return None
    try:
        definition = _TOOL_DEFINITION.validate_python(row.definition)
    except ValidationError:
        return None
    if not isinstance(definition.auth, McpOAuthAuth) or not resource_covers(flow.resource, definition.url):
        return None
    return definition


async def _load_tool(db: AsyncSession, flow: _Flow) -> Tool | None:
    row: Tool | None = await db.scalar(
        select(Tool).where(Tool.id == flow.tool_id, Tool.workspace_id == flow.workspace_id)
    )
    return row


async def _client_secret_bag(db: AsyncSession, vault: Vault, flow: _Flow) -> dict[str, str]:
    if flow.client_row_id is None:
        return {}
    row = await db.scalar(
        select(McpOauthClient).where(
            McpOauthClient.id == flow.client_row_id, McpOauthClient.workspace_id == flow.workspace_id
        )
    )
    return client_secrets(row, vault) if row is not None else {}


async def _exchange(
    client: httpx.AsyncClient,
    policy: UrlPolicy,
    flow: _Flow,
    *,
    code: str,
    verifier: str,
    client_secret: str | None,
) -> OAuthToken:
    endpoint = require_url(flow.token_endpoint, policy, field="token_endpoint")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": flow.redirect_uri,
        "code_verifier": verifier,
        "client_id": flow.client_id,
        "resource": flow.resource,
    }
    basic: tuple[str, str] | None = None
    if client_secret and flow.token_endpoint_auth_method == "client_secret_basic":
        # RFC 6749 §2.3.1: both parts form-url-encoded before the Basic encoding.
        basic = (quote(flow.client_id, safe=""), quote(client_secret, safe=""))
        del data["client_id"]
    elif client_secret and flow.token_endpoint_auth_method == "client_secret_post":
        data["client_secret"] = client_secret
    fetched = await fetch(
        client,
        "POST",
        endpoint,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=data,
        auth=basic,
    )
    if fetched.status != 200:
        refusal = fetched.json_object() or {}
        error = refusal.get("error")
        code_hint = error if isinstance(error, str) and _ERROR_CODE.match(error) else None
        raise McpOauthError(
            "token_exchange_failed",
            f"the sign-in provider refused the code ({fetched.status}"
            + (f", {code_hint})" if code_hint else ")"),
        )
    body = fetched.json_object()
    try:
        token = OAuthToken.model_validate(body)
    except ValidationError as exc:
        raise McpOauthError(
            "token_exchange_failed", "the sign-in provider's token answer is not valid"
        ) from exc
    if not token.access_token or len(token.access_token) > MAX_TOKEN_CHARS:
        raise McpOauthError("token_exchange_failed", "the sign-in provider sent no usable access token")
    if token.refresh_token is not None and len(token.refresh_token) > MAX_TOKEN_CHARS:
        raise McpOauthError("token_exchange_failed", "the sign-in provider's refresh token is too long")
    token_lifetime(token.expires_in)  # refuses zero or a negative lifetime (S5-18)
    return token


def _bag(
    flow: _Flow,
    token: OAuthToken,
    secrets_bag: dict[str, str],
    now: dt.datetime,
    client_metadata_url: str | None,
) -> dict[str, str]:
    scope = token.scope if token.scope is not None else (flow.scopes or "")
    lifetime = token_lifetime(token.expires_in)
    values: dict[str, str | None] = {
        "access_token": token.access_token,
        "refresh_token": token.refresh_token,
        "expires_at": (now + dt.timedelta(seconds=lifetime)).isoformat() if lifetime is not None else None,
        "scope": scope or None,
        "issuer": flow.issuer,
        "token_endpoint": flow.token_endpoint,
        "revocation_endpoint": flow.revocation_endpoint,
        "resource": flow.resource,
        "client_id": flow.client_id,
        "client_secret": secrets_bag.get("client_secret"),
        "token_endpoint_auth_method": flow.token_endpoint_auth_method,
        "registration": flow.registration,
        "registration_access_token": secrets_bag.get("registration_access_token"),
        "registration_client_uri": secrets_bag.get("registration_client_uri"),
        "client_metadata_url": client_metadata_url,
        "status": STATUS_ACTIVE,
        "tool_id": flow.tool_id,
        "connected_at": now.isoformat(),
        "last_refresh_at": now.isoformat(),
    }
    return {key: value for key, value in values.items() if value}


async def _store(
    database: Database,
    vault: Vault,
    flow: _Flow,
    token: OAuthToken,
    secrets_bag: dict[str, str],
    now: dt.datetime,
    *,
    browser_bound: bool,
) -> CallbackOutcome:
    async with database.session() as db:
        row = await _load_tool(db, flow)
        definition = _oauth_definition(row, flow)
        if row is None or definition is None:
            _audit(db, flow, "mcp_oauth.callback_rejected", reason="tool_changed")
            return CallbackOutcome(False, "tool_changed")
        client_metadata_url = flow.client_id if flow.registration == "cimd" else None
        if flow.client_row_id is not None:
            client_row = await db.scalar(
                select(McpOauthClient).where(
                    McpOauthClient.id == flow.client_row_id, McpOauthClient.workspace_id == flow.workspace_id
                )
            )
            if client_row is not None and client_row.registration_client_uri:
                secrets_bag = {**secrets_bag, "registration_client_uri": client_row.registration_client_uri}
        bag = _bag(flow, token, secrets_bag, now, client_metadata_url)
        credential: Credential | None = None
        auth = definition.auth
        if isinstance(auth, McpOAuthAuth) and auth.credential_id:
            credential = await db.scalar(
                select(Credential).where(
                    Credential.id == auth.credential_id,
                    Credential.workspace_id == flow.workspace_id,
                    Credential.provider_id == MCP_OAUTH_PROVIDER_ID,
                )
            )
            if credential is not None and not binds_tool(
                vault.decrypt(credential.ciphertext), tool_id=row.id, url=definition.url
            ):
                credential = None
        fingerprint = fingerprint_of(flow.issuer, bag.get("scope"))
        if credential is None:
            credential = Credential(
                workspace_id=flow.workspace_id,
                provider_id=MCP_OAUTH_PROVIDER_ID,
                label=f"{row.name} sign-in"[:200],
                ciphertext=vault.encrypt(bag),
                fingerprint=fingerprint,
            )
            db.add(credential)
        else:
            credential.ciphertext = vault.encrypt(bag)
            credential.fingerprint = fingerprint
            credential.updated_at = now
        credential.last_test_at, credential.last_test_ok, credential.last_test_message = (
            now,
            True,
            "connected",
        )
        await db.flush()
        stored = definition.model_dump(mode="json")
        stored["auth"]["credential_id"] = credential.id
        stored["credential_id"] = credential.id
        row.definition = _TOOL_DEFINITION.validate_python(stored).model_dump(mode="json")
        row.updated_at = now
        _audit(
            db,
            flow,
            "mcp_oauth.callback_ok",
            credential_id=credential.id,
            issuer_host=host_of(flow.issuer),
            registration=flow.registration,
            scope_count=len(bag.get("scope", "").split()),
            refresh_token=bool(token.refresh_token),
            browser_bound=browser_bound,
        )
    log.info("mcp_oauth_connected", tool_id=flow.tool_id, registration=flow.registration)
    return CallbackOutcome(True, "ok")


async def handle_callback(
    database: Database,
    vault: Vault,
    client: httpx.AsyncClient,
    settings: Settings,
    *,
    state: str | None,
    code: str | None,
    iss: str | None,
    error: str | None,
    binder: str | None = None,
    now: dt.datetime | None = None,
) -> CallbackOutcome:
    """Finish a sign-in (see the module docstring for the order of checks)."""
    ts = now or utcnow()
    if not state or len(state) > MAX_STATE_CHARS:
        return await _reject(database, None, "malformed_state")
    claimed = await _claim(database, state, ts)
    if isinstance(claimed, str):
        return await _reject(database, None, claimed)
    flow = claimed
    try:
        return await _finish(
            database, vault, client, settings, flow, code=code, iss=iss, error=error, binder=binder, now=ts
        )
    except Exception as exc:  # noqa: BLE001 - a consumed flow always ends in an audited refusal
        log.warning("mcp_oauth_callback_failed", tool_id=flow.tool_id, error_type=type(exc).__name__)
        return await _reject(database, flow, "internal")


async def _finish(
    database: Database,
    vault: Vault,
    client: httpx.AsyncClient,
    settings: Settings,
    flow: _Flow,
    *,
    code: str | None,
    iss: str | None,
    error: str | None,
    binder: str | None,
    now: dt.datetime,
) -> CallbackOutcome:
    """Every check after the claim, the exchange and the store."""
    ts = now
    if ts >= flow.expires_at:
        return await _reject(database, flow, "expired")
    browser_bound = _browser_bound(flow, settings)
    if browser_bound and not _binder_matches(flow, vault, binder):
        return await _reject(database, flow, "browser_mismatch")
    issuer_problem = _issuer_problem(flow, iss)
    if issuer_problem is not None:
        return await _reject(database, flow, issuer_problem)  # the error parameters are ignored
    if error is not None:
        hint = error if _ERROR_CODE.match(error) else "other"
        return await _reject(database, flow, "provider_error", provider_error=hint)
    if not code or len(code) > MAX_CODE_CHARS:
        return await _reject(database, flow, "missing_code")

    async with database.session() as db:
        if _oauth_definition(await _load_tool(db, flow), flow) is None:
            _audit(db, flow, "mcp_oauth.callback_rejected", reason="tool_changed")
            return CallbackOutcome(False, "tool_changed")
        secrets_bag = await _client_secret_bag(db, vault, flow)
    verifier = vault.decrypt(flow.verifier_ciphertext).get("code_verifier", "")
    try:
        token = await _exchange(
            client,
            UrlPolicy.from_settings(settings),
            flow,
            code=code,
            verifier=verifier,
            client_secret=secrets_bag.get("client_secret"),
        )
    except McpOauthError as exc:
        return await _reject(database, flow, exc.reason)
    return await _store(database, vault, flow, token, secrets_bag, ts, browser_bound=browser_bound)


__all__ = [
    "BAD_STATE_REASONS",
    "CONSOLE_TOOLS_PATH",
    "CallbackOutcome",
    "console_redirect",
    "handle_callback",
]
