"""Vendor catalog adapters: list a provider's models/voices/avatars/personas.

Every adapter fetches one vendor's catalog with one or two cheap authenticated
GET calls (D-V2-9). Response shapes for several avatar vendors are
UNVERIFIED against a real key (``docs/research-v2/livekit-plugins-catalog.md``
§2.2 marks the endpoint and auth as confirmed, not the payload), so
:func:`parse_items` is deliberately lenient: it accepts a bare list or any of
several common envelope keys, and reads an id/label from any of several
common field names, stashing the whole raw item in ``CatalogItem.meta`` so a
console can still render *something* (and WP-4 a preview image) even when a
guess about the exact key name is wrong.

Credential tests (:mod:`lkap_api.credential_tests`) reuse these adapters: a
"test" *is* "fetch the catalog with a short timeout and report how many items
came back" — there is one adapter tree, not two (``ProviderSpec.test ==
ProviderSpec.catalog.adapter`` for every entry that has both).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from lkap_contracts.api_models import CatalogItem
from lkap_contracts.providers import CatalogKind, ProviderSpec

#: Per-request timeout for every vendor call (CONTRACTS-V2 D-V2-9: "10 s timeout").
DEFAULT_TIMEOUT_S = 10.0

#: How many items a credential test echoes back as `CredentialTestResult.catalog_preview`.
PREVIEW_ITEMS = 5

#: Envelope keys tried, in order, when a vendor wraps its list in an object.
_ENVELOPE_KEYS: tuple[str, ...] = (
    "data",
    "items",
    "voices",
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


class CatalogAdapter(Protocol):
    """Fetches one vendor's catalog for one :class:`CatalogKind`."""

    async def fetch(
        self, *, client: httpx.AsyncClient, secrets: Mapping[str, str], kind: CatalogKind
    ) -> list[CatalogItem]:
        """Return the vendor's items for ``kind``.

        Args:
            client: The shared outbound client (``HttpClientDep`` in routes;
                tests override it with an offline ``httpx.MockTransport``).
            secrets: The credential's decrypted secret bag, normalised so
                ``secrets["api_key"]`` always holds the primary secret value
                regardless of the registry field's real (possibly dotted)
                name — see :func:`normalize_secrets`.
            kind: Which list to fetch; vendors offering more than one
                (Tavus faces + pals, Anam avatars + personas) key their
                ``endpoints`` mapping by both.

        Raises:
            UnsupportedCatalogKind: If the vendor has no endpoint for ``kind``.
            CatalogAdapterError: If the request fails or times out.
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


def _extract_list(payload: Any) -> list[Any]:
    """Pull the list of raw items out of a vendor response body."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in _ENVELOPE_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


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
    for raw in _extract_list(payload):
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


@dataclass(frozen=True, slots=True)
class HttpCatalogAdapter:
    """A generic "GET url with vendor auth" adapter (covers every Phase-1 vendor).

    Attributes:
        vendor: Display name, used in `CredentialTestResult.message`.
        endpoints: ``{kind: url}`` — one entry per list the vendor exposes.
        auth: Builds the vendor's auth headers from the normalised secret bag.
        id_keys: Extra id field names specific to this vendor's response shape.
        label_keys: Extra label field names specific to this vendor.
        timeout_s: Per-request timeout.
    """

    vendor: str
    endpoints: Mapping[CatalogKind, str]
    auth: AuthBuilder = bearer_auth
    id_keys: tuple[str, ...] = field(default_factory=tuple)
    label_keys: tuple[str, ...] = field(default_factory=tuple)
    timeout_s: float = DEFAULT_TIMEOUT_S

    async def fetch(
        self, *, client: httpx.AsyncClient, secrets: Mapping[str, str], kind: CatalogKind
    ) -> list[CatalogItem]:
        """Fetch and parse this vendor's list for ``kind``."""
        url = self.endpoints.get(kind)
        if url is None:
            raise UnsupportedCatalogKind(f"{self.vendor} has no '{kind}' catalog")
        try:
            response = await client.get(url, headers=self.auth(secrets), timeout=self.timeout_s)
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            raise CatalogAdapterError(f"{self.vendor} request failed: {type(exc).__name__}") from exc
        except ValueError as exc:  # pragma: no cover - non-JSON vendor body
            raise CatalogAdapterError(f"{self.vendor} returned a non-JSON body") from exc
        return parse_items(body, id_keys=self.id_keys, label_keys=self.label_keys)
