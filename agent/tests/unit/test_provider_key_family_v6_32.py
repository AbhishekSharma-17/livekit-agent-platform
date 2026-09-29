"""V6-32: one Deepgram key builds the Deepgram voice (offline; the plugin makes no call on build)."""

from __future__ import annotations

import pytest
from lkap_contracts import providers as registry
from lkap_contracts.agent_config import ResolvedProvider

from lkap_agent.providers.factory import ProviderFactory

#: What the api resolves for a `deepgram-tts` slot whose key was added as a `deepgram-stt` key:
#: the key's secret fields become constructor kwargs (`config_service.resolve_provider_ref`).
DEEPGRAM_KEY = "dg-placeholder-key-0001"


def test_the_deepgram_voice_and_transcriber_share_one_key() -> None:
    assert registry.shares_credential("deepgram-stt", "deepgram-tts")
    assert registry.get("deepgram-tts").secret_fields[0].name == "api_key"


def test_a_deepgram_stt_key_reaches_the_deepgram_voice_as_its_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The key must come from the kwargs, never the process environment (factory invariant 1).
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    spec = registry.get("deepgram-tts")
    resolved = ResolvedProvider(
        provider_id=spec.id,
        python_class=spec.python_class,
        model=spec.default_model,
        kwargs={"api_key": DEEPGRAM_KEY},
    )

    built = ProviderFactory().build("tts", resolved)

    assert type(built).__module__.startswith("livekit.plugins.deepgram")
    assert built._opts.api_key == DEEPGRAM_KEY
    assert built.model == spec.default_model
