"""The insurance manifest seeds a vision-capable LLM (DECISIONS-W2 §D-W2-10 step 4)."""

from lkap_contracts.providers import ModelSpec, ProviderSpec, get

from packs.generic.manifest import MANIFEST as GENERIC_MANIFEST
from packs.insurance_claim.manifest import MANIFEST, _vision_model


def test_insurance_manifest_seeds_the_first_vision_model_for_its_camera() -> None:
    llm = MANIFEST.recommended_pipeline.llm
    assert MANIFEST.capabilities.camera is True
    assert llm is not None
    assert llm.provider_id == "livekit-inference-llm"
    assert llm.model == "google/gemini-3.5-flash"


def test_generic_manifest_keeps_the_text_default() -> None:
    llm = GENERIC_MANIFEST.recommended_pipeline.llm
    assert llm is not None
    assert llm.model == get("livekit-inference-llm").default_model == "google/gemma-4-31b-it"


def test_vision_model_falls_back_to_the_default_when_no_model_is_flagged() -> None:
    spec = ProviderSpec(
        id="x-llm",
        kind="llm",
        label="X",
        vendor="X",
        package="x",
        python_class="x.LLM",
        models=[ModelSpec(id="a", label="A"), ModelSpec(id="b", label="B")],
        default_model="a",
    )
    assert _vision_model(spec) == "a"
