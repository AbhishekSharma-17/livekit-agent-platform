"""Avatar probes: session-less only — a GET of the id or list membership, never a session (D-V4-26).

Starting an avatar session costs money and needs a room, so these probes
only prove the vendor knows the id with this key. Payload shapes beyond the
status code are UNVERIFIED (``docs/research-v2/livekit-plugins-catalog.md``
§2.2), so membership reuses the catalog package's lenient item parser.
"""

from __future__ import annotations

import time
from urllib.parse import quote

from lkap_contracts.api_models import ProbeResult

from lkap_api.catalogs.base import parse_items
from lkap_api.custom_models.probes.base import ProbeContext, ProbeOutcome, elapsed_ms, failure, json_body

BEY_AVATAR_URL = "https://api.bey.dev/v1/avatars/{id}"
TAVUS_REPLICA_URL = "https://tavusapi.com/v2/replicas/{id}"
ANAM_AVATAR_URL = "https://api.anam.ai/v1/avatars/{id}"
SIMLI_FACES_URL = "https://api.simli.ai/faces"


def _passed(latency_ms: int, message: str) -> ProbeOutcome:
    return ProbeOutcome(
        ok=True,
        results=[ProbeResult(name="basic", ok=True, latency_ms=latency_ms, message=message)],
        message=message,
    )


class _GetByIdProbe:
    """``GET <url with the id>``: 2xx passes, anything else fails with the vendor's reason."""

    name = "avatar_get"
    url_template = ""

    def headers(self, ctx: ProbeContext) -> dict[str, str]:
        raise NotImplementedError

    async def run(self, ctx: ProbeContext) -> ProbeOutcome:
        """Read the one id; never create a session."""
        start = time.perf_counter()
        response = await ctx.client.get(
            self.url_template.format(id=quote(ctx.model, safe="")), headers=self.headers(ctx)
        )
        latency = elapsed_ms(start)
        if not response.is_success:
            return ProbeOutcome(ok=False, results=[failure("basic", response, latency)])
        return _passed(latency, "the vendor knows this id")


class BeyAvatarProbe(_GetByIdProbe):
    """``GET https://api.bey.dev/v1/avatars/{id}`` (``x-api-key``)."""

    name = "bey_avatar_get"
    url_template = BEY_AVATAR_URL

    def headers(self, ctx: ProbeContext) -> dict[str, str]:
        return {"x-api-key": ctx.api_key}


class TavusReplicaProbe(_GetByIdProbe):
    """``GET https://tavusapi.com/v2/replicas/{id}`` (``x-api-key``)."""

    name = "tavus_replica_get"
    url_template = TAVUS_REPLICA_URL

    def headers(self, ctx: ProbeContext) -> dict[str, str]:
        return {"x-api-key": ctx.api_key}


class AnamAvatarProbe(_GetByIdProbe):
    """``GET https://api.anam.ai/v1/avatars/{id}`` (bearer)."""

    name = "anam_avatar_get"
    url_template = ANAM_AVATAR_URL

    def headers(self, ctx: ProbeContext) -> dict[str, str]:
        return {"Authorization": f"Bearer {ctx.api_key}"}


class SimliFaceMemberProbe:
    """``GET https://api.simli.ai/faces`` and look for the id in the list."""

    name = "simli_face_member"

    async def run(self, ctx: ProbeContext) -> ProbeOutcome:
        """Pass when the key's face list contains the id."""
        start = time.perf_counter()
        response = await ctx.client.get(SIMLI_FACES_URL, headers={"x-simli-api-key": ctx.api_key})
        latency = elapsed_ms(start)
        if not response.is_success:
            return ProbeOutcome(ok=False, results=[failure("basic", response, latency)])
        ids = {item.id for item in parse_items(json_body(response))}
        if ctx.model in ids:
            return _passed(latency, "the id is in this key's face list")
        message = f"the id is not in this key's face list ({len(ids)} faces listed)"
        return ProbeOutcome(
            ok=False,
            results=[ProbeResult(name="basic", ok=False, latency_ms=latency, message=message)],
            message=message,
        )


__all__ = [
    "ANAM_AVATAR_URL",
    "BEY_AVATAR_URL",
    "SIMLI_FACES_URL",
    "TAVUS_REPLICA_URL",
    "AnamAvatarProbe",
    "BeyAvatarProbe",
    "SimliFaceMemberProbe",
    "TavusReplicaProbe",
]
