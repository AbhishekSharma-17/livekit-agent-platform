"""Resolve `AgentConfig.qa.model` (or its fallbacks) to a callable `JudgeLLM`.

See `qa/llm_client.py`'s module docstring for the open design question this
works around: only the OpenAI-compatible wire format is implemented, so any
other provider (including the pack default, `livekit-inference-llm`) resolves
to an explicit, honest failure reason instead of a guess.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from lkap_contracts import providers
from lkap_contracts.agent_config import AgentConfig
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api import net_guard
from lkap_api.db.models import Credential
from lkap_api.qa.llm_client import JudgeLLM, OpenAiCompatibleJudgeLLM
from lkap_api.settings import get_settings
from lkap_api.vault import Vault

#: Provider ids whose HTTP wire format is the documented OpenAI chat-completions
#: shape, and their default base URL when a `ProviderRef` does not override one.
_OPENAI_COMPATIBLE_DEFAULT_BASE_URL: dict[str, str] = {
    "openai-llm": "https://api.openai.com/v1",
    # V4-03 (OPENROUTER.md D-V4-12): OpenRouter's chat completions are OpenAI-shaped.
    "openrouter-llm": providers.OPENROUTER_BASE_URL,
}


@dataclass(slots=True, frozen=True)
class JudgeResolution:
    """A resolved, callable judge and the label persisted to `session_qa.model`."""

    llm: JudgeLLM
    model_label: str


async def resolve_judge(
    session: AsyncSession, vault: Vault, http: httpx.AsyncClient, config: AgentConfig, *, workspace_id: str
) -> tuple[JudgeResolution | None, str | None]:
    """Resolve the judge model for `config` (`qa.model → pipeline.workflow_llm → pipeline.llm`).

    The judge's credential is looked up in the agent's own workspace only
    (`workspace_id`); an id from another workspace resolves as unknown.

    Returns:
        `(resolution, None)` on success, or `(None, reason)` when no supported
        judge model can be resolved — never raises.
    """
    ref = config.qa.model or config.pipeline.workflow_llm or config.pipeline.llm
    if ref is None:
        return None, "no judge model configured (qa.model / pipeline.workflow_llm / pipeline.llm all unset)"

    try:
        spec = providers.get(ref.provider_id)
    except KeyError:
        return None, f"unknown provider id: {ref.provider_id}"

    if ref.provider_id not in _OPENAI_COMPATIBLE_DEFAULT_BASE_URL:
        return (
            None,
            f"unsupported judge provider: {ref.provider_id} (no documented api-process HTTP endpoint)",
        )
    if not spec.requires_credential:
        return None, (
            f"unsupported judge provider: {ref.provider_id} "
            "(requires no credential; no HTTP endpoint documented for the api process)"
        )
    if not ref.credential_id:
        return None, f"provider {ref.provider_id} requires a credential_id on qa.model/pipeline.workflow_llm"

    credential = (
        await session.execute(
            select(Credential).where(
                Credential.id == ref.credential_id, Credential.workspace_id == workspace_id
            )
        )
    ).scalar_one_or_none()
    if credential is None:
        return None, f"unknown credential_id: {ref.credential_id}"

    secrets = vault.decrypt(credential.ciphertext)
    api_key = secrets.get("api_key") or next(iter(secrets.values()), "")
    base_url = str(ref.fields.get("base_url") or _OPENAI_COMPATIBLE_DEFAULT_BASE_URL[ref.provider_id])
    blocked = net_guard.check_url(base_url, net_guard.policy_from_settings(get_settings()))
    if blocked is not None:
        # V2-21: the judge call carries the credential's key; the guarded client
        # re-checks after DNS, this is the early, readable refusal.
        return None, f"judge base_url refused: {blocked}"
    model = ref.model or spec.default_model
    if not model:
        return None, f"provider {ref.provider_id} has no model resolved (no ref.model, no default_model)"

    llm = OpenAiCompatibleJudgeLLM(http, base_url=base_url, api_key=str(api_key), model=model)
    return JudgeResolution(llm=llm, model_label=f"{ref.provider_id}/{model}"), None
