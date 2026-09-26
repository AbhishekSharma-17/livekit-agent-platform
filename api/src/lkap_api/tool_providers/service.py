"""Connected apps: the key, the catalogue, connect/callback/status/disconnect (docs/v5/COMPOSIO.md §2, §4).

Storage (D-V5-C3, no new table): a connected app is a ``credentials`` row with
``provider_id == TOOL_PROVIDER_ACCOUNT``. Its encrypted bag holds references
only (toolkit, auth config id, connected account id, subject, method, status,
picked actions, and — while a sign-in is pending — the SHA-256 of the flow
nonce and its expiry). The row's plain ``last_test_*`` columns mirror the
status (``last_test_message`` = status, ``last_test_ok`` = active,
``last_test_at`` = last checked), so the callback and the sweep can select
pending rows without decrypting a whole workspace.

The callback (D-V5-C5) is bound to the row by ``flow = <row id>.<nonce>``:
the row is loaded by id, the nonce compared in constant time against the
stored hash, the pending state claimed with a conditional ``UPDATE`` (single
use), the expiry and the ``connected_account_id`` checked, and finally
Composio asked whether that account is ``ACTIVE`` for the row's own subject.
Nothing from the query string is trusted on its own.

Secrets: the key reaches an adapter and nothing else; ``fields`` typed in a
Connect dialog are forwarded to Composio and dropped. No log line, audit
payload or error carries either, nor a vendor sign-in URL.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import secrets
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import urlencode

from lkap_contracts.tool_providers import (
    COMPOSIO_PROVIDER_ID,
    TOOL_PROVIDER_ACCOUNT,
    AppActionOut,
    AppActionPage,
    AppActionsPickIn,
    AppActionsPickOut,
    AppAuthField,
    AppConnectIn,
    AppConnectionOut,
    AppConnectionPage,
    AppConnectOut,
    AppKeyTestOut,
    AppsStatusOut,
    AuthOption,
    ConnectionStatus,
    ConnectMethod,
    ToolkitOut,
    ToolkitPage,
    action_risk,
    agent_subject,
    workspace_subject,
)
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.auth import audit
from lkap_api.auth.deps import WorkspaceContext
from lkap_api.auth.ratelimit import RateLimitedError
from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import Agent, Credential, Tool, WorkspaceProvider, utcnow
from lkap_api.errors import ApiError, ConflictError, NotFoundError, UnprocessableEntityError
from lkap_api.logging import get_logger
from lkap_api.settings import Settings
from lkap_api.tool_providers import materialise
from lkap_api.tool_providers.adapter import (
    AdapterFactory,
    ToolProviderAdapter,
    ToolProviderAuthError,
    ToolProviderError,
    ToolProviderNotFoundError,
    ToolProviderRateLimitedError,
    ToolProviderRequestError,
    scrub_vendor_text,
)
from lkap_api.tool_providers.composio import OAUTH_REDIRECT_URI
from lkap_api.vault import Vault

log = get_logger(__name__)

#: How long a started sign-in stays claimable (Composio expires INITIATED after 10 minutes).
FLOW_TTL: Final = dt.timedelta(minutes=10)

#: Catalogue cache lifetime per workspace key (D-V2-9 pattern, COMPOSIO.md §4).
CATALOG_TTL_S: Final = 600.0

#: Most entries the in-process catalogue cache keeps.
CATALOG_CACHE_MAX: Final = 512

#: Vendor pages followed when aggregating `/toolkits/categories` into one list
#: (the category list is small — Composio's own docs show ~40 — so this caps
#: work if the vendor ever pages it more finely than expected).
CATEGORY_PAGE_CAP: Final = 10

#: Pasted-key tests per workspace per minute.
KEY_TEST_PER_MIN: Final = 10

#: Page size and page cap of the destructive-action scan (R-V5-9). The page size is the
#: console picker's, so the scan shares its cache entries; 20 pages cover 1000 actions per app.
DESTRUCTIVE_SCAN_LIMIT: Final = 50
DESTRUCTIVE_SCAN_PAGES: Final = 20

#: Where the console shows Apps (the callback's redirect target).
CONSOLE_APPS_PATH: Final = "/console/tools"

_VENDOR_STATUS: Final[dict[str, ConnectionStatus]] = {
    "ACTIVE": "active",
    "INITIATED": "initiated",
    "INITIALIZING": "initiated",
    "EXPIRED": "expired",
    "FAILED": "failed",
    "INACTIVE": "inactive",
}

_BROKEN: Final[frozenset[str]] = frozenset({"expired", "failed", "inactive"})

_OAUTH_SCHEMES: Final[frozenset[str]] = frozenset({"OAUTH2", "OAUTH1", "OAUTH1A", "DCR_OAUTH"})

#: Key-style schemes in the order the Connect dialog prefers them.
_KEY_SCHEMES: Final[tuple[str, ...]] = ("API_KEY", "BEARER_TOKEN", "BASIC", "BASIC_WITH_JWT")

_KEY_OPTION: Final[dict[str, AuthOption]] = {
    "API_KEY": "api_key",
    "BEARER_TOKEN": "bearer",
    "BASIC": "basic",
    "BASIC_WITH_JWT": "basic",
}


# ============================================================================ errors
class AppsNotEnabledError(ApiError):
    """409 — the workspace has no Composio key, or the provider is disabled."""

    status_code = 409
    code = "apps_not_enabled"


class ToolProviderApiError(ApiError):
    """502 — Composio refused or failed a call made with the stored key."""

    status_code = 502
    code = "tool_provider_error"


class ToolProviderKeyRejectedError(ApiError):
    """502 — Composio rejected the workspace's stored key."""

    status_code = 502
    code = "tool_provider_unauthorized"


def redact(exc: ToolProviderError, secrets: Iterable[str]) -> ToolProviderError:
    """``exc`` with every given secret value cut out of its message (a vendor may echo input)."""
    message = exc.message
    for value in secrets:
        if value and len(value) >= 4:
            message = message.replace(value, "[redacted]")
    exc.message = message
    exc.args = (message,)
    return exc


def api_error(exc: ToolProviderError) -> ApiError:
    """Turn a vendor failure into the api's error envelope (messages are scrubbed)."""
    match exc:
        case ToolProviderAuthError():
            return ToolProviderKeyRejectedError(
                "Composio rejected this workspace's key; validate or rotate it under Tools, Apps",
                details={"reason": exc.reason},
            )
        case ToolProviderNotFoundError():
            return NotFoundError(f"Composio has no such object: {exc.message}")
        case ToolProviderRateLimitedError():
            return RateLimitedError(
                "Composio is rate limiting this workspace; try again shortly", details={"reason": exc.reason}
            )
        case ToolProviderRequestError():
            return UnprocessableEntityError(
                f"Composio refused the request: {exc.message}", details={"reason": exc.reason}
            )
        case _:
            return ToolProviderApiError(
                f"Composio is unavailable: {exc.message}", details={"reason": exc.reason}
            )


# ============================================================================ helpers
def _iso(value: dt.datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def _parse_dt(value: object) -> dt.datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=dt.UTC)


def _hash_nonce(nonce: str) -> str:
    return hashlib.sha256(nonce.encode("utf-8")).hexdigest()


def vendor_status(value: object) -> ConnectionStatus:
    """Composio's connection status (``ACTIVE`` …) as the contract's lower-case value."""
    return _VENDOR_STATUS.get(str(value or "").upper(), "unknown")


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


# ============================================================================ records
@dataclass
class AppConnection:
    """A decrypted connected-app row (the bag holds references only, never a vendor token)."""

    row: Credential
    toolkit: str
    method: ConnectMethod
    subject: str
    toolkit_name: str | None = None
    status: ConnectionStatus = "initiated"
    auth_config_id: str | None = None
    auth_scheme: str | None = None
    managed: bool = False
    connected_account_id: str | None = None
    previous_account_id: str | None = None
    nonce_hash: str | None = None
    flow_expires_at: dt.datetime | None = None
    connected_at: dt.datetime | None = None
    needs_reconnect: bool = False
    picked_actions: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        """The connection id (the credential row's id)."""
        return self.row.id

    @property
    def agent_id(self) -> str | None:
        """The agent of an ``agent:<id>`` subject."""
        return self.subject.removeprefix("agent:") if self.subject.startswith("agent:") else None

    @classmethod
    def from_row(cls, row: Credential, vault: Vault) -> AppConnection:
        """Decrypt a row (a corrupt bag reads as an ``unknown`` connection, never an exception)."""
        try:
            bag = vault.decrypt(row.ciphertext)
        except Exception:  # noqa: BLE001 - a bad row must not break the list
            bag = {}
        method = bag.get("method", "managed")
        try:
            picked = json.loads(bag.get("picked_actions") or "[]")
        except ValueError:
            picked = []
        return cls(
            row=row,
            toolkit=bag.get("toolkit", ""),
            toolkit_name=bag.get("toolkit_name") or None,
            method=method if method in ("managed", "custom_oauth", "api_key", "none") else "managed",  # type: ignore[arg-type]
            subject=bag.get("subject", ""),
            status=vendor_status(bag.get("status")) if bag else "unknown",
            auth_config_id=bag.get("auth_config_id") or None,
            auth_scheme=bag.get("auth_scheme") or None,
            managed=bag.get("managed") == "true",
            connected_account_id=bag.get("connected_account_id") or None,
            previous_account_id=bag.get("previous_account_id") or None,
            nonce_hash=bag.get("nonce_sha256") or None,
            flow_expires_at=_parse_dt(bag.get("flow_expires_at")),
            connected_at=_parse_dt(bag.get("connected_at")),
            needs_reconnect=bag.get("needs_reconnect") == "true",
            picked_actions=[str(a) for a in picked if isinstance(a, str)],
        )

    def bag(self) -> dict[str, str]:
        """The row's secret bag: every value a string (the vault's shape)."""
        return {
            "provider": COMPOSIO_PROVIDER_ID,
            "toolkit": self.toolkit,
            "toolkit_name": self.toolkit_name or "",
            "method": self.method,
            "subject": self.subject,
            "status": self.status.upper(),
            "auth_config_id": self.auth_config_id or "",
            "auth_scheme": self.auth_scheme or "",
            "managed": "true" if self.managed else "false",
            "connected_account_id": self.connected_account_id or "",
            "previous_account_id": self.previous_account_id or "",
            "nonce_sha256": self.nonce_hash or "",
            "flow_expires_at": _iso(self.flow_expires_at),
            "connected_at": _iso(self.connected_at),
            "needs_reconnect": "true" if self.needs_reconnect else "false",
            "picked_actions": json.dumps(self.picked_actions),
        }

    def save(self, vault: Vault, *, checked_at: dt.datetime | None = None) -> None:
        """Write the bag back and mirror the status into the plain columns."""
        row = self.row
        row.ciphertext = vault.encrypt(self.bag())
        tail = (self.connected_account_id or self.toolkit)[-4:]
        row.fingerprint = f"…{tail}" if tail else "…"
        row.label = self.label()
        row.last_test_message = self.status
        row.last_test_ok = self.status == "active"
        if checked_at is not None:
            row.last_test_at = checked_at
        row.updated_at = utcnow()

    def label(self) -> str:
        """The row's display label."""
        scope = "workspace" if self.subject.startswith("ws:") else "one agent"
        return f"{self.toolkit_name or self.toolkit} ({scope})"[:200]

    def to_out(self, agents_using: int = 0) -> AppConnectionOut:
        """The public view (no ids of the vendor account, no auth config)."""
        return AppConnectionOut(
            id=self.id,
            toolkit=self.toolkit,
            toolkit_name=self.toolkit_name,
            subject=self.subject,
            status=self.status,
            method=self.method,
            connected_at=self.connected_at,
            last_checked_at=self.row.last_test_at,
            needs_reconnect=self.needs_reconnect or self.status in _BROKEN,
            picked_actions=list(self.picked_actions),
            agents_using=agents_using,
        )


# ============================================================================ key
@dataclass(frozen=True)
class KeyState:
    """The workspace's Composio key row (if any) and whether the provider is on."""

    credential: Credential | None
    enabled: bool


async def key_state(db: AsyncSession, workspace_id: str) -> KeyState:
    """The default Composio key of a workspace: the configured default, else the newest row."""
    settings_row = await db.scalar(
        select(WorkspaceProvider).where(
            WorkspaceProvider.workspace_id == workspace_id,
            WorkspaceProvider.provider_id == COMPOSIO_PROVIDER_ID,
        )
    )
    enabled = settings_row is None or bool(settings_row.enabled)
    rows = (
        (
            await db.execute(
                select(Credential)
                .where(
                    Credential.workspace_id == workspace_id, Credential.provider_id == COMPOSIO_PROVIDER_ID
                )
                .order_by(Credential.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    chosen: Credential | None = None
    if settings_row is not None and settings_row.default_credential_id:
        chosen = next((r for r in rows if r.id == settings_row.default_credential_id), None)
    if chosen is None and rows:
        chosen = rows[0]
    return KeyState(credential=chosen, enabled=enabled)


async def workspace_adapter(
    db: AsyncSession,
    vault: Vault,
    factory: AdapterFactory,
    workspace_id: str,
    *,
    require_enabled: bool = True,
) -> tuple[ToolProviderAdapter, Credential]:
    """An adapter built with the workspace's stored key, or :class:`AppsNotEnabledError`."""
    state = await key_state(db, workspace_id)
    if state.credential is None:
        raise AppsNotEnabledError(
            "Apps are not set up: add a Composio key first (Tools, Apps, Enable Composio)"
        )
    if require_enabled and not state.enabled:
        raise AppsNotEnabledError("Apps are turned off for this workspace; enable Composio again to use them")
    api_key = vault.decrypt(state.credential.ciphertext).get("api_key", "")
    if not api_key:
        raise AppsNotEnabledError("the stored Composio key is empty; rotate it")
    return factory(api_key), state.credential


async def test_key(adapter: ToolProviderAdapter, *, api_key: str = "") -> AppKeyTestOut:
    """Check a key: the project it belongs to, else a scoped list, then the app count.

    ``session_info`` names the project; if that call is unavailable or refuses
    the key (project keys may not read it), the project's auth-config list
    (which needs a valid key) decides. A rejected
    key is ``ok=false``; an unreachable vendor is ``ok=false`` with that said.
    The organisation member's personal name is never read.
    """
    project_name: str | None = None
    account_name: str | None = None
    try:
        info = await adapter.session_info()
    except ToolProviderError:
        # Project API keys may be refused by the account endpoint (401/403) while
        # being perfectly valid for the project's own resources, so a refusal
        # here is not a verdict: the key-scoped list below decides.
        try:
            await adapter.list_auth_configs(limit=1)
        except ToolProviderAuthError:
            return AppKeyTestOut(ok=False, message="Composio rejected this key")
        except ToolProviderError as exc:
            reason = redact(exc, [api_key]).message
            return AppKeyTestOut(ok=False, message=f"Couldn't reach Composio: {reason}")
    else:
        project = _dict(info.get("project"))
        project_name = scrub_vendor_text(project.get("name"), limit=120) or None
        org = _dict(project.get("org")) or _dict(info.get("org")) or _dict(info.get("organization"))
        account_name = scrub_vendor_text(org.get("name"), limit=120) or None
    count: int | None = None
    try:
        page = await adapter.list_toolkits(limit=1)
        count = _int(page.get("total_items"))
    except ToolProviderError:
        count = None
    where = project_name or account_name
    parts = ["Key works"]
    if where:
        parts.append(f"connected to {where}")
    if count is not None:
        parts.append(f"{count} apps available")
    return AppKeyTestOut(
        ok=True,
        account_name=account_name,
        project_name=project_name,
        toolkits_count=count,
        message=" — ".join(parts),
    )


# ============================================================================ catalogue
class CatalogCache:
    """A small in-process TTL cache of vendor catalogue pages, keyed per workspace key."""

    def __init__(
        self,
        ttl_s: float = CATALOG_TTL_S,
        max_entries: int = CATALOG_CACHE_MAX,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_s
        self._max = max_entries
        self._clock = clock
        self._items: OrderedDict[tuple[Any, ...], tuple[float, dict[str, Any]]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: tuple[Any, ...]) -> dict[str, Any] | None:
        """A fresh entry, or ``None``."""
        entry = self._items.get(key)
        if entry is None or self._clock() - entry[0] >= self._ttl:
            self.misses += 1
            return None
        self._items.move_to_end(key)
        self.hits += 1
        return entry[1]

    def put(self, key: tuple[Any, ...], value: dict[str, Any]) -> None:
        """Store a page, evicting the oldest past the cap."""
        self._items[key] = (self._clock(), value)
        self._items.move_to_end(key)
        while len(self._items) > self._max:
            self._items.popitem(last=False)


def _cache_key(credential: Credential, *parts: Any) -> tuple[Any, ...]:
    """A key that changes when the key row is rotated (new fingerprint / updated_at)."""
    return (
        credential.workspace_id,
        credential.id,
        credential.fingerprint,
        _iso(credential.updated_at),
        *parts,
    )


def _categories(meta: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for item in _list(meta.get("categories")):
        name = item if isinstance(item, str) else _str(_dict(item).get("name")) or _str(_dict(item).get("id"))
        if name:
            out.append(scrub_vendor_text(name, limit=60))
    return out


def _schemes(item: dict[str, Any]) -> list[str]:
    raw = _list(item.get("auth_schemes")) or [
        _dict(detail).get("mode") for detail in _list(item.get("auth_config_details"))
    ]
    return [str(s).upper() for s in raw if s]


def _auth_options(item: dict[str, Any]) -> list[AuthOption]:
    schemes = _schemes(item)
    managed = [str(s).upper() for s in _list(item.get("composio_managed_auth_schemes")) if s]
    options: list[AuthOption] = []
    if item.get("no_auth") is True or (not schemes and not managed):
        options.append("none")
    if any(s in _OAUTH_SCHEMES for s in managed):
        options.append("oauth_managed")
    if any(s in _OAUTH_SCHEMES for s in schemes):
        options.append("oauth_custom")
    for scheme in _KEY_SCHEMES:
        option = _KEY_OPTION[scheme]
        if scheme in schemes and option not in options:
            options.append(option)
    return options


def _field_list(value: object, *, secret_default: bool) -> list[AppAuthField]:
    out: list[AppAuthField] = []
    for raw in _list(value):
        entry = _dict(raw)
        name = _str(entry.get("name"))
        if not name:
            continue
        label = _str(entry.get("displayName")) or _str(entry.get("display_name")) or name
        secret = entry.get("is_secret")
        out.append(
            AppAuthField(
                name=name,
                label=scrub_vendor_text(label, limit=80),
                secret=bool(secret) if isinstance(secret, bool) else secret_default,
                required=bool(entry.get("required", True)),
            )
        )
    return out


def _auth_fields(item: dict[str, Any]) -> dict[str, list[AppAuthField]]:
    """Per auth option, what the Connect dialog asks for (from ``auth_config_details``)."""
    out: dict[str, list[AppAuthField]] = {}
    for raw in _list(item.get("auth_config_details")):
        detail = _dict(raw)
        mode = _str(detail.get("mode")).upper()
        fields = _dict(detail.get("fields"))
        if mode in _OAUTH_SCHEMES:
            creation = _dict(fields.get("auth_config_creation"))
            names = _field_list(creation.get("required"), secret_default=True)
            out.setdefault(
                "oauth_custom",
                names
                or [
                    AppAuthField(name="client_id", label="Client ID", secret=False),
                    AppAuthField(name="client_secret", label="Client secret"),
                ],
            )
        elif mode in _KEY_OPTION:
            initiation = _dict(fields.get("connected_account_initiation"))
            required = _field_list(initiation.get("required"), secret_default=True)
            optional = [
                f.model_copy(update={"required": False})
                for f in _field_list(initiation.get("optional"), secret_default=False)
            ]
            out.setdefault(_KEY_OPTION[mode], required + optional)
    return out


class ToolProviderCategoryOut(BaseModel):
    """One toolkit category. Kept api-local (docs/CONTRACTS.md §1: not every
    response model needs a generated TS type) rather than added to
    ``lkap_contracts`` — the web hook declares its own matching interface.

    ``id`` is what `GET .../toolkits?category=` filters on; ``name`` is the
    vendor's display label (title-cased already, most of the time).
    """

    id: str
    name: str


class ToolProviderCategoryPage(BaseModel):
    """Every category the vendor knows, aggregated across its own pages (see :func:`list_categories`)."""

    items: list[ToolProviderCategoryOut]


def category_out(item: dict[str, Any]) -> ToolProviderCategoryOut:
    """A vendor category trimmed for the console."""
    id_ = (_str(item.get("id")) or _str(item.get("slug"))).lower()
    name = scrub_vendor_text(item.get("name") or id_, limit=60)
    return ToolProviderCategoryOut(id=id_, name=name)


def toolkit_out(item: dict[str, Any], *, detail: bool = False) -> ToolkitOut:
    """A vendor toolkit trimmed for the console (logo, description and categories live in ``meta``)."""
    meta = _dict(item.get("meta"))
    logo = _str(meta.get("logo")) or _str(item.get("logo")) or None
    options = _auth_options(item)
    return ToolkitOut(
        slug=_str(item.get("slug")).lower(),
        name=scrub_vendor_text(item.get("name") or item.get("slug"), limit=120),
        logo=logo if logo and logo.startswith("https://") else None,
        categories=_categories(meta),
        description=scrub_vendor_text(meta.get("description") or item.get("description"), limit=400),
        auth=options,
        tools_count=_int(meta.get("tools_count")) or 0,
        auth_fields=_auth_fields(item) if detail else {},
        auth_guide_url=(_str(item.get("auth_guide_url")) or None) if detail else None,
        oauth_redirect_uri=OAUTH_REDIRECT_URI if detail and "oauth_custom" in options else None,
    )


def action_out(item: dict[str, Any]) -> AppActionOut:
    """A vendor tool trimmed for the Actions picker."""
    slug = _str(item.get("slug"))
    tags = [str(t)[:40] for t in _list(item.get("tags")) if isinstance(t, str)]
    params = _dict(item.get("input_parameters"))
    return AppActionOut(
        slug=slug,
        name=scrub_vendor_text(item.get("name") or slug, limit=120),
        description=scrub_vendor_text(item.get("description") or item.get("human_description"), limit=600),
        parameters=params,
        important="important" in {t.lower() for t in tags} or item.get("important") is True,
        tags=tags,
        risk=action_risk(slug, tags),
        version=_str(item.get("version")) or None,
    )


async def _cached(
    cache: CatalogCache, key: tuple[Any, ...], fetch: Callable[[], Any], refresh: bool
) -> dict[str, Any]:
    if not refresh:
        hit = cache.get(key)
        if hit is not None:
            return hit
    try:
        value: dict[str, Any] = await fetch()
    except ToolProviderError as exc:
        raise api_error(exc) from exc
    cache.put(key, value)
    return value


async def list_toolkits(
    db: AsyncSession,
    vault: Vault,
    factory: AdapterFactory,
    cache: CatalogCache,
    ctx: WorkspaceContext,
    *,
    query: str | None,
    category: str | None,
    cursor: str | None,
    limit: int,
    connected_only: bool = False,
    refresh: bool = False,
) -> ToolkitPage:
    """A page of apps with this workspace's connection state."""
    connections = await list_connection_records(db, vault, ctx.workspace_id)
    if connected_only:
        items = []
        adapter, credential = await workspace_adapter(db, vault, factory, ctx.workspace_id)
        seen: set[str] = set()
        for conn in connections:
            if conn.toolkit in seen or conn.status != "active":
                continue
            seen.add(conn.toolkit)
            key = _cache_key(credential, "toolkit", conn.toolkit)
            try:
                raw = await _cached(cache, key, _toolkit_fetch(adapter, conn.toolkit), refresh)
            except NotFoundError:
                raw = {"slug": conn.toolkit, "name": conn.toolkit_name or conn.toolkit}
            items.append(toolkit_out(raw))
        return ToolkitPage(items=_mark_connected(items, connections), next_cursor=None, total=len(items))
    adapter, credential = await workspace_adapter(db, vault, factory, ctx.workspace_id)
    key = _cache_key(credential, "toolkits", query or "", category or "", cursor or "", limit)
    page = await _cached(
        cache,
        key,
        lambda: adapter.list_toolkits(search=query, category=category, cursor=cursor, limit=limit),
        refresh,
    )
    items = [toolkit_out(_dict(item)) for item in _list(page.get("items"))]
    items = [item for item in items if item.slug]
    return ToolkitPage(
        items=_mark_connected(items, connections),
        next_cursor=_str(page.get("next_cursor")) or None,
        total=_int(page.get("total_items")),
    )


def _toolkit_fetch(adapter: ToolProviderAdapter, slug: str) -> Callable[[], Any]:
    async def fetch() -> dict[str, Any]:
        return await adapter.get_toolkit(slug)

    return fetch


def _mark_connected(items: list[ToolkitOut], connections: list[AppConnection]) -> list[ToolkitOut]:
    active = {c.toolkit: c.id for c in connections if c.status == "active"}
    return [
        item.model_copy(update={"connected": item.slug in active, "connection_id": active.get(item.slug)})
        for item in items
    ]


async def get_toolkit(
    db: AsyncSession,
    vault: Vault,
    factory: AdapterFactory,
    cache: CatalogCache,
    ctx: WorkspaceContext,
    slug: str,
    *,
    refresh: bool = False,
) -> ToolkitOut:
    """One app with the fields each connect method asks for."""
    adapter, credential = await workspace_adapter(db, vault, factory, ctx.workspace_id)
    raw = await _cached(
        cache, _cache_key(credential, "toolkit", slug), lambda: adapter.get_toolkit(slug), refresh
    )
    connections = await list_connection_records(db, vault, ctx.workspace_id)
    return _mark_connected([toolkit_out(raw, detail=True)], connections)[0]


async def list_categories(
    db: AsyncSession,
    vault: Vault,
    factory: AdapterFactory,
    cache: CatalogCache,
    ctx: WorkspaceContext,
    *,
    refresh: bool = False,
) -> ToolProviderCategoryPage:
    """Every toolkit category Composio knows (``GET /toolkits/categories``), for the
    gallery's category filter — the complete list, not just the categories seen among
    whatever toolkit page happens to be loaded. Composio's own pages are followed and
    flattened into one list (capped at :data:`CATEGORY_PAGE_CAP` vendor pages) and the
    result is cached as a single entry, same TTL as the toolkit catalogue: the category
    list is small and changes rarely, so paging it further to the console gains nothing.

    Composio's categories endpoint carries no per-category counts, so none are
    synthesised here — a count derived from whatever toolkit page happens to be
    loaded client-side would misrepresent the true, complete count.
    """
    adapter, credential = await workspace_adapter(db, vault, factory, ctx.workspace_id)
    key = _cache_key(credential, "categories")
    if not refresh:
        hit = cache.get(key)
        if hit is not None:
            return ToolProviderCategoryPage.model_validate(hit)
    raw_items: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(CATEGORY_PAGE_CAP):
        try:
            page = await adapter.list_toolkit_categories(cursor=cursor, limit=100)
        except ToolProviderError as exc:
            raise api_error(exc) from exc
        raw_items.extend(_dict(item) for item in _list(page.get("items")))
        cursor = _str(page.get("next_cursor")) or None
        if not cursor:
            break
    valid_items = [item for item in raw_items if item.get("id") or item.get("name")]
    out = ToolProviderCategoryPage(items=[category_out(item) for item in valid_items])
    cache.put(key, out.model_dump(mode="json"))
    return out


async def list_actions(
    db: AsyncSession,
    vault: Vault,
    factory: AdapterFactory,
    cache: CatalogCache,
    ctx: WorkspaceContext,
    slug: str,
    *,
    query: str | None,
    important: bool,
    cursor: str | None,
    limit: int,
    refresh: bool = False,
) -> AppActionPage:
    """A page of one app's actions, with a risk label on each."""
    adapter, credential = await workspace_adapter(db, vault, factory, ctx.workspace_id)
    key = _cache_key(credential, "tools", slug, query or "", important, cursor or "", limit)
    page = await _cached(
        cache,
        key,
        lambda: adapter.list_tools(
            toolkit=slug, search=query, important=important, cursor=cursor, limit=limit
        ),
        refresh,
    )
    items = [action_out(_dict(item)) for item in _list(page.get("items"))]
    return AppActionPage(
        items=[item for item in items if item.slug],
        next_cursor=_str(page.get("next_cursor")) or None,
        total=_int(page.get("total_items")),
    )


def _tools_fetch(adapter: ToolProviderAdapter, toolkit: str, cursor: str | None) -> Callable[[], Any]:
    async def fetch() -> dict[str, Any]:
        return await adapter.list_tools(toolkit=toolkit, cursor=cursor, limit=DESTRUCTIVE_SCAN_LIMIT)

    return fetch


async def destructive_actions(
    adapter: ToolProviderAdapter, credential: Credential, cache: CatalogCache, toolkit: str
) -> list[str]:
    """Every destructive action of one app in the vendor catalogue (R-V5-9), upper-cased and sorted.

    Walks the app's action pages with the catalogue's risk rule (``action_risk``), up to
    :data:`DESTRUCTIVE_SCAN_PAGES` pages, through the same cache entries as the console's
    Actions list.

    Raises:
        ApiError: Composio refused or failed a page (the caller must not provision a tool
            finder whose destructive actions it could not list).
    """
    found: set[str] = set()
    cursor: str | None = None
    for _ in range(DESTRUCTIVE_SCAN_PAGES):
        key = _cache_key(credential, "tools", toolkit, "", False, cursor or "", DESTRUCTIVE_SCAN_LIMIT)
        page = await _cached(cache, key, _tools_fetch(adapter, toolkit, cursor), refresh=False)
        for raw in _list(page.get("items")):
            item = _dict(raw)
            slug = _str(item.get("slug"))
            tags = [str(t) for t in _list(item.get("tags")) if isinstance(t, str)]
            if slug and action_risk(slug, tags) == "destructive":
                found.add(slug.upper())
        cursor = _str(page.get("next_cursor")) or None
        if cursor is None:
            break
    else:
        log.warning("apps_destructive_scan_truncated", toolkit=toolkit, pages=DESTRUCTIVE_SCAN_PAGES)
    return sorted(found)


# ============================================================================ connections
async def _connection_rows(db: AsyncSession, workspace_id: str) -> list[Credential]:
    return list(
        (
            await db.execute(
                select(Credential)
                .where(
                    Credential.workspace_id == workspace_id, Credential.provider_id == TOOL_PROVIDER_ACCOUNT
                )
                .order_by(Credential.created_at.asc())
            )
        )
        .scalars()
        .all()
    )


async def list_connection_records(db: AsyncSession, vault: Vault, workspace_id: str) -> list[AppConnection]:
    """Every connected-app row of a workspace, decrypted."""
    return [AppConnection.from_row(row, vault) for row in await _connection_rows(db, workspace_id)]


async def load_connection(
    db: AsyncSession, vault: Vault, workspace_id: str, connection_id: str
) -> AppConnection:
    """One connection of the workspace, or 404."""
    row = await db.scalar(
        select(Credential).where(
            Credential.id == connection_id,
            Credential.workspace_id == workspace_id,
            Credential.provider_id == TOOL_PROVIDER_ACCOUNT,
        )
    )
    if row is None:
        raise NotFoundError(f"unknown app connection '{connection_id}'")
    return AppConnection.from_row(row, vault)


def _definition(tool: Tool) -> dict[str, Any]:
    return tool.definition if isinstance(tool.definition, dict) else {}


async def bound_tools(
    db: AsyncSession,
    workspace_id: str,
    *,
    credential_ids: Iterable[str] = (),
    connection_ids: Iterable[str] = (),
) -> list[Tool]:
    """Tools whose definition binds one of these Composio keys or app connections.

    That is an MCP tool pointed at Composio with the key bound (the app server or
    tool finder) and every ``provider`` tool (key and ``connection_id``).
    """
    keys, conns = set(credential_ids), set(connection_ids)
    if not keys and not conns:
        return []
    rows = (await db.execute(select(Tool).where(Tool.workspace_id == workspace_id))).scalars().all()
    return [
        tool
        for tool in rows
        if _definition(tool).get("credential_id") in keys or _definition(tool).get("connection_id") in conns
    ]


async def _agents_using(db: AsyncSession, workspace_id: str, tools: list[Tool]) -> int:
    if not tools:
        return 0
    ids = {tool.id for tool in tools}
    agents: set[str] = {tool.agent_id for tool in tools if tool.agent_id}
    for agent in (await db.execute(select(Agent).where(Agent.workspace_id == workspace_id))).scalars().all():
        config = agent.config if isinstance(agent.config, dict) else {}
        tool_ids = _dict(config.get("tools")).get("tool_ids")
        if isinstance(tool_ids, list) and ids.intersection(t for t in tool_ids if isinstance(t, str)):
            agents.add(agent.id)
    return len(agents)


async def connection_out(db: AsyncSession, workspace_id: str, conn: AppConnection) -> AppConnectionOut:
    """The public view of one connection, with how many agents use it."""
    tools = await bound_tools(db, workspace_id, connection_ids=[conn.id])
    return conn.to_out(await _agents_using(db, workspace_id, tools))


async def list_connections(db: AsyncSession, vault: Vault, ctx: WorkspaceContext) -> AppConnectionPage:
    """Every connected app of the workspace (no vendor call)."""
    records = await list_connection_records(db, vault, ctx.workspace_id)
    items = [await connection_out(db, ctx.workspace_id, conn) for conn in records]
    return AppConnectionPage(items=items, total=len(items))


def callback_base(settings: Settings) -> str:
    """The api origin a browser returns to (``LKAP_PUBLIC_BASE_URL``, else the api's own url)."""
    return (settings.public_base_url or settings.worker_callback_base_url).rstrip("/")


def console_base(settings: Settings) -> str:
    """The console origin (``LKAP_WEB_BASE_URL``, else the first web origin)."""
    return (settings.web_base_url or (settings.web_origins[0] if settings.web_origins else "")).rstrip("/")


def console_redirect(settings: Settings, outcome: str) -> str:
    """Where the callback sends the browser: the Apps tab, with ``connect=ok|error`` only."""
    return f"{console_base(settings)}{CONSOLE_APPS_PATH}?{urlencode({'tab': 'apps', 'connect': outcome})}"


async def _subject_for(db: AsyncSession, ctx: WorkspaceContext, payload: AppConnectIn) -> str:
    if payload.subject == "workspace":
        return workspace_subject(ctx.workspace_id)
    if not payload.agent_id:
        raise UnprocessableEntityError("agent_id is required when the connection is for one agent")
    found = await db.scalar(
        select(Agent.id).where(Agent.id == payload.agent_id, Agent.workspace_id == ctx.workspace_id)
    )
    if found is None:
        raise UnprocessableEntityError(f"unknown agent '{payload.agent_id}'")
    return agent_subject(payload.agent_id)


def _audit(
    db: AsyncSession,
    ctx: WorkspaceContext | None,
    action: str,
    target_id: str | None,
    *,
    workspace_id: str | None = None,
    **payload: Any,
) -> None:
    audit.record(
        db,
        workspace_id=ctx.workspace_id if ctx is not None else workspace_id,
        actor_type=ctx.actor.actor_type if ctx is not None else "system",
        actor_id=ctx.actor.id if ctx is not None else None,
        action=action,
        target_type="app_connection",
        target_id=target_id,
        payload=payload,
    )


async def _reusable_auth_config(
    adapter: ToolProviderAdapter,
    records: list[AppConnection],
    *,
    toolkit: str,
    method: ConnectMethod,
    scheme: str | None,
) -> str | None:
    """An auth config already made for (toolkit, method, scheme): ours first, then the project's."""
    for conn in records:
        if (
            conn.toolkit == toolkit
            and conn.method == method
            and conn.auth_config_id
            and (scheme is None or conn.auth_scheme == scheme)
        ):
            return conn.auth_config_id
    listed = await adapter.list_auth_configs(toolkit=toolkit, composio_managed=method == "managed")
    for raw in _list(listed.get("items")):
        item = _dict(raw)
        if _str(item.get("status")).upper() == "DISABLED":
            continue
        if (item.get("is_composio_managed") is True) != (method == "managed"):
            continue
        if scheme is not None and _str(item.get("auth_scheme")).upper() != scheme:
            continue
        if _str(_dict(item.get("toolkit")).get("slug") or toolkit).lower() != toolkit:
            continue
        found = _str(item.get("id"))
        if found:
            return found
    return None


def _key_scheme(toolkit: dict[str, Any]) -> str | None:
    schemes = _schemes(toolkit)
    return next((scheme for scheme in _KEY_SCHEMES if scheme in schemes), None)


async def _start_link(
    adapter: ToolProviderAdapter, settings: Settings, conn: AppConnection, now: dt.datetime
) -> AppConnectOut:
    """Start a hosted sign-in for ``conn`` (a fresh single-use nonce each time)."""
    assert conn.auth_config_id is not None
    nonce = secrets.token_urlsafe(32)
    flow = f"{conn.id}.{nonce}"
    callback_url = (
        f"{callback_base(settings)}/v1/tool-providers/composio/callback?{urlencode({'flow': flow})}"
    )
    link = await adapter.start_link(
        auth_config_id=conn.auth_config_id, subject=conn.subject, callback_url=callback_url
    )
    account_id = _str(link.get("connected_account_id"))
    redirect_url = _str(link.get("redirect_url"))
    if not account_id or not redirect_url.startswith("https://"):
        raise ToolProviderApiError("Composio did not return a sign-in link")
    if conn.connected_account_id and conn.connected_account_id != account_id:
        conn.previous_account_id = conn.connected_account_id
    conn.connected_account_id = account_id
    conn.status = "initiated"
    conn.nonce_hash = _hash_nonce(nonce)
    vendor_expiry = _parse_dt(link.get("expires_at"))
    conn.flow_expires_at = min(filter(None, [vendor_expiry, now + FLOW_TTL]))
    return AppConnectOut(
        connection_id=conn.id, status="initiated", redirect_url=redirect_url, expires_at=conn.flow_expires_at
    )


async def connect(
    db: AsyncSession,
    vault: Vault,
    factory: AdapterFactory,
    settings: Settings,
    ctx: WorkspaceContext,
    payload: AppConnectIn,
) -> AppConnectOut:
    """Connect an app (D-V5-C4): managed sign-in, your own OAuth app, an API key, or keyless."""
    adapter, _ = await workspace_adapter(db, vault, factory, ctx.workspace_id)
    subject = await _subject_for(db, ctx, payload)
    toolkit_slug = payload.toolkit.lower()
    now = utcnow()
    try:
        toolkit = await adapter.get_toolkit(toolkit_slug)
        options = _auth_options(toolkit)
        records = await list_connection_records(db, vault, ctx.workspace_id)
        conn = AppConnection(
            row=Credential(
                workspace_id=ctx.workspace_id, provider_id=TOOL_PROVIDER_ACCOUNT, label="", ciphertext=b""
            ),
            toolkit=toolkit_slug,
            toolkit_name=scrub_vendor_text(toolkit.get("name") or toolkit_slug, limit=120),
            method=payload.method,
            subject=subject,
        )
        match payload.method:
            case "none":
                if "none" not in options:
                    raise UnprocessableEntityError(
                        f"'{toolkit_slug}' needs sign-in; pick another connect method"
                    )
                conn.status, conn.connected_at = "active", now
                result: AppConnectOut | None = None
            case "managed":
                if "oauth_managed" not in options:
                    raise UnprocessableEntityError(
                        f"Composio has no shared sign-in for '{toolkit_slug}'; use your own OAuth app"
                    )
                conn.managed = True
                conn.auth_config_id = await _reusable_auth_config(
                    adapter, records, toolkit=toolkit_slug, method="managed", scheme=None
                )
                if conn.auth_config_id is None:
                    created = await adapter.create_auth_config(
                        toolkit=toolkit_slug, managed=True, name=f"LKAP {conn.toolkit_name}"[:100]
                    )
                    conn.auth_config_id = _str(_dict(created.get("auth_config")).get("id")) or None
                result = None
            case "custom_oauth":
                if "oauth_custom" not in options:
                    raise UnprocessableEntityError(f"'{toolkit_slug}' does not sign in with OAuth")
                if not payload.fields.get("client_id") or not payload.fields.get("client_secret"):
                    raise UnprocessableEntityError("your own OAuth app needs 'client_id' and 'client_secret'")
                oauth_scheme = next((s for s in _schemes(toolkit) if s in _OAUTH_SCHEMES), "OAUTH2")
                created = await adapter.create_auth_config(
                    toolkit=toolkit_slug,
                    managed=False,
                    auth_scheme=oauth_scheme,
                    credentials=dict(payload.fields),
                    name=f"LKAP {conn.toolkit_name}"[:100],
                )
                conn.auth_config_id = _str(_dict(created.get("auth_config")).get("id")) or None
                conn.auth_scheme = oauth_scheme
                result = None
            case "api_key":
                scheme = _key_scheme(toolkit)
                if scheme is None:
                    raise UnprocessableEntityError(f"'{toolkit_slug}' does not connect with a key")
                if not any(value for value in payload.fields.values()):
                    raise UnprocessableEntityError("connecting with a key needs the key field(s) in 'fields'")
                conn.auth_scheme = scheme
                conn.auth_config_id = await _reusable_auth_config(
                    adapter, records, toolkit=toolkit_slug, method="api_key", scheme=scheme
                )
                if conn.auth_config_id is None:
                    created = await adapter.create_auth_config(
                        toolkit=toolkit_slug,
                        managed=False,
                        auth_scheme=scheme,
                        credentials={},
                        name=f"LKAP {conn.toolkit_name}"[:100],
                    )
                    conn.auth_config_id = _str(_dict(created.get("auth_config")).get("id")) or None
                result = None
        if payload.method in ("managed", "custom_oauth", "api_key") and not conn.auth_config_id:
            raise ToolProviderApiError("Composio did not return an auth config for this app")
        # The row exists before the link so the flow nonce can name it.
        conn.save(vault, checked_at=now)
        db.add(conn.row)
        await db.flush()
        if payload.method in ("managed", "custom_oauth"):
            result = await _start_link(adapter, settings, conn, now)
        elif payload.method == "api_key":
            assert conn.auth_config_id is not None and conn.auth_scheme is not None
            created_account = await adapter.create_with_key(
                auth_config_id=conn.auth_config_id,
                subject=subject,
                auth_scheme=conn.auth_scheme,
                fields=dict(payload.fields),
            )
            conn.connected_account_id = _str(created_account.get("id")) or None
            conn.status = vendor_status(created_account.get("status"))
            if conn.status == "unknown" and conn.connected_account_id:
                conn.status = "active"
            if conn.status == "active":
                conn.connected_at = now
    except ToolProviderError as exc:
        log.info("apps_connect_vendor_error", toolkit=toolkit_slug, method=payload.method, reason=exc.reason)
        raise api_error(redact(exc, payload.fields.values())) from None
    conn.save(vault, checked_at=now)
    await db.flush()
    _audit(
        db, ctx, "apps.connect.start", conn.id, toolkit=toolkit_slug, method=payload.method, subject=subject
    )
    if conn.status == "active":
        _audit(db, ctx, "apps.connect.ok", conn.id, toolkit=toolkit_slug, method=payload.method)
    log.info("apps_connect_started", connection_id=conn.id, toolkit=toolkit_slug, method=payload.method)
    if result is not None:
        return result
    return AppConnectOut(connection_id=conn.id, status=conn.status, redirect_url=None, expires_at=None)


async def reconnect(
    db: AsyncSession,
    vault: Vault,
    factory: AdapterFactory,
    settings: Settings,
    ctx: WorkspaceContext,
    connection_id: str,
    fields: dict[str, str],
) -> AppConnectOut:
    """A new sign-in (or a new key) on the same connection row; its picks and tools stay."""
    conn = await load_connection(db, vault, ctx.workspace_id, connection_id)
    adapter, _ = await workspace_adapter(db, vault, factory, ctx.workspace_id)
    now = utcnow()
    try:
        match conn.method:
            case "none":
                conn.status, conn.needs_reconnect = "active", False
                conn.connected_at = conn.connected_at or now
            case "managed" | "custom_oauth":
                if not conn.auth_config_id:
                    raise UnprocessableEntityError(
                        "this connection has no sign-in to repeat; connect the app again"
                    )
                result = await _start_link(adapter, settings, conn, now)
                conn.save(vault, checked_at=now)
                await db.flush()
                _audit(db, ctx, "apps.reconnect.start", conn.id, toolkit=conn.toolkit, method=conn.method)
                return result
            case "api_key":
                if not conn.auth_config_id or not conn.auth_scheme:
                    raise UnprocessableEntityError("this connection cannot be renewed; connect the app again")
                if not any(value for value in fields.values()):
                    raise UnprocessableEntityError(
                        "reconnecting a key-based app needs the new key in 'fields'"
                    )
                created = await adapter.create_with_key(
                    auth_config_id=conn.auth_config_id,
                    subject=conn.subject,
                    auth_scheme=conn.auth_scheme,
                    fields=dict(fields),
                )
                old = conn.connected_account_id
                conn.connected_account_id = _str(created.get("id")) or None
                conn.status = vendor_status(created.get("status"))
                if conn.status == "unknown" and conn.connected_account_id:
                    conn.status = "active"
                if conn.status == "active":
                    conn.connected_at, conn.needs_reconnect = now, False
                    await _delete_quietly(adapter, old if old != conn.connected_account_id else None)
    except ToolProviderError as exc:
        raise api_error(redact(exc, fields.values())) from None
    conn.save(vault, checked_at=now)
    if conn.status == "active":
        await _set_tools_enabled(db, ctx.workspace_id, [conn.id], enabled=True)
    await db.flush()
    _audit(db, ctx, "apps.reconnect", conn.id, toolkit=conn.toolkit, method=conn.method, status=conn.status)
    return AppConnectOut(connection_id=conn.id, status=conn.status)


async def _delete_quietly(adapter: ToolProviderAdapter, account_id: str | None) -> None:
    if not account_id:
        return
    try:
        await adapter.delete_connection(account_id)
    except ToolProviderError as exc:
        log.info("apps_old_account_delete_failed", reason=exc.reason)


# ============================================================================ callback
@dataclass(frozen=True)
class CallbackOutcome:
    """What the callback decided (the route turns it into a redirect)."""

    ok: bool
    reason: str
    connection_id: str | None = None


def _split_flow(flow: str | None) -> tuple[str, str] | None:
    if not flow or "." not in flow or len(flow) > 200:
        return None
    row_id, _, nonce = flow.partition(".")
    if not row_id or not nonce:
        return None
    return row_id, nonce


async def handle_callback(
    db: AsyncSession,
    vault: Vault,
    factory: AdapterFactory,
    *,
    flow: str | None,
    status: str | None,
    connected_account_id: str | None,
) -> CallbackOutcome:
    """Finish a hosted sign-in (D-V5-C5). Every failure is audited; nothing is trusted alone."""
    parts = _split_flow(flow)
    if parts is None:
        _audit(db, None, "apps.connect.failed", None, reason="malformed_flow")
        return CallbackOutcome(False, "malformed_flow")
    row_id, nonce = parts
    row = await db.scalar(
        select(Credential)
        .where(Credential.id == row_id, Credential.provider_id == TOOL_PROVIDER_ACCOUNT)
        # The callback is unauthenticated: the row itself names the workspace.
        .execution_options(**{CROSS_WORKSPACE_OPTION: True})
    )
    if row is None:
        _audit(db, None, "apps.connect.failed", None, reason="unknown_flow")
        return CallbackOutcome(False, "unknown_flow")
    workspace_id = row.workspace_id
    conn = AppConnection.from_row(row, vault)

    def fail(reason: str, *, consume: bool) -> CallbackOutcome:
        if consume:
            conn.status = "failed"
            conn.nonce_hash, conn.flow_expires_at, conn.needs_reconnect = None, None, True
            conn.save(vault, checked_at=utcnow())
        _audit(db, None, "apps.connect.failed", conn.id, workspace_id=workspace_id, reason=reason)
        log.info("apps_connect_failed", connection_id=conn.id, reason=reason)
        return CallbackOutcome(False, reason, conn.id)

    if not conn.nonce_hash or not hmac.compare_digest(conn.nonce_hash, _hash_nonce(nonce)):
        return fail("bad_nonce", consume=False)
    claimed = await db.execute(
        update(Credential)
        .where(Credential.id == row.id, Credential.last_test_message == "initiated")
        .values(last_test_message="verifying")
        .execution_options(**{CROSS_WORKSPACE_OPTION: True}, synchronize_session=False)
    )
    if cast_rowcount(claimed) != 1 or conn.status != "initiated":
        return fail("already_used", consume=False)
    now = utcnow()
    if conn.flow_expires_at is None or now >= conn.flow_expires_at:
        return fail("expired", consume=True)
    if (status or "").lower() != "success":
        return fail("vendor_status_not_success", consume=True)
    if (
        not connected_account_id
        or not conn.connected_account_id
        # Bytes: compare_digest raises TypeError on a non-ASCII str from the query string.
        or not hmac.compare_digest(
            connected_account_id.encode("utf-8"), conn.connected_account_id.encode("utf-8")
        )
    ):
        return fail("account_mismatch", consume=True)
    try:
        adapter, _ = await workspace_adapter(db, vault, factory, workspace_id, require_enabled=False)
        account = await adapter.get_connection(conn.connected_account_id)
    except (ToolProviderError, ApiError):
        return fail("verify_failed", consume=True)
    if _str(account.get("user_id")) != conn.subject:
        return fail("subject_mismatch", consume=True)
    if vendor_status(account.get("status")) != "active":
        return fail("not_active", consume=True)
    old = conn.previous_account_id
    conn.status, conn.connected_at, conn.needs_reconnect = "active", now, False
    conn.nonce_hash, conn.flow_expires_at, conn.previous_account_id = None, None, None
    conn.save(vault, checked_at=now)
    await _set_tools_enabled(db, workspace_id, [conn.id], enabled=True)
    await _delete_quietly(adapter, old if old and old != conn.connected_account_id else None)
    _audit(db, None, "apps.connect.ok", conn.id, workspace_id=workspace_id, toolkit=conn.toolkit)
    log.info("apps_connected", connection_id=conn.id, toolkit=conn.toolkit)
    return CallbackOutcome(True, "ok", conn.id)


def cast_rowcount(result: Any) -> int:
    """The row count of an ``UPDATE`` result."""
    return int(result.rowcount) if isinstance(result, CursorResult) else int(getattr(result, "rowcount", 0))


# ============================================================================ status
async def refresh_connection(
    db: AsyncSession, vault: Vault, factory: AdapterFactory, ctx: WorkspaceContext, connection_id: str
) -> AppConnectionOut:
    """Re-read one connection's state from Composio (the five vendor states, D-V5-C9)."""
    conn = await load_connection(db, vault, ctx.workspace_id, connection_id)
    now = utcnow()
    if (
        conn.method == "none"
        or not conn.connected_account_id
        or (conn.status == "initiated" and conn.nonce_hash)
    ):
        # Nothing at the vendor to ask about (keyless), or a sign-in still in flight.
        return await connection_out(db, ctx.workspace_id, conn)
    adapter, _ = await workspace_adapter(db, vault, factory, ctx.workspace_id)
    try:
        account = await adapter.get_connection(conn.connected_account_id)
    except ToolProviderNotFoundError:
        conn.status, conn.needs_reconnect = "inactive", True
    except ToolProviderError as exc:
        raise api_error(exc) from exc
    else:
        if _str(account.get("user_id")) and _str(account.get("user_id")) != conn.subject:
            conn.status = "failed"
        else:
            conn.status = vendor_status(account.get("status"))
        conn.needs_reconnect = conn.status in _BROKEN
        if conn.status == "active" and conn.connected_at is None:
            conn.connected_at = now
    conn.save(vault, checked_at=now)
    await db.flush()
    return await connection_out(db, ctx.workspace_id, conn)


async def _set_tools_enabled(
    db: AsyncSession,
    workspace_id: str,
    connection_ids: list[str],
    *,
    enabled: bool,
    credential_ids: Iterable[str] = (),
) -> list[str]:
    tools = await bound_tools(db, workspace_id, credential_ids=credential_ids, connection_ids=connection_ids)
    changed = [tool.id for tool in tools if bool(tool.enabled) != enabled]
    for tool in tools:
        tool.enabled = enabled
    return changed


async def disconnect(
    db: AsyncSession,
    vault: Vault,
    factory: AdapterFactory,
    ctx: WorkspaceContext,
    connection_id: str,
    *,
    purge: bool = False,
) -> AppConnectionOut | None:
    """Disconnect an app: delete it at Composio (best effort) and pause what used it.

    The row stays (``inactive``, needs reconnect) so the picked actions and the
    tools keep their references and Reconnect restores them; ``purge`` deletes
    the row too, refused while a tool still references it.
    """
    conn = await load_connection(db, vault, ctx.workspace_id, connection_id)
    tools = await bound_tools(db, ctx.workspace_id, connection_ids=[conn.id])
    if purge and tools:
        raise ConflictError(
            "tools still use this app; delete them first",
            details={"tools": sorted(tool.name for tool in tools)},
        )
    if conn.connected_account_id:
        try:
            adapter, _ = await workspace_adapter(db, vault, factory, ctx.workspace_id, require_enabled=False)
            await adapter.delete_connection(conn.connected_account_id)
        except ToolProviderNotFoundError:
            pass
        except (ToolProviderError, AppsNotEnabledError) as exc:
            log.info("apps_disconnect_vendor_failed", connection_id=conn.id, error=type(exc).__name__)
    _audit(db, ctx, "apps.disconnect", conn.id, toolkit=conn.toolkit, purge=purge, tools_paused=len(tools))
    if purge:
        await db.delete(conn.row)
        await db.flush()
        return None
    paused = await _set_tools_enabled(db, ctx.workspace_id, [conn.id], enabled=False)
    conn.status, conn.needs_reconnect = "inactive", True
    conn.connected_account_id, conn.previous_account_id, conn.nonce_hash = None, None, None
    conn.save(vault, checked_at=utcnow())
    await db.flush()
    log.info("apps_disconnected", connection_id=conn.id, tools_paused=len(paused))
    return conn.to_out(await _agents_using(db, ctx.workspace_id, tools))


# ============================================================================ picks
async def pick_actions(
    db: AsyncSession,
    vault: Vault,
    factory: AdapterFactory,
    ctx: WorkspaceContext,
    payload: AppActionsPickIn,
) -> AppActionsPickOut:
    """Pick actions of a connected app: store them and turn each into a ``provider`` tool.

    Every slug must be an action of the connection's app; a destructive one
    needs ``allow_destructive`` (D-V5-C7). Each action becomes one tool
    (reused when it exists, :mod:`.materialise`); with ``agent_id`` the tools
    are attached to that agent as a new config version (V5-47).
    """
    conn = await load_connection(db, vault, ctx.workspace_id, payload.connection_id)
    if conn.status != "active":
        raise ConflictError(f"'{conn.toolkit}' is not connected (status {conn.status}); reconnect it first")
    if payload.agent_id is not None:
        found = await db.scalar(
            select(Agent.id).where(Agent.id == payload.agent_id, Agent.workspace_id == ctx.workspace_id)
        )
        if found is None:
            raise UnprocessableEntityError(f"unknown agent '{payload.agent_id}'")
        if conn.agent_id is not None and conn.agent_id != payload.agent_id:
            raise UnprocessableEntityError("this app is connected for another agent only")
    wanted = list(dict.fromkeys(slug.strip().upper() for slug in payload.actions if slug.strip()))
    adapter, key = await workspace_adapter(db, vault, factory, ctx.workspace_id)
    try:
        listed = await adapter.list_tools(toolkit=conn.toolkit, tool_slugs=wanted, limit=max(len(wanted), 1))
    except ToolProviderError as exc:
        raise api_error(exc) from exc
    known = {
        action.slug.upper(): action for action in (action_out(_dict(i)) for i in _list(listed.get("items")))
    }
    unknown = [slug for slug in wanted if slug not in known]
    if unknown:
        raise UnprocessableEntityError(
            f"not actions of '{conn.toolkit}': {', '.join(unknown)}", details={"unknown": unknown}
        )
    destructive = [slug for slug in wanted if known[slug].risk == "destructive"]
    if destructive and not payload.allow_destructive:
        raise UnprocessableEntityError(
            "these actions delete, remove or move money; confirm with allow_destructive=true: "
            + ", ".join(destructive),
            details={"destructive": destructive},
        )
    conn.picked_actions = list(dict.fromkeys([*conn.picked_actions, *wanted]))
    conn.save(vault)
    await db.flush()
    made = await materialise.materialise_actions(
        db,
        ctx,
        actions=[known[slug] for slug in wanted],
        toolkit=conn.toolkit,
        connection_id=conn.id,
        subject=conn.subject,
        connection_agent_id=conn.agent_id,
        key=key,
    )
    if payload.agent_id is not None:
        await materialise.attach_tools(
            db, ctx, payload.agent_id, made.tool_ids, note=f"attached {conn.toolkit} actions"
        )
    _audit(
        db,
        ctx,
        "apps.materialise",
        conn.id,
        toolkit=conn.toolkit,
        actions=wanted,
        agent_id=payload.agent_id,
        destructive=destructive,
        tools_created=len(made.created),
    )
    return AppActionsPickOut(
        connection_id=conn.id,
        picked_actions=conn.picked_actions,
        agent_id=payload.agent_id,
        tools_created=made.created,
        tools_existing=made.existing,
    )


# ============================================================================ enable / status
async def _settings_row(db: AsyncSession, workspace_id: str) -> WorkspaceProvider | None:
    row: WorkspaceProvider | None = await db.scalar(
        select(WorkspaceProvider).where(
            WorkspaceProvider.workspace_id == workspace_id,
            WorkspaceProvider.provider_id == COMPOSIO_PROVIDER_ID,
        )
    )
    return row


async def _composio_key_ids(db: AsyncSession, workspace_id: str) -> list[str]:
    return list(
        (
            await db.execute(
                select(Credential.id).where(
                    Credential.workspace_id == workspace_id, Credential.provider_id == COMPOSIO_PROVIDER_ID
                )
            )
        )
        .scalars()
        .all()
    )


async def status(db: AsyncSession, vault: Vault, ctx: WorkspaceContext) -> AppsStatusOut:
    """The Apps header: the key row, its last test, the connection count, paused tools."""
    state = await key_state(db, ctx.workspace_id)
    records = await list_connection_records(db, vault, ctx.workspace_id)
    paused = 0
    if not state.enabled:
        tools = await bound_tools(
            db,
            ctx.workspace_id,
            credential_ids=await _composio_key_ids(db, ctx.workspace_id),
            connection_ids=[c.id for c in records],
        )
        paused = sum(1 for tool in tools if not tool.enabled)
    cred = state.credential
    return AppsStatusOut(
        enabled=cred is not None and state.enabled,
        credential_id=cred.id if cred else None,
        fingerprint=cred.fingerprint if cred else None,
        last_test_ok=cred.last_test_ok if cred else None,
        last_test_at=cred.last_test_at if cred else None,
        last_test_message=cred.last_test_message if cred else None,
        connections=sum(1 for c in records if c.status == "active"),
        paused_tools=paused,
    )


async def set_enabled(
    db: AsyncSession, vault: Vault, ctx: WorkspaceContext, *, enabled: bool
) -> AppsStatusOut:
    """Turn Apps on or off for the workspace (D-V5-C13).

    Off keeps the key and every connection and switches off the tools that use
    them; on switches them back. Enabling needs a key row.
    """
    state = await key_state(db, ctx.workspace_id)
    if enabled and state.credential is None:
        raise AppsNotEnabledError("add a Composio key before enabling Apps")
    row = await _settings_row(db, ctx.workspace_id)
    if row is None:
        row = WorkspaceProvider(
            workspace_id=ctx.workspace_id, provider_id=COMPOSIO_PROVIDER_ID, enabled=enabled
        )
        db.add(row)
    else:
        row.enabled = enabled
    records = await list_connection_records(db, vault, ctx.workspace_id)
    changed = await _set_tools_enabled(
        db,
        ctx.workspace_id,
        [c.id for c in records if enabled is False or c.status == "active"],
        enabled=enabled,
        credential_ids=await _composio_key_ids(db, ctx.workspace_id),
    )
    await db.flush()
    _audit(
        db,
        ctx,
        "apps.enable" if enabled else "apps.disable",
        state.credential.id if state.credential else None,
        tools_changed=len(changed),
    )
    return await status(db, vault, ctx)


# ============================================================================ sweep
async def expire_stale_connections(db: AsyncSession, vault: Vault, *, now: dt.datetime | None = None) -> int:
    """Mark sign-ins nobody finished within 10 minutes as ``expired`` (all workspaces)."""
    ts = now or utcnow()
    rows = (
        (
            await db.execute(
                select(Credential)
                .where(
                    Credential.provider_id == TOOL_PROVIDER_ACCOUNT,
                    Credential.last_test_message.in_(("initiated", "verifying")),
                )
                # The sweep runs platform-wide, across every workspace.
                .execution_options(**{CROSS_WORKSPACE_OPTION: True})
            )
        )
        .scalars()
        .all()
    )
    expired = 0
    for row in rows:
        conn = AppConnection.from_row(row, vault)
        if conn.flow_expires_at is not None and ts < conn.flow_expires_at:
            continue
        if row.last_test_message == "verifying" and ts - (row.updated_at or ts) < FLOW_TTL:
            continue  # a callback is verifying right now
        conn.status, conn.needs_reconnect = "expired", True
        conn.nonce_hash, conn.flow_expires_at = None, None
        conn.save(vault, checked_at=ts)
        expired += 1
    if expired:
        log.info("apps_stale_connections_expired", count=expired)
    return expired


__all__ = [
    "CATALOG_TTL_S",
    "DESTRUCTIVE_SCAN_LIMIT",
    "DESTRUCTIVE_SCAN_PAGES",
    "FLOW_TTL",
    "KEY_TEST_PER_MIN",
    "CATEGORY_PAGE_CAP",
    "AppConnection",
    "AppsNotEnabledError",
    "CallbackOutcome",
    "CatalogCache",
    "KeyState",
    "ToolProviderApiError",
    "ToolProviderCategoryOut",
    "ToolProviderCategoryPage",
    "ToolProviderKeyRejectedError",
    "action_out",
    "api_error",
    "category_out",
    "connect",
    "console_redirect",
    "destructive_actions",
    "disconnect",
    "expire_stale_connections",
    "get_toolkit",
    "handle_callback",
    "key_state",
    "list_actions",
    "list_categories",
    "list_connections",
    "list_toolkits",
    "pick_actions",
    "reconnect",
    "refresh_connection",
    "set_enabled",
    "status",
    "test_key",
    "toolkit_out",
    "vendor_status",
    "workspace_adapter",
]
