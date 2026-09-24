"""Vendor catalog adapters: list a provider's models/voices/avatars/personas.

Every adapter fetches one vendor's catalog with one or a few cheap GET calls
(D-V2-9). Response shapes for several avatar vendors are UNVERIFIED against a
real key (``docs/research-v2/livekit-plugins-catalog.md`` §2.2 marks the
endpoint and auth as confirmed, not the payload), so :func:`parse_items` is
deliberately lenient: it accepts a bare list or any of several common envelope
keys, and reads an id/label from any of several common field names, stashing
the whole raw item in ``CatalogItem.meta`` so a console can still render
*something* (and WP-4 a preview image) even when a guess about the exact key
name is wrong.

Credential tests (:mod:`lkap_api.credential_tests`) reuse these adapters: a
"test" *is* "fetch the catalog with a short timeout and report how many items
came back" — there is one adapter tree, not two (``ProviderSpec.test ==
ProviderSpec.catalog.adapter`` for every entry that has both). A test fetches
page one only (``first_page_only=True``).

V4-07 (docs/v4/CUSTOM-MODELS.md D-V4-25): an adapter declares ``public`` when
its vendor list answers without a key (Deepgram, Rime); the catalog service then
fetches it with an empty secret bag. ``fetch`` follows the vendor's pages per
the registry's :class:`~lkap_contracts.providers.PageSpec` (10 s per request, a
30 s budget per fetch, ``max_pages`` at most); page one failing raises, a later
page failing keeps what was read.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from lkap_contracts.api_models import CatalogItem
from lkap_contracts.providers import CatalogKind, PageSpec, ProviderSpec

from lkap_api.logging import get_logger

log = get_logger(__name__)

#: Per-request timeout for every vendor call (CONTRACTS-V2 D-V2-9: "10 s timeout").
DEFAULT_TIMEOUT_S = 10.0

#: Wall-clock budget for one paged fetch (D-V4-25).
TOTAL_BUDGET_S = 30.0

#: How many items a credential test echoes back as `CredentialTestResult.catalog_preview`.
PREVIEW_ITEMS = 5

#: Envelope keys tried, in order, when a vendor wraps its list in an object.
_ENVELOPE_KEYS: tuple[str, ...] = (
    "data",
    "items",
    "voices",
    "voices_page",
    "avatars",
    "faces",
    "pals",
    "personas",
    "models",
    "presenters",
    "results",
)

#: Item id keys tried, in order, when a vendor's item is an object.
_ID_KEYS: tuple[str, ...] = (
    "id",
    "voice_id",
    "avatar_id",
    "face_id",
    "pal_id",
    "persona_id",
    "presenter_id",
    "avatarId",
    "model",
    "name",
)

#: Item label keys tried, in order, before falling back to the id.
_LABEL_KEYS: tuple[str, ...] = (
    "name",
    "label",
    "display_name",
    "voice_name",
    "face_name",
    "pal_name",
    "title",
)


class CatalogAdapterError(Exception):
    """A vendor catalog call failed (network error, timeout or non-2xx status)."""


class UnsupportedCatalogKind(CatalogAdapterError):
    """The adapter has no endpoint for the requested :class:`CatalogKind`."""


class VendorStatusError(CatalogAdapterError):
    """The vendor answered with a non-2xx status (the message stays secret-free)."""

    def __init__(self, vendor: str, status_code: int) -> None:
        super().__init__(f"{vendor} request failed: HTTPStatusError")
        self.status_code = status_code


class CatalogAdapter(Protocol):
    """Fetches one vendor's catalog for one :class:`CatalogKind`."""

    @property
    def public(self) -> bool:
        """Whether the vendor list answers without a key (fetched with an empty secret bag)."""
        ...

    async def fetch(
        self,
        *,
        client: httpx.AsyncClient,
        secrets: Mapping[str, str],
        kind: CatalogKind,
        page: PageSpec | None = None,
        first_page_only: bool = False,
    ) -> list[CatalogItem]:
        """Return the vendor's items for ``kind``.

        Args:
            client: The shared outbound client (``HttpClientDep`` in routes;
                tests override it with an offline ``httpx.MockTransport``).
            secrets: The credential's decrypted secret bag, normalised so
                ``secrets["api_key"]`` always holds the primary secret value
                regardless of the registry field's real (possibly dotted)
                name — see :func:`normalize_secrets`. Empty for a public list.
            kind: Which list to fetch; vendors offering more than one
                (Tavus faces + pals, Anam avatars + personas) key their
                ``endpoints`` mapping by both.
            page: How to follow the vendor's pages (``CatalogSpec.page``);
                ``None`` reads one page.
            first_page_only: Read page one only (credential tests).

        Raises:
            UnsupportedCatalogKind: If the vendor has no endpoint for ``kind``.
            CatalogAdapterError: If the first request fails or times out.
        """
        ...


def normalize_secrets(spec: ProviderSpec, secrets: Mapping[str, str]) -> dict[str, str]:
    """Return ``secrets`` with an ``"api_key"`` alias for the primary secret field.

    Most registry entries already name their one secret field ``api_key``, but
    a few don't (Azure's ``speech_key``, Simli's nested ``simli_config.api_key``).
    Adapters read ``secrets["api_key"]`` unconditionally so one vendor's
    unusual field name never has to leak into its adapter's auth logic.

    Args:
        spec: The provider whose first `secret_fields` entry is primary.
        secrets: The raw decrypted secret bag (keyed by the registry's field
            names, dotted paths included).

    Returns:
        A copy of ``secrets`` plus (or overwriting) an ``"api_key"`` entry.
    """
    out = dict(secrets)
    primary = spec.secret_fields[0].name if spec.secret_fields else None
    if primary is not None:
        out["api_key"] = secrets.get(primary, "")
    elif "api_key" not in out and secrets:
        out["api_key"] = next(iter(secrets.values()))
    return out


def extract_list(payload: Any) -> list[Any]:
    """Pull the list of raw items out of a vendor response body."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in _ENVELOPE_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def dig(payload: Any, dotted: str) -> Any:
    """The value at a dotted path of a JSON body, or ``None``."""
    node = payload
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _first_present(item: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = item.get(key)
        if value is None or value == "":
            continue
        return str(value)
    return None


def parse_items(
    payload: Any,
    *,
    id_keys: tuple[str, ...] = (),
    label_keys: tuple[str, ...] = (),
) -> list[CatalogItem]:
    """Turn a vendor's (unverified-shape) JSON body into `CatalogItem`s.

    Args:
        payload: The parsed JSON response body.
        id_keys: Extra id field names to try before the common ones.
        label_keys: Extra label field names to try before the common ones.

    Returns:
        One :class:`CatalogItem` per raw entry found; entries this function
        cannot make sense of are silently skipped rather than raised on —
        a partially-parsed catalog is more useful than a broken picker.
    """
    items: list[CatalogItem] = []
    for raw in extract_list(payload):
        if isinstance(raw, str):
            items.append(CatalogItem(id=raw, label=raw))
            continue
        if not isinstance(raw, dict):
            continue
        item_id = _first_present(raw, (*id_keys, *_ID_KEYS))
        if item_id is None:
            continue
        label = _first_present(raw, (*label_keys, *_LABEL_KEYS)) or item_id
        items.append(CatalogItem(id=item_id, label=label, meta=raw))
    return items


AuthBuilder = Callable[[Mapping[str, str]], dict[str, str]]


def bearer_auth(secrets: Mapping[str, str]) -> dict[str, str]:
    """The default auth builder: ``Authorization: Bearer <api_key>``."""
    return {"Authorization": f"Bearer {secrets.get('api_key', '')}"}


def no_auth(_secrets: Mapping[str, str]) -> dict[str, str]:
    """The auth builder of a public list: no headers at all."""
    return {}


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    vendor: str,
    headers: Mapping[str, str],
    params: Mapping[str, str | int] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> Any:
    """One vendor GET: the parsed JSON body, or :class:`CatalogAdapterError` (secret-free)."""
    try:
        # `params=None` keeps a query string already in `url` (httpx replaces it otherwise).
        response = await client.get(
            url, headers=dict(headers), params=dict(params) if params else None, timeout=timeout_s
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        raise VendorStatusError(vendor, exc.response.status_code) from exc
    except httpx.HTTPError as exc:
        raise CatalogAdapterError(f"{vendor} request failed: {type(exc).__name__}") from exc
    except ValueError as exc:  # pragma: no cover - non-JSON vendor body
        raise CatalogAdapterError(f"{vendor} returned a non-JSON body") from exc


async def fetch_pages(
    client: httpx.AsyncClient,
    url: str,
    *,
    vendor: str,
    headers: Mapping[str, str],
    params: Mapping[str, str | int] | None = None,
    page: PageSpec | None = None,
    first_page_only: bool = False,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    budget_s: float = TOTAL_BUDGET_S,
    count: Callable[[Any], int] = lambda body: len(extract_list(body)),
) -> list[Any]:
    """GET ``url`` and follow its pages per ``page``; return every page's JSON body.

    Page one failing raises; a later page failing (or the budget running out)
    stops the loop and keeps the pages already read, because a partial list is
    more useful than none (the V2-06 rule). The loop stops on an empty page,
    on ``more_path`` reading ``false``, on a missing next token, and at
    ``page.max_pages``.
    """
    base_params: dict[str, str | int] = dict(params or {})
    if page is not None and page.size_param and page.size:
        base_params[page.size_param] = page.size
    pages_allowed = 1 if page is None or first_page_only else page.max_pages
    deadline = time.monotonic() + budget_s
    bodies: list[Any] = []
    cursor: str | None = None
    seen = 0
    page_no = 0
    for index in range(pages_allowed):
        request_params = dict(base_params)
        if index > 0 and page is not None:
            match page.kind:
                case "token" | "cursor":
                    request_params[page.param] = cursor or ""
                case "offset":
                    request_params[page.param] = seen
                case "page":
                    request_params[page.param] = page_no
        remaining = deadline - time.monotonic()
        if index > 0 and remaining <= 0:
            log.info("catalog_page_budget_spent", vendor=vendor, pages=index)
            break
        try:
            body = await get_json(
                client,
                url,
                vendor=vendor,
                headers=headers,
                params=request_params,
                timeout_s=min(timeout_s, remaining) if index > 0 else timeout_s,
            )
        except CatalogAdapterError:
            if index == 0:
                raise
            log.warning("catalog_page_failed", vendor=vendor, page=index)
            break
        bodies.append(body)
        if page is None or index + 1 >= pages_allowed:
            break
        n_items = count(body)
        if n_items == 0:
            break
        seen += n_items
        if page.more_path is not None and dig(body, page.more_path) is False:
            break
        match page.kind:
            case "token" | "cursor":
                nxt = dig(body, page.next_path) if page.next_path else None
                if nxt is None or nxt == "":
                    break
                cursor = str(nxt)
            case "offset":
                if page.size and n_items < page.size:
                    break
            case "page":
                page_no += 1
                total = dig(body, page.next_path) if page.next_path else None
                if isinstance(total, int) and page_no >= total:
                    break
    return bodies


@dataclass(frozen=True, slots=True)
class HttpCatalogAdapter:
    """A generic "GET url with vendor auth" adapter (covers most vendors).

    Attributes:
        vendor: Display name, used in `CredentialTestResult.message`.
        endpoints: ``{kind: url}`` — one entry per list the vendor exposes.
        auth: Builds the vendor's auth headers from the normalised secret bag.
        id_keys: Extra id field names specific to this vendor's response shape.
        label_keys: Extra label field names specific to this vendor.
        timeout_s: Per-request timeout.
        params: Fixed query parameters sent on every request (Hume's ``provider``).
        fallback_endpoints: ``{kind: url}`` tried when the primary url answers 404
            (ElevenLabs ``/v2/voices`` → ``/v1/voices``).
        public: The list answers without a key (D-V4-25).
    """

    vendor: str
    endpoints: Mapping[CatalogKind, str]
    auth: AuthBuilder = bearer_auth
    id_keys: tuple[str, ...] = field(default_factory=tuple)
    label_keys: tuple[str, ...] = field(default_factory=tuple)
    timeout_s: float = DEFAULT_TIMEOUT_S
    params: Mapping[str, str] = field(default_factory=dict)
    fallback_endpoints: Mapping[CatalogKind, str] = field(default_factory=dict)
    public: bool = False

    async def fetch(
        self,
        *,
        client: httpx.AsyncClient,
        secrets: Mapping[str, str],
        kind: CatalogKind,
        page: PageSpec | None = None,
        first_page_only: bool = False,
    ) -> list[CatalogItem]:
        """Fetch (following pages) and parse this vendor's list for ``kind``."""
        url = self.endpoints.get(kind)
        if url is None:
            raise UnsupportedCatalogKind(f"{self.vendor} has no '{kind}' catalog")
        headers = self.auth(secrets)
        try:
            bodies = await fetch_pages(
                client,
                url,
                vendor=self.vendor,
                headers=headers,
                params=self.params,
                page=page,
                first_page_only=first_page_only,
                timeout_s=self.timeout_s,
            )
        except VendorStatusError as exc:
            fallback = self.fallback_endpoints.get(kind)
            if exc.status_code != 404 or fallback is None:
                raise
            bodies = await fetch_pages(
                client,
                fallback,
                vendor=self.vendor,
                headers=headers,
                params=self.params,
                timeout_s=self.timeout_s,
            )
        items: list[CatalogItem] = []
        for body in bodies:
            items.extend(parse_items(body, id_keys=self.id_keys, label_keys=self.label_keys))
        return items
