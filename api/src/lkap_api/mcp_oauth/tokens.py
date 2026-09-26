"""Access tokens of signed-in MCP servers: refresh, the worker's token route (V5-16, T §4.3.6).

The api is the OAuth client; the worker only ever holds an access token that expires
minutes from now. :func:`get_access_token` hands out the stored one while it has more
than :data:`REFRESH_SKEW` left and otherwise refreshes it, **single-flight per
credential**:

1. a process lock (one :class:`asyncio.Lock` per credential id) — the SQLite case;
2. a Redis ``SET NX PX`` lock when ``LKAP_REDIS_URL`` is set (several api replicas);
3. ``SELECT … FOR UPDATE`` on the credential row inside the refresh transaction (Postgres;
   SQLite ignores it and relies on 1).

Under the lock the bag is **re-read**: when another request already refreshed (or a
provider rotated the refresh token) the new values are used as they are, so a rotated
refresh token is never replayed. The refreshed bag is committed before the lock is
released.

``invalid_grant`` from the token endpoint (or an expired token with no refresh token)
flips the bag's ``status`` to ``needs_reauth``, writes a ``mcp_oauth.needs_reauth``
audit row and emits the ``tool.needs_reauth`` webhook; the admin signs in again from
the console. A transient failure (network, 5xx) keeps the current token while it still
works.

``POST /internal/v1/tools/{tool_id}/oauth/token`` (:func:`issue_session_token`) is the
worker's way to a fresh token: service token, and bound to a live session whose agent
uses the tool. It returns ``{access_token, expires_at}`` — never the refresh token.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
import importlib
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import quote

import httpx
from lkap_contracts.agent_config import AgentConfig, McpOAuthTokenIn, McpOAuthTokenOut
from lkap_contracts.providers import MCP_OAUTH_PROVIDER_ID
from lkap_contracts.tools import McpOAuthAuth, McpServerDefinition, ToolDefinition
from mcp.shared.auth import OAuthToken
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api import webhooks
from lkap_api.auth import audit
from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import Agent, Credential, Tool, utcnow
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.errors import ApiError, ConflictError, NotFoundError
from lkap_api.jobs.service import JobsService
from lkap_api.logging import get_logger
from lkap_api.mcp_oauth.credential import STATUS_ACTIVE, binds_tool, parse_time
from lkap_api.mcp_oauth.http import McpOauthError, UrlPolicy, fetch, host_of, require_url
from lkap_api.settings import Settings
from lkap_api.vault import Vault
from lkap_api.webhooks import events as webhook_events

log = get_logger(__name__)

#: A token with less than this left is refreshed before it is handed out (the card's 60 s).
REFRESH_SKEW: Final[dt.timedelta] = dt.timedelta(seconds=60)
#: Bag ``status`` after ``invalid_grant``: the admin must sign in again.
STATUS_NEEDS_REAUTH: Final[str] = "needs_reauth"
#: How long a Redis refresh lock lives if its holder dies (milliseconds).
LOCK_TTL_MS: Final[int] = 30_000
#: How long a request waits for another replica's refresh before giving up (seconds).
LOCK_WAIT_S: Final[float] = 15.0
#: The longest access or refresh token accepted from a refresh answer.
MAX_TOKEN_CHARS: Final[int] = 16_000
#: Session statuses whose worker may still ask for a token.
_LIVE_SESSION_STATUSES: Final[frozenset[str]] = frozenset({"created", "active"})

_TOOL: TypeAdapter[ToolDefinition] = TypeAdapter(ToolDefinition)


class NeedsReauth(Exception):  # noqa: N818 - a state, not a failure of the call
    """The sign-in cannot produce a token any more; an admin must sign in again."""

    def __init__(self, reason: str) -> None:
        """Keep the machine-readable reason (never a value)."""
        super().__init__(f"the MCP server's sign-in needs to be renewed ({reason})")
        self.reason = reason


class TokenUnavailable(Exception):  # noqa: N818 - mirrors NeedsReauth
    """A refresh failed for a transient reason and the current token no longer works."""

    def __init__(self, reason: str) -> None:
        """Keep the machine-readable reason."""
        super().__init__(f"could not refresh the MCP server's sign-in ({reason})")
        self.reason = reason


class TokenServiceUnavailableError(ApiError):
    """503: the sign-in provider could not refresh the token just now (retry later)."""

    status_code = 503
    code = "token_unavailable"


@dataclass(frozen=True)
class AccessToken:
    """An access token and when it expires (``None``: the provider did not say)."""

    access_token: str
    expires_at: dt.datetime | None


def token_sha256(token: str) -> str:
    """The hex SHA-256 the worker sends to name a token the server refused."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _current(bag: dict[str, str]) -> AccessToken | None:
    token = bag.get("access_token")
    return AccessToken(token, parse_time(bag.get("expires_at"))) if token else None


def _require(token: AccessToken | None) -> AccessToken:
    """``token`` after :func:`_fresh` or :func:`_valid` accepted it (both imply one)."""
    assert token is not None  # noqa: S101 - narrowed by the caller's check
    return token


def _check_status(bag: dict[str, str]) -> None:
    status = bag.get("status", STATUS_ACTIVE)
    if status != STATUS_ACTIVE:
        raise NeedsReauth(status)


def _left(token: AccessToken, now: dt.datetime) -> dt.timedelta | None:
    return None if token.expires_at is None else token.expires_at - now


def _fresh(token: AccessToken | None, now: dt.datetime) -> bool:
    """More than :data:`REFRESH_SKEW` left (or no expiry at all)."""
    if token is None:
        return False
    left = _left(token, now)
    return left is None or left > REFRESH_SKEW


def _valid(token: AccessToken | None, now: dt.datetime) -> bool:
    """Not expired yet (possibly inside the skew)."""
    if token is None:
        return False
    left = _left(token, now)
    return left is None or left > dt.timedelta(0)


def _rejected(token: AccessToken | None, rejected_sha256: str | None) -> bool:
    """Whether the worker's refused token is the one currently stored."""
    if token is None or rejected_sha256 is None:
        return False
    return hmac.compare_digest(token_sha256(token.access_token), rejected_sha256)


# ------------------------------------------------------------------------------ locks
_PROCESS_LOCKS: dict[str, asyncio.Lock] = {}


def _default_redis(url: str) -> Any:
    return importlib.import_module("redis.asyncio").from_url(url)


#: Builds the Redis client of the refresh lock (tests swap in ``fakeredis``).
redis_factory: Callable[[str], Any] = _default_redis


@asynccontextmanager
async def _redis_lock(url: str, credential_id: str) -> AsyncIterator[None]:
    key = f"lkap:mcp_oauth:refresh:{credential_id}"
    owner = secrets.token_hex(16)
    client = redis_factory(url)
    acquired = False
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + LOCK_WAIT_S
        try:
            while not await client.set(key, owner, nx=True, px=LOCK_TTL_MS):
                if loop.time() >= deadline:
                    raise TokenUnavailable("lock_timeout")
                await asyncio.sleep(0.05)
            acquired = True
        except TokenUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - a Redis outage must not stop every refresh
            # The row lock (Postgres) and the process lock still hold; say so once per event.
            log.warning("mcp_oauth_refresh_lock_redis_unavailable", error=type(exc).__name__)
        yield
    finally:
        try:
            if acquired:
                held = await client.get(key)
                if held is not None and (held.decode() if isinstance(held, bytes) else str(held)) == owner:
                    await client.delete(key)
        except Exception as exc:  # noqa: BLE001 - the TTL releases it anyway
            log.warning("mcp_oauth_refresh_lock_release_failed", error=type(exc).__name__)
        finally:
            close = getattr(client, "aclose", None)
            if close is not None:
                await close()


@asynccontextmanager
async def refresh_lock(settings: Settings, credential_id: str) -> AsyncIterator[None]:
    """Single-flight for one credential's refresh: the process lock, then Redis when configured."""
    lock = _PROCESS_LOCKS.setdefault(credential_id, asyncio.Lock())
    async with lock:
        if settings.redis_url:
            async with _redis_lock(settings.redis_url, credential_id):
                yield
        else:
            yield


# ---------------------------------------------------------------------------- refresh
class _InvalidGrant(Exception):  # noqa: N818 - internal signal
    """The token endpoint answered ``invalid_grant``."""


def client_authentication(
    data: dict[str, str], *, client_id: str, client_secret: str | None, method: str | None
) -> tuple[str, str] | None:
    """Add the client's authentication to a token/revocation form; the Basic pair when it uses one.

    ``client_secret_basic`` (RFC 6749 §2.3.1: both parts form-url-encoded before the Basic
    encoding) drops ``client_id`` from the form; ``client_secret_post`` adds the secret; a
    public client sends ``client_id`` alone.
    """
    if client_secret and method == "client_secret_basic":
        data.pop("client_id", None)
        return quote(client_id, safe=""), quote(client_secret, safe="")
    data["client_id"] = client_id
    if client_secret and method == "client_secret_post":
        data["client_secret"] = client_secret
    return None


async def _refresh(client: httpx.AsyncClient, policy: UrlPolicy, bag: dict[str, str]) -> OAuthToken:
    endpoint = require_url(bag.get("token_endpoint"), policy, field="token_endpoint")
    data = {"grant_type": "refresh_token", "refresh_token": bag["refresh_token"]}
    if bag.get("resource"):
        data["resource"] = bag["resource"]
    basic = client_authentication(
        data,
        client_id=bag.get("client_id", ""),
        client_secret=bag.get("client_secret"),
        method=bag.get("token_endpoint_auth_method"),
    )
    fetched = await fetch(
        client,
        "POST",
        endpoint,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=data,
        auth=basic,
    )
    if fetched.status != 200:
        error = (fetched.json_object() or {}).get("error")
        if fetched.status in (400, 401) and error == "invalid_grant":
            raise _InvalidGrant()
        raise McpOauthError(
            "refresh_failed", f"the sign-in provider did not refresh the token ({fetched.status})"
        )
    try:
        token = OAuthToken.model_validate(fetched.json_object())
    except ValidationError as exc:
        raise McpOauthError("refresh_failed", "the sign-in provider's refresh answer is not valid") from exc
    if not token.access_token or len(token.access_token) > MAX_TOKEN_CHARS:
        raise McpOauthError("refresh_failed", "the sign-in provider sent no usable access token")
    if token.refresh_token is not None and len(token.refresh_token) > MAX_TOKEN_CHARS:
        raise McpOauthError("refresh_failed", "the sign-in provider's refresh token is too long")
    return token


def _refreshed_bag(bag: dict[str, str], token: OAuthToken, now: dt.datetime) -> dict[str, str]:
    updated = dict(bag)
    updated["access_token"] = token.access_token
    if token.refresh_token:
        updated["refresh_token"] = token.refresh_token  # rotation: the old one is spent
    if token.expires_in is not None and token.expires_in > 0:
        updated["expires_at"] = (now + dt.timedelta(seconds=int(token.expires_in))).isoformat()
    else:
        updated.pop("expires_at", None)
    if token.scope:
        updated["scope"] = token.scope
    updated["status"] = STATUS_ACTIVE
    updated["last_refresh_at"] = now.isoformat()
    return updated


async def get_access_token(
    database: Database,
    vault: Vault,
    client: httpx.AsyncClient,
    settings: Settings,
    *,
    workspace_id: str,
    credential_id: str,
    tool_id: str | None = None,
    rejected_sha256: str | None = None,
    may_refresh: bool = True,
    jobs: JobsService | None = None,
    now: dt.datetime | None = None,
) -> AccessToken:
    """A working access token of one ``mcp-oauth`` credential, refreshed when needed.

    Args:
        database: The process database; the refresh commits in its own transaction.
        vault: Decrypts and encrypts the bag.
        client: The api's guarded outbound client.
        settings: ``LKAP_REDIS_URL`` (the lock) and the URL policy.
        workspace_id: The credential's workspace.
        credential_id: The ``mcp-oauth`` credential.
        tool_id: For the audit row and the webhook.
        rejected_sha256: The SHA-256 of a token the MCP server just refused; when it is
            still the stored one the token is refreshed even though it has not expired.
        may_refresh: ``False`` returns the stored token as long as it has not expired and
            never writes (a caller that already holds SQLite's write lock).
        jobs: Emits ``tool.needs_reauth`` when given.
        now: The clock (tests).

    Raises:
        NeedsReauth: No sign-in, or it was revoked or refused (``invalid_grant``).
        TokenUnavailable: The refresh failed for a transient reason and the stored token
            no longer works (or refreshing was not allowed).
    """
    ts = now or utcnow()
    async with database.session() as db:
        row = await _credential(db, workspace_id, credential_id)
        if row is None:
            raise NeedsReauth("not_connected")
        bag = vault.decrypt(row.ciphertext)
    _check_status(bag)
    current = _current(bag)
    if not _rejected(current, rejected_sha256) and _fresh(current, ts):
        return _require(current)
    if not may_refresh:
        if _valid(current, ts) and not _rejected(current, rejected_sha256):
            return _require(current)
        raise TokenUnavailable("refresh_deferred")

    needs_reauth: str | None = None
    result: AccessToken | None = None
    async with refresh_lock(settings, credential_id), database.session() as db:
        row = await _credential(db, workspace_id, credential_id, for_update=True)
        if row is None:
            raise NeedsReauth("not_connected")
        bag = vault.decrypt(row.ciphertext)  # re-read: another request may have refreshed
        _check_status(bag)
        current = _current(bag)
        rejected = _rejected(current, rejected_sha256)
        if not rejected and _fresh(current, ts):
            return _require(current)
        if not bag.get("refresh_token"):
            if not rejected and _valid(current, ts):
                return _require(current)
            needs_reauth = "no_refresh_token"
        else:
            try:
                token = await _refresh(client, UrlPolicy.from_settings(settings), bag)
            except _InvalidGrant:
                needs_reauth = "invalid_grant"
            except McpOauthError as exc:
                log.warning("mcp_oauth_refresh_failed", tool_id=tool_id, reason=exc.reason)
                if not rejected and _valid(current, ts):
                    return _require(current)
                raise TokenUnavailable(exc.reason) from exc
            else:
                updated = _refreshed_bag(bag, token, ts)
                row.ciphertext = vault.encrypt(updated)
                row.updated_at = ts
                row.last_test_at, row.last_test_ok, row.last_test_message = ts, True, "refreshed"
                result = AccessToken(updated["access_token"], parse_time(updated.get("expires_at")))
                log.info(
                    "mcp_oauth_refreshed",
                    tool_id=tool_id,
                    credential_id=credential_id,
                    rotated=bool(token.refresh_token),
                )
        if needs_reauth is not None:
            bag["status"] = STATUS_NEEDS_REAUTH
            row.ciphertext = vault.encrypt(bag)
            row.updated_at = ts
            row.last_test_at, row.last_test_ok = ts, False
            row.last_test_message = "the sign-in needs to be renewed: sign in again"
            audit.record(
                db,
                workspace_id=workspace_id,
                actor_type="system",
                actor_id=None,
                action="mcp_oauth.needs_reauth",
                target_type="tool",
                target_id=tool_id,
                payload={
                    "reason": needs_reauth,
                    "credential_id": credential_id,
                    "issuer_host": host_of(bag.get("issuer", "")),
                },
            )
    if result is not None:
        return result
    assert needs_reauth is not None  # noqa: S101 - every other branch returned or raised
    log.warning("mcp_oauth_needs_reauth", tool_id=tool_id, reason=needs_reauth)
    if jobs is not None:
        # After the commit: `emit` opens its own session (the SQLite rule of ask #40).
        await webhooks.emit(
            database,
            jobs,
            workspace_id=workspace_id,
            event_type=webhook_events.TOOL_NEEDS_REAUTH,
            data={"tool_id": tool_id, "reason": needs_reauth},
        )
    raise NeedsReauth(needs_reauth)


async def _credential(
    db: AsyncSession, workspace_id: str, credential_id: str, *, for_update: bool = False
) -> Credential | None:
    statement = select(Credential).where(
        Credential.id == credential_id,
        Credential.workspace_id == workspace_id,
        Credential.provider_id == MCP_OAUTH_PROVIDER_ID,
    )
    if for_update:
        statement = statement.with_for_update()
    row: Credential | None = await db.scalar(statement)
    return row


# ------------------------------------------------------------------ the worker's route
def oauth_definition(row: Tool) -> McpServerDefinition | None:
    """The tool's definition when it is an MCP server that signs in, else ``None``."""
    if row.kind != "mcp":
        return None
    try:
        definition = _TOOL.validate_python(row.definition)
    except ValidationError:
        return None
    if isinstance(definition, McpServerDefinition) and isinstance(definition.auth, McpOAuthAuth):
        return definition
    return None


async def tool_access(
    database: Database,
    vault: Vault,
    client: httpx.AsyncClient,
    settings: Settings,
    *,
    tool: Tool,
    definition: McpServerDefinition,
    rejected_sha256: str | None = None,
    may_refresh: bool = True,
    jobs: JobsService | None = None,
) -> AccessToken:
    """The access token of the oauth MCP tool ``tool``, from the sign-in that belongs to it.

    Raises:
        NeedsReauth: The tool has no sign-in of its own (``not_connected``), or it needs
            an admin (see :func:`get_access_token`).
        TokenUnavailable: See :func:`get_access_token`.
    """
    auth = definition.auth
    credential_id = auth.credential_id if isinstance(auth, McpOAuthAuth) else None
    if not credential_id:
        raise NeedsReauth("not_connected")
    async with database.session() as db:
        row = await _credential(db, tool.workspace_id, credential_id)
        bound = row is not None and binds_tool(
            vault.decrypt(row.ciphertext), tool_id=tool.id, url=definition.url
        )
    if not bound:
        raise NeedsReauth("not_connected")
    return await get_access_token(
        database,
        vault,
        client,
        settings,
        workspace_id=tool.workspace_id,
        credential_id=credential_id,
        tool_id=tool.id,
        rejected_sha256=rejected_sha256,
        may_refresh=may_refresh,
        jobs=jobs,
    )


async def issue_session_token(
    db: AsyncSession,
    database: Database,
    vault: Vault,
    client: httpx.AsyncClient,
    settings: Settings,
    jobs: JobsService | None,
    *,
    tool_id: str,
    payload: McpOAuthTokenIn,
) -> McpOAuthTokenOut:
    """``POST /internal/v1/tools/{tool_id}/oauth/token``: a fresh access token for a live session.

    The session must be live and its agent must use the tool (``config.tools.tool_ids``,
    which is also a flow's tool pool); the tool must be an enabled oauth MCP server of the
    session's workspace whose sign-in belongs to it.

    Raises:
        NotFoundError: Unknown session, or a tool the session's agent does not use.
        ConflictError: The session ended, or the sign-in needs an admin (``needs_reauth``).
        TokenServiceUnavailableError: A transient refresh failure.
    """
    session = await db.scalar(
        select(SessionRow)
        .where(SessionRow.id == payload.session_id)
        # The worker is not a workspace member: the session row names the workspace.
        .execution_options(**{CROSS_WORKSPACE_OPTION: True})
    )
    if session is None:
        raise NotFoundError(f"unknown session '{payload.session_id}'")
    if session.status not in _LIVE_SESSION_STATUSES:
        raise ConflictError("the session has ended", details={"reason": "session_ended"})
    agent = await db.scalar(
        select(Agent).where(Agent.id == session.agent_id, Agent.workspace_id == session.workspace_id)
    )
    if agent is None or tool_id not in AgentConfig.model_validate(agent.config).tools.tool_ids:
        raise NotFoundError(f"the session's agent does not use tool '{tool_id}'")
    row = await db.scalar(
        select(Tool).where(
            Tool.id == tool_id, Tool.workspace_id == session.workspace_id, Tool.enabled.is_(True)
        )
    )
    definition = oauth_definition(row) if row is not None else None
    if row is None or definition is None:
        raise NotFoundError(f"tool '{tool_id}' is not an MCP server that signs in")
    try:
        token = await tool_access(
            database,
            vault,
            client,
            settings,
            tool=row,
            definition=definition,
            rejected_sha256=payload.rejected_token_sha256,
            jobs=jobs,
        )
    except NeedsReauth as exc:
        raise ConflictError(
            "this MCP server needs to be signed in again by an admin", details={"reason": "needs_reauth"}
        ) from exc
    except TokenUnavailable as exc:
        raise TokenServiceUnavailableError(
            "the sign-in provider could not refresh the token just now", details={"reason": exc.reason}
        ) from exc
    log.info("mcp_oauth_token_issued", tool_id=tool_id, session_id=session.id)
    return McpOAuthTokenOut(access_token=token.access_token, expires_at=token.expires_at)


__all__ = [
    "LOCK_TTL_MS",
    "REFRESH_SKEW",
    "STATUS_NEEDS_REAUTH",
    "AccessToken",
    "NeedsReauth",
    "TokenServiceUnavailableError",
    "TokenUnavailable",
    "client_authentication",
    "get_access_token",
    "issue_session_token",
    "oauth_definition",
    "redis_factory",
    "refresh_lock",
    "token_sha256",
    "tool_access",
]
