"""Catalog sightings on `provider_models` rows (V4-07, D-V4-27 (1)(2), R-V4-28).

A successful ``models`` fetch stamps ``catalog_seen_at`` on the workspace's
rows it lists, marks ``catalog_missing_since`` on rows that were in the
**previous fetch of the same entry and filter** and are gone, clears it when
the id comes back, and takes an explicit vendor deprecation date as is.
"""

from __future__ import annotations

import datetime as dt

import httpx
import respx
from lkap_contracts.providers import get
from sqlalchemy import select

from lkap_api.custom_models import records
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import ProviderModel
from lkap_api.db.session import Database

OPENAI_MODELS_URL = "https://api.openai.com/v1/models"
OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


async def _default_key(admin_client: httpx.AsyncClient, provider_id: str) -> None:
    created = await admin_client.post(
        "/v1/credentials",
        json={"provider_id": provider_id, "label": provider_id, "secrets": {"api_key": "placeholder-key"}},
    )
    assert created.status_code == 201, created.text
    home = created.json()["provider_id"]
    await admin_client.put(
        f"/v1/providers/{home}/settings", json={"default_credential_id": created.json()["id"]}
    )


async def _seed(database: Database, provider_id: str, model_id: str) -> None:
    async with database.session() as session:
        await records.upsert(
            session,
            workspace_id=DEFAULT_WORKSPACE_ID,
            spec=get(provider_id),
            model_id=model_id,
            last_test_ok=True,
        )
        await session.commit()


async def _row(database: Database, model_id: str) -> ProviderModel:
    async with database.session() as session:
        row = await session.scalar(
            select(ProviderModel).where(
                ProviderModel.workspace_id == DEFAULT_WORKSPACE_ID, ProviderModel.model_id == model_id
            )
        )
        assert row is not None
        return row


def _openai(*ids: str) -> httpx.Response:
    return httpx.Response(200, json={"data": [{"id": i} for i in ids]})


async def test_a_listed_id_is_seen_then_missing_then_cleared(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    await _default_key(admin_client, "openai-llm")
    await _seed(database, "openai-llm", "gpt-4.1-nano-2026")
    url = "/v1/providers/openai-llm/catalog"

    with respx.mock:
        respx.get(OPENAI_MODELS_URL).mock(return_value=_openai("gpt-4.1", "gpt-4.1-nano-2026"))
        await admin_client.get(url)
    first = await _row(database, "gpt-4.1-nano-2026")
    assert first.catalog_seen_at is not None
    assert first.catalog_missing_since is None

    with respx.mock:
        respx.get(OPENAI_MODELS_URL).mock(return_value=_openai("gpt-4.1"))
        await admin_client.get(url, params={"refresh": "true"})
    gone = await _row(database, "gpt-4.1-nano-2026")
    assert gone.catalog_missing_since is not None, "it was in the previous fetch and is gone now"
    assert gone.catalog_seen_at == first.catalog_seen_at

    with respx.mock:
        respx.get(OPENAI_MODELS_URL).mock(return_value=_openai("gpt-4.1", "gpt-4.1-nano-2026"))
        await admin_client.get(url, params={"refresh": "true"})
    back = await _row(database, "gpt-4.1-nano-2026")
    assert back.catalog_missing_since is None, "the warning clears when the id reappears"


async def test_an_id_never_in_the_previous_list_is_not_marked(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    await _default_key(admin_client, "openai-llm")
    await _seed(database, "openai-llm", "ft:gpt-4.1:org::abc")

    with respx.mock:
        respx.get(OPENAI_MODELS_URL).mock(return_value=_openai("gpt-4.1"))
        await admin_client.get("/v1/providers/openai-llm/catalog")
        await admin_client.get("/v1/providers/openai-llm/catalog", params={"refresh": "true"})

    row = await _row(database, "ft:gpt-4.1:org::abc")
    assert row.catalog_missing_since is None
    assert row.catalog_seen_at is None


async def test_the_same_id_absent_from_a_differently_filtered_list_is_untouched(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    # One OpenRouter key serves five entries with five filtered lists. An LLM row must
    # not be flagged because the STT list (a different filter and kind) lacks it.
    await _default_key(admin_client, "openrouter-llm")
    await _seed(database, "openrouter-llm", "vendor/chat-model")
    stt_lists = [
        {"data": [{"id": "vendor/chat-model"}, {"id": "openai/whisper-1"}]},
        {"data": [{"id": "openai/whisper-1"}]},
    ]
    with respx.mock:
        respx.get(OPENROUTER_KEY_URL).mock(return_value=httpx.Response(200, json={"data": {}}))
        respx.get(url__startswith=OPENROUTER_MODELS_URL).mock(
            side_effect=[httpx.Response(200, json=body) for body in stt_lists]
        )
        await admin_client.get("/v1/providers/openrouter-stt/catalog")
        await admin_client.get("/v1/providers/openrouter-stt/catalog", params={"refresh": "true"})

    row = await _row(database, "vendor/chat-model")
    assert row.catalog_missing_since is None
    assert row.catalog_seen_at is None, "the STT list never speaks for an LLM row"


async def test_an_openai_shutdown_date_sets_the_deprecation_date(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    await _default_key(admin_client, "openai-llm")
    await _seed(database, "openai-llm", "gpt-4o-2024-05-13")
    shutdown = int(dt.datetime(2026, 12, 1, tzinfo=dt.UTC).timestamp())

    with respx.mock:
        respx.get(OPENAI_MODELS_URL).mock(
            return_value=httpx.Response(
                200,
                json={"data": [{"id": "gpt-4o-2024-05-13", "shutdown_date": shutdown}, {"id": "gpt-4.1"}]},
            )
        )
        await admin_client.get("/v1/providers/openai-llm/catalog")

    row = await _row(database, "gpt-4o-2024-05-13")
    assert row.catalog_missing_since == dt.datetime(2026, 12, 1, tzinfo=dt.UTC)
    assert row.catalog_seen_at is not None


def test_deprecation_signals_parse_leniently() -> None:
    now = dt.datetime(2026, 9, 25, tzinfo=dt.UTC)
    assert records.deprecation_date({"shutdown_date": "2026-12-01"}, now) == dt.datetime(
        2026, 12, 1, tzinfo=dt.UTC
    )
    assert records.deprecation_date({"shutdown_date": "soon"}, now) == now
    assert records.deprecation_date({"archived": True}, now) == now
    assert records.deprecation_date({"modelLifecycle": {"status": "LEGACY"}}, now) == now
    assert records.deprecation_date({"modelLifecycle": {"status": "ACTIVE"}}, now) is None
    assert records.deprecation_date({"archived": False, "shutdown_date": None}, now) is None
