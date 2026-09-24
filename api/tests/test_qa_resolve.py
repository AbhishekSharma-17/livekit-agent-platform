"""`lkap_api.qa.resolve.resolve_judge`: the api-process QA judge on OpenRouter (V4-03, D-V4-12).

Offline: the chat-completions call is mocked with `respx`; no real key is used.
"""

from __future__ import annotations

import httpx
import respx
from conftest import inference_config
from lkap_contracts.agent_config import ProviderRef

from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import Credential
from lkap_api.db.session import Database
from lkap_api.qa.resolve import resolve_judge
from lkap_api.settings import Settings
from lkap_api.vault import Vault


async def _store(database: Database, vault: Vault, *, credential_id: str, provider_id: str) -> None:
    async with database.session() as session:
        session.add(
            Credential(
                id=credential_id,
                workspace_id=DEFAULT_WORKSPACE_ID,
                provider_id=provider_id,
                label=provider_id,
                ciphertext=vault.encrypt({"api_key": "sk-or-v1-judge"}),
                fingerprint="...udge",
            )
        )


async def test_an_openrouter_judge_posts_to_openrouter_chat_completions(
    settings: Settings, database: Database
) -> None:
    vault = Vault(settings.master_key)
    await _store(database, vault, credential_id="cred-or-qa", provider_id="openrouter-llm")
    config = inference_config()
    config.qa.enabled = True
    config.qa.model = ProviderRef(provider_id="openrouter-llm", credential_id="cred-or-qa")

    with respx.mock:
        route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": '{"score": 7}'}}]})
        )
        async with httpx.AsyncClient() as http, database.session() as session:
            resolution, reason = await resolve_judge(
                session, vault, http, config, workspace_id=DEFAULT_WORKSPACE_ID
            )
            assert reason is None
            assert resolution is not None
            text = await resolution.llm.complete(system="Score it.", user="transcript")

    assert text == '{"score": 7}'
    assert resolution.model_label == "openrouter-llm/openai/gpt-4.1-mini"
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer sk-or-v1-judge"
    assert b'"model":"openai/gpt-4.1-mini"' in request.content.replace(b" ", b"")


async def test_an_openrouter_judge_keeps_its_explicit_model(settings: Settings, database: Database) -> None:
    vault = Vault(settings.master_key)
    await _store(database, vault, credential_id="cred-or-qa2", provider_id="openrouter-llm")
    config = inference_config()
    config.qa.model = ProviderRef(
        provider_id="openrouter-llm", credential_id="cred-or-qa2", model="google/gemini-3.5-flash"
    )

    async with httpx.AsyncClient() as http, database.session() as session:
        resolution, reason = await resolve_judge(
            session, vault, http, config, workspace_id=DEFAULT_WORKSPACE_ID
        )

    assert reason is None
    assert resolution is not None
    assert resolution.model_label == "openrouter-llm/google/gemini-3.5-flash"
