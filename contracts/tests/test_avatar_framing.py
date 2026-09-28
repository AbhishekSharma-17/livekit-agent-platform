"""V6-26 "Avatar framing" contracts hunk: `AvatarOptions.framing`/`.fit` and
`ProviderCapabilities.avatar_aspect` (PLAN-V6 §3).

The compatibility rule (docs/v6/PLAN-V6.md §0.1): a new config field never
changes what an existing published agent does. Both new `AvatarOptions`
fields default to ``None`` so a stored agent's dict round-trips unchanged;
the *rendering* default they imply (``auto`` + ``contain``, crop-free) is a
web-side concern (`web/src/components/session/avatar-framing.ts`), not
enforced here — this file only pins the wire shape.
"""

import pytest
from pydantic import ValidationError

from lkap_contracts.agent_config import AvatarOptions, PipelineConfig
from lkap_contracts.api_models import AgentAvatarFraming, AgentPublicOut
from lkap_contracts.providers import REGISTRY, ProviderCapabilities


def test_avatar_options_framing_and_fit_default_to_none() -> None:
    """A stored agent from before V6-26 has neither field — the model must not invent one."""
    options = AvatarOptions()
    assert options.framing is None
    assert options.fit is None


def test_avatar_options_v6_26_fields_round_trip() -> None:
    options = AvatarOptions.model_validate({"framing": "portrait", "fit": "cover"})
    assert options.framing == "portrait"
    assert options.fit == "cover"
    assert options.model_dump()["framing"] == "portrait"
    assert options.model_dump()["fit"] == "cover"


def test_avatar_options_rejects_an_unknown_framing_or_fit() -> None:
    with pytest.raises(ValidationError):
        AvatarOptions.model_validate({"framing": "diagonal"})
    with pytest.raises(ValidationError):
        AvatarOptions.model_validate({"fit": "stretch"})


def test_pipeline_config_avatar_options_unset_is_compatible_with_a_pre_v6_26_dict() -> None:
    """A pre-V6-26 stored `PipelineConfig` dict (no `framing`/`fit` keys at all) still validates."""
    pipeline = PipelineConfig.model_validate(
        {"mode": "cascaded", "avatar_options": {"participant_name": "Avatar"}}
    )
    assert pipeline.avatar_options.framing is None
    assert pipeline.avatar_options.fit is None


def test_provider_capabilities_avatar_aspect_defaults_to_none() -> None:
    """Unset (`auto`) is the honest default: most vendors document no fixed resolution."""
    caps = ProviderCapabilities()
    assert caps.avatar_aspect is None
    assert caps.avatar_aspect_note is None


def test_provider_capabilities_rejects_an_unknown_avatar_aspect() -> None:
    with pytest.raises(ValidationError):
        ProviderCapabilities.model_validate({"avatar_aspect": "diagonal"})


def _avatar_entry(provider_id: str) -> ProviderCapabilities:
    for spec in REGISTRY:
        if spec.id == provider_id:
            assert spec.kind == "avatar"
            return spec.capabilities
    raise AssertionError(f"{provider_id} not in REGISTRY")


def test_lemonslice_declares_its_documented_portrait_aspect() -> None:
    """docs.livekit.io/agents/integrations/avatar/lemonslice/: '368x560 pixel videos'."""
    caps = _avatar_entry("lemonslice-avatar")
    assert caps.avatar_aspect == "portrait"
    assert caps.avatar_aspect_note and "368x560" in caps.avatar_aspect_note


def test_anam_declares_its_documented_landscape_default() -> None:
    """anam.ai/docs/personas/session/video: Cara 3/4 default to landscape when unconfigured."""
    caps = _avatar_entry("anam-avatar")
    assert caps.avatar_aspect == "landscape"
    assert caps.avatar_aspect_note


def test_every_other_avatar_entry_has_no_fabricated_aspect() -> None:
    """The other 14 registered avatar vendors have no vendor-documented default resolution/aspect
    found (V6-26's research); `avatar_aspect` must stay unset for them rather than a guess."""
    undocumented = {
        "bey-avatar",
        "tavus-avatar",
        "simli-avatar",
        "bithuman-avatar",
        "liveavatar-avatar",
        "avatario-avatar",
        "avatartalk-avatar",
        "did-avatar",
        "keyframe-avatar",
        "protoface-avatar",
        "runway-avatar",
        "spatius-avatar",
        "synthesia-avatar",
        "trugen-avatar",
    }
    found = {spec.id for spec in REGISTRY if spec.kind == "avatar"}
    assert undocumented <= found, f"missing from REGISTRY: {undocumented - found}"
    for provider_id in undocumented:
        caps = _avatar_entry(provider_id)
        assert caps.avatar_aspect is None, f"{provider_id} should be auto, got {caps.avatar_aspect}"


# --------------------------------------------------------------------------- V6-26b (ask #151)
_PUBLIC_AGENT_BASE: dict[str, object] = {
    "id": "a1",
    "slug": "front-desk",
    "name": "Front desk",
    "description": "",
    "ui_panel_id": "generic",
    "panel": {"panel_id": "generic"},
    "capabilities": {},
    "pipeline_mode": "cascaded",
}


def test_agent_public_out_avatar_framing_defaults_to_none() -> None:
    """A pre-V6-26b payload (no `avatar_framing` key) still validates and means "no hints"."""
    agent = AgentPublicOut.model_validate(_PUBLIC_AGENT_BASE)
    assert agent.avatar_framing is None


def test_agent_public_out_avatar_framing_round_trips() -> None:
    agent = AgentPublicOut.model_validate(
        {
            **_PUBLIC_AGENT_BASE,
            "avatar_framing": {"framing": "portrait", "fit": "cover", "declared_aspect": "portrait"},
        }
    )
    assert agent.avatar_framing == AgentAvatarFraming(
        framing="portrait", fit="cover", declared_aspect="portrait"
    )
    assert AgentPublicOut.model_validate_json(agent.model_dump_json()) == agent


def test_agent_avatar_framing_mirrors_unset_stored_values() -> None:
    """Unset stays unset (`None`), never an invented `auto`/`contain` (PLAN-V6 §0.1)."""
    framing = AgentAvatarFraming()
    assert framing.model_dump() == {"framing": None, "fit": None, "declared_aspect": None}


def test_agent_avatar_framing_carries_display_hints_only() -> None:
    """Public by construction: no provider id, credential or other pipeline config field."""
    assert set(AgentAvatarFraming.model_fields) == {"framing", "fit", "declared_aspect"}


@pytest.mark.parametrize(
    "payload",
    [{"framing": "diagonal"}, {"fit": "stretch"}, {"declared_aspect": "auto"}],
)
def test_agent_avatar_framing_rejects_unknown_values(payload: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        AgentAvatarFraming.model_validate(payload)
