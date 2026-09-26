"""Per-connection LiveKit clients and token minting (ARCHITECTURE-V2 D-V2-5).

:class:`ConnectionClientFactory` is the single place a connection's API key and
secret are decrypted for use against LiveKit. Everything that talks to a
LiveKit deployment — the ``connect`` token, the ``test`` probe, webhook
verification, Egress/SIP calls in later packages — goes through it, so a
connection's credentials are never read from process-wide ``LIVEKIT_*``
settings.

Caching: decrypted ``(api_key, api_secret)`` pairs are cached in-process keyed
by ``(connection_id, credentials_version)`` with a short TTL, so a rotation
(which bumps ``credentials_version``) takes effect on the very next call. The
url and agent name are always read from the row, never cached, because a plain
``PUT`` may change them without a version bump. :class:`livekit.api.LiveKitAPI`
owns an ``aiohttp`` session bound to the running event loop, so it is built per
use through :meth:`ConnectionClientFactory.api` and closed on exit rather than
kept in the cache.

Nothing here logs a key, a secret or a minted token.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING, Annotated, Protocol

import aiohttp
from aiohttp.abc import AbstractResolver
from fastapi import Depends
from livekit.api import LiveKitAPI, TokenVerifier
from lkap_contracts.dispatch import DispatchMetadata

from lkap_api import net_guard
from lkap_api.deps import VaultDep
from lkap_api.livekit_tokens import TOKEN_TTL, mint_participant_token
from lkap_api.settings import get_settings
from lkap_api.vault import Vault

if TYPE_CHECKING:
    from lkap_api.telephony.phone_numbers import PhoneNumberClient

#: How long a decrypted key/secret pair stays in the in-process cache.
CREDENTIAL_CACHE_TTL_S = 300.0

#: Upper bound on cached connections; the oldest entry is evicted first.
CREDENTIAL_CACHE_MAX = 128

#: Default per-request timeout for LiveKit server API calls made by the api.
DEFAULT_API_TIMEOUT_S = 10.0


class ConnectionRowLike(Protocol):
    """The columns of ``livekit_connections`` the factory reads.

    A protocol rather than the ORM class so probes can run against a connection
    that has not been saved yet (``POST /v1/connections/test``).
    """

    @property
    def id(self) -> str: ...  # noqa: D102 - protocol member

    @property
    def url(self) -> str: ...  # noqa: D102 - protocol member

    @property
    def agent_name(self) -> str: ...  # noqa: D102 - protocol member

    @property
    def deployment_type(self) -> str: ...  # noqa: D102 - protocol member

    @property
    def credentials_version(self) -> int: ...  # noqa: D102 - protocol member

    @property
    def api_key_ct(self) -> bytes: ...  # noqa: D102 - protocol member

    @property
    def api_secret_ct(self) -> bytes: ...  # noqa: D102 - protocol member


@dataclass(frozen=True, slots=True)
class ConnectionCredentials:
    """Decrypted connection material. **Contains a secret; never log it.**"""

    connection_id: str
    credentials_version: int
    url: str
    agent_name: str
    api_key: str
    api_secret: str = field(repr=False)


@dataclass(slots=True)
class _CacheEntry:
    api_key: str
    api_secret: str = field(repr=False)
    expires_at: float = 0.0


class ConnectionClientFactory:
    """Builds LiveKit API clients, tokens and webhook verifiers per connection."""

    def __init__(
        self,
        vault: Vault,
        *,
        ttl_s: float = CREDENTIAL_CACHE_TTL_S,
        max_entries: int = CREDENTIAL_CACHE_MAX,
        clock: Callable[[], float] = time.monotonic,
        net_policy: net_guard.NetPolicy | None = None,
        net_resolver: Callable[[], AbstractResolver] | None = None,
    ) -> None:
        """Build a factory.

        Args:
            vault: The vault holding ``LKAP_MASTER_KEY``.
            ttl_s: Lifetime of a cached decrypted key/secret pair.
            max_entries: Maximum number of cached connections.
            clock: Monotonic clock, injectable for tests.
            net_policy: The outbound network guard's allowlist; by default read
                from the process settings at each call (``LKAP_NET_ALLOW_PRIVATE_HOSTS``).
                Each call narrows it to the row's ``deployment_type``
                (:meth:`~lkap_api.net_guard.NetPolicy.for_connection`): a self-hosted
                connection may reach loopback, private and CGNAT addresses.
            net_resolver: Builds the name resolver the guard wraps (tests inject a
                fake; by default aiohttp's own).
        """
        self._vault = vault
        self._net_policy = net_policy
        self._net_resolver = net_resolver
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._clock = clock
        self._cache: dict[tuple[str, int], _CacheEntry] = {}

    # ------------------------------------------------------------------ credentials
    def credentials(self, row: ConnectionRowLike) -> ConnectionCredentials:
        """Return the decrypted credentials of a connection.

        Args:
            row: A ``livekit_connections`` row (or an unsaved look-alike).

        Returns:
            The url, agent name and decrypted key/secret. **Contains a secret.**

        Raises:
            lkap_api.vault.VaultError: If the ciphertext cannot be decrypted.
        """
        key = (row.id, row.credentials_version)
        now = self._clock()
        entry = self._cache.get(key)
        if entry is None or entry.expires_at <= now:
            entry = _CacheEntry(
                api_key=self._vault.decrypt(row.api_key_ct)["api_key"],
                api_secret=self._vault.decrypt(row.api_secret_ct)["api_secret"],
                expires_at=now + self._ttl_s,
            )
            self._store(key, entry)
        return ConnectionCredentials(
            connection_id=row.id,
            credentials_version=row.credentials_version,
            url=row.url,
            agent_name=row.agent_name,
            api_key=entry.api_key,
            api_secret=entry.api_secret,
        )

    def _store(self, key: tuple[str, int], entry: _CacheEntry) -> None:
        # Drop every older version of this connection, then bound the cache size.
        for stale in [k for k in self._cache if k[0] == key[0] and k != key]:
            del self._cache[stale]
        self._cache[key] = entry
        while len(self._cache) > self._max_entries:
            del self._cache[next(iter(self._cache))]

    def invalidate(self, connection_id: str) -> None:
        """Forget every cached version of a connection (rotation, delete)."""
        for key in [k for k in self._cache if k[0] == connection_id]:
            del self._cache[key]

    # --------------------------------------------------------------------- clients
    def _policy_for(self, row: ConnectionRowLike) -> net_guard.NetPolicy:
        """The guard policy for this connection: the process allowlist, by deployment type."""
        base = self._net_policy or net_guard.policy_from_settings(get_settings())
        return base.for_connection(row.deployment_type)

    @asynccontextmanager
    async def api(
        self,
        row: ConnectionRowLike,
        *,
        timeout_s: float = DEFAULT_API_TIMEOUT_S,
        failover: bool = True,
    ) -> AsyncIterator[LiveKitAPI]:
        """Yield a :class:`~livekit.api.LiveKitAPI` for one connection, closed on exit.

        Args:
            row: The connection to talk to.
            timeout_s: Total per-request timeout.
            failover: Whether the SDK may retry other Cloud regions on
                transport errors. Probes pass ``False`` so a bad url fails
                within ``timeout_s`` instead of entering region discovery.

        Yields:
            A client authenticated with this connection's key/secret.
        """
        creds = self.credentials(row)
        policy = self._policy_for(row)
        # V2-21 / S1: IP-literal hosts never reach aiohttp's resolver, so they are
        # checked here; names are checked by the guarded resolver at connect time,
        # against the very address the socket then uses (no DNS-rebinding window).
        problem = net_guard.check_url(creds.url, policy, schemes=net_guard.LIVEKIT_SCHEMES)
        if problem is not None:
            raise net_guard.BlockedDestinationError(f"blocked destination: {problem}")
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        session = net_guard.guarded_aiohttp_session(
            policy, timeout=timeout, inner_resolver=self._net_resolver() if self._net_resolver else None
        )
        client = LiveKitAPI(
            url=creds.url,
            api_key=creds.api_key,
            api_secret=creds.api_secret,
            timeout=timeout,
            session=session,
            failover=failover,
        )
        try:
            yield client
        finally:
            await client.aclose()
            await session.close()

    @asynccontextmanager
    async def phone_numbers(
        self, row: ConnectionRowLike, *, timeout_s: float = DEFAULT_API_TIMEOUT_S
    ) -> AsyncIterator[PhoneNumberClient]:
        """Yield a Twirp-JSON ``PhoneNumberService`` client for one connection (V4-05, D-V4-16).

        Same url check and network-guarded session as :meth:`api`; each request
        is signed with this connection's key and ``SIPGrants(admin=True)``.
        """
        # Imported here: the telephony package imports this module.
        from lkap_api.telephony.phone_numbers import PhoneNumberClient, sign

        creds = self.credentials(row)
        policy = self._policy_for(row)
        problem = net_guard.check_url(creds.url, policy, schemes=net_guard.LIVEKIT_SCHEMES)
        if problem is not None:
            raise net_guard.BlockedDestinationError(f"blocked destination: {problem}")
        session = net_guard.guarded_aiohttp_session(
            policy,
            timeout=aiohttp.ClientTimeout(total=timeout_s),
            inner_resolver=self._net_resolver() if self._net_resolver else None,
        )
        try:
            yield PhoneNumberClient(session, creds.url, lambda: sign(creds.api_key, creds.api_secret))
        finally:
            await session.close()

    def token_verifier(self, row: ConnectionRowLike) -> TokenVerifier:
        """Return a verifier for JWTs signed with this connection's secret (webhooks)."""
        creds = self.credentials(row)
        return TokenVerifier(api_key=creds.api_key, api_secret=creds.api_secret)

    # ---------------------------------------------------------------------- tokens
    def mint_participant_token(
        self,
        row: ConnectionRowLike,
        *,
        room_name: str,
        identity: str,
        participant_name: str,
        dispatch: DispatchMetadata,
        attributes: dict[str, str] | None = None,
        ttl: dt.timedelta = TOKEN_TTL,
    ) -> str:
        """Mint a browser participant JWT signed with this connection's key/secret.

        The token dispatches exactly one agent, named ``row.agent_name`` (never
        the process-wide ``LKAP_AGENT_NAME``), so a project shared with other
        agents never receives a job meant for someone else.

        Args:
            row: The connection the agent is bound to.
            room_name: Room the participant may join.
            identity: Participant identity.
            participant_name: Display name.
            dispatch: ID-only dispatch metadata; ``connection_id`` should equal ``row.id``.
            attributes: Optional public participant attributes.
            ttl: Token lifetime.

        Returns:
            The signed JWT. Its ``iss`` claim is the connection's API key.
        """
        creds = self.credentials(row)
        return mint_participant_token(
            api_key=creds.api_key,
            api_secret=creds.api_secret,
            agent_name=creds.agent_name,
            room_name=room_name,
            identity=identity,
            participant_name=participant_name,
            dispatch=dispatch,
            attributes=attributes,
            ttl=ttl,
        )


@lru_cache(maxsize=4)
def _factory_for(vault: Vault) -> ConnectionClientFactory:
    return ConnectionClientFactory(vault)


def get_client_factory(vault: VaultDep) -> ConnectionClientFactory:
    """FastAPI dependency: the process-wide :class:`ConnectionClientFactory`."""
    return _factory_for(vault)


ClientFactoryDep = Annotated[ConnectionClientFactory, Depends(get_client_factory)]
