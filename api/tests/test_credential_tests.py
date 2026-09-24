"""`lkap_api.credential_tests`: the vendor-test half of V2-06's shared adapter tree.

`api/src/lkap_api/routers/provider_keys.py` is not wired to this package yet —
see that package's module docstring and `docs/v2/_asks.md` for why (a
permission block in this session's environment). These tests exercise the
package directly, exactly as the router's `POST /v1/credentials/{id}/test`
handler is meant to call it once that patch lands.
"""

from __future__ import annotations

import datetime as dt

import httpx
import pytest
import respx
from lkap_contracts.providers import get

import lkap_api.credential_tests as credential_tests


def test_has_adapter_is_true_only_for_wired_test_names() -> None:
    assert credential_tests.has_adapter(get("bey-avatar")) is True
    assert credential_tests.has_adapter(get("simli-avatar")) is True
    assert credential_tests.has_adapter(get("did-avatar")) is False, "spec.test is unset"
    assert credential_tests.has_adapter(get("azure-tts")) is False, "no adapter registered for azure-tts"


def test_is_cache_fresh() -> None:
    now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)

    assert credential_tests.is_cache_fresh(None, now=now) is False
    assert credential_tests.is_cache_fresh(now - dt.timedelta(minutes=5), now=now) is True
    assert credential_tests.is_cache_fresh(now - dt.timedelta(minutes=11), now=now) is False


def test_cached_result_carries_no_preview() -> None:
    result = credential_tests.cached_result(ok=True, message="cached", checked_at=None)

    assert result.ok is True
    assert result.message == "cached"
    assert result.catalog_preview is None


async def test_run_returns_none_for_a_provider_with_no_adapter() -> None:
    assert await credential_tests.run(get("did-avatar"), {"api_key": "x"}, httpx.AsyncClient()) is None


async def test_run_reports_success_with_a_catalog_preview() -> None:
    spec = get("bey-avatar")

    with respx.mock:
        respx.get("https://api.bey.dev/v1/avatars").mock(
            return_value=httpx.Response(
                200, json=[{"id": f"id-{n}", "name": f"Avatar {n}"} for n in range(8)]
            )
        )
        async with httpx.AsyncClient() as client:
            result = await credential_tests.run(spec, {"api_key": "bey-1"}, client)

    assert result is not None
    assert result.ok is True
    assert "Beyond Presence" in result.message
    assert result.checked_at is not None
    assert result.catalog_preview is not None
    assert len(result.catalog_preview) == 5, "preview is capped, even though 8 items came back"
    assert result.catalog_preview[0].id == "id-0"


async def test_run_uses_simlis_nested_secret_field() -> None:
    spec = get("simli-avatar")

    with respx.mock:
        route = respx.get("https://api.simli.ai/faces").mock(
            return_value=httpx.Response(200, json=[{"face_id": "f1", "face_name": "Aria"}])
        )
        async with httpx.AsyncClient() as client:
            result = await credential_tests.run(spec, {"simli_config.api_key": "simli-1"}, client)

    assert result is not None and result.ok is True
    assert route.calls.last.request.headers["x-simli-api-key"] == "simli-1"


async def test_run_reports_failure_without_raising() -> None:
    spec = get("bey-avatar")

    with respx.mock:
        respx.get("https://api.bey.dev/v1/avatars").mock(return_value=httpx.Response(401))
        async with httpx.AsyncClient() as client:
            result = await credential_tests.run(spec, {"api_key": "bad"}, client)

    assert result is not None
    assert result.ok is False
    assert "failed" in result.message


async def test_run_times_out_within_ten_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hung vendor call must not hang the credential test forever (CONTRACTS-V2 D-V2-9)."""
    import asyncio

    from lkap_api.catalogs import adapters as adapters_module

    class _HangingAdapter:
        async def fetch(self, *, client: object, secrets: object, kind: object, **_: object) -> list[object]:
            await asyncio.sleep(10)
            return []

    spec = get("bey-avatar")
    monkeypatch.setattr(credential_tests, "TIMEOUT_S", 0.01)
    monkeypatch.setitem(adapters_module.ADAPTERS, "bey_avatars", _HangingAdapter())

    async with httpx.AsyncClient() as client:
        result = await credential_tests.run(spec, {"api_key": "k"}, client)

    assert result is not None
    assert result.ok is False
