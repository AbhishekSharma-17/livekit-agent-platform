"""Cross-check every registry field name against the real plugin constructor.

Most of the ~120 registry entries live in plugin packages this project's
venv does not install (only the 8 v1 MVP packages are present — see
`docs/research-v2/livekit-plugins-catalog.md` §0). This module is the
committed evidence that V2-05 checked their field names against the real
source anyway, using the AST snapshot `scripts/snapshot_plugin_signatures.py`
produced into `agent/tests/fixtures/plugin_signatures.json` from a fresh
clone of `github.com/livekit/agents` at the pinned release tag (now
`livekit-agents@1.8.3`) — not from re-typing the catalog doc's own field
lists unread.

An `available` provider whose `python_class` is not in the fixture **fails**
(R-V4-55): a skip there once hid three OpenAI Realtime checks when upstream
#7318 turned the class into a star re-export. Skips are reserved for
`**kwargs` constructors (any name is forwarded); deferred entries are not in
`_PLUGIN_SPECS` at all. This is still a verification aid, not a live-import
check (that would require installing all ~70 plugin packages, most with
native/heavy dependencies this project does not carry —
`agent/requirements/full.txt` and its own import-check gate are V2-09's job,
CONTRACTS-V2 §7).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from lkap_contracts.providers import ProviderKind, ProviderSpec, available_providers

_FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "plugin_signatures.json"

#: Kinds whose `python_class` names a `livekit.*` plugin class the snapshot
#: can plausibly cover. `image_gen`/`embedding`/`secret_bag` point at internal
#: `lkap_agent`/`lkap_api` classes (or, for `secret_bag`, nothing at all) and
#: are out of scope for this cross-check.
_PLUGIN_KINDS: frozenset[ProviderKind] = frozenset(
    {"realtime", "stt", "llm", "tts", "avatar", "vad", "turn_detection", "noise_cancellation"}
)


def _load_signatures() -> dict[str, dict[str, Any]]:
    if not _FIXTURE_PATH.exists():
        return {}
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


SIGNATURES = _load_signatures()

#: (provider_id, field_name) pairs the factory remaps before construction
#: (`factory.py::_constructor_kwargs`), so the field is real but is not a
#: direct constructor kwarg name — this test checks names reach *some*
#: correct destination, not that every field is a literal top-level kwarg.
_KNOWN_FIELD_REMAPS: frozenset[tuple[str, str]] = frozenset({("livekit-inference-llm", "temperature")})

#: Every `available` entry whose class lives in a `livekit.*` plugin package.
_PLUGIN_SPECS = [
    spec
    for spec in available_providers()
    if spec.kind in _PLUGIN_KINDS and spec.python_class.startswith("livekit.")
]


def _signature_for(spec: ProviderSpec, signatures: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The fixture entry for an available provider's `python_class`; missing is a failure (R-V4-55)."""
    signature = signatures.get(spec.python_class)
    assert signature is not None, (
        f"{spec.id}: {spec.python_class} is not in the AST snapshot; re-run "
        "scripts/snapshot_plugin_signatures.py against the pinned release tag"
    )
    return signature


def test_fixture_file_exists_and_is_non_empty() -> None:
    """Guards against silently skipping every test below if the fixture is missing."""
    assert _FIXTURE_PATH.exists(), (
        "agent/tests/fixtures/plugin_signatures.json is missing — regenerate with "
        "scripts/snapshot_plugin_signatures.py against the pinned livekit-agents release tag"
    )
    assert SIGNATURES, "plugin_signatures.json exists but is empty"


def test_at_least_one_hundred_plugin_signatures_were_captured() -> None:
    assert len(SIGNATURES) >= 100


@pytest.mark.parametrize("spec", _PLUGIN_SPECS, ids=lambda s: s.id)
def test_field_names_are_accepted_by_the_real_constructor(spec: ProviderSpec) -> None:
    signature = _signature_for(spec, SIGNATURES)
    if signature["has_var_keyword"]:
        pytest.skip(f"{spec.python_class} accepts **kwargs; any field name is technically forwarded")

    allowed = set(signature["params"])
    module_path = spec.python_class.rsplit(".", 1)[0]

    for field in (*spec.secret_fields, *spec.fields):
        if field.nested_model:
            nested_key = f"{module_path}.{field.nested_model}"
            nested_signature = SIGNATURES.get(nested_key)
            if nested_signature is None:
                continue  # nested model not captured this pass; do not fail on it
            if "." in field.name:
                inner_name = field.name.split(".", 1)[1]
                assert inner_name in nested_signature["params"], (
                    f"{spec.id}: {field.name!r} not accepted by {nested_key}"
                )
            continue
        if (spec.id, field.name) in _KNOWN_FIELD_REMAPS:
            continue
        outer_name = field.name.split(".", 1)[0]
        assert outer_name in allowed, f"{spec.id}: field {field.name!r} not accepted by {spec.python_class}"


@pytest.mark.parametrize("spec", _PLUGIN_SPECS, ids=lambda s: s.id)
def test_nested_model_classes_resolve_from_the_same_module(spec: ProviderSpec) -> None:
    """Every `nested_model` name must actually import from `python_class`'s own module.

    This is what `special_cases.unwrap_nested_fields` relies on at runtime
    (no separate "where does this class live" lookup); confirming it here
    means a broken nested-model reference fails a fast unit test, not a live
    session.
    """
    module_path = spec.python_class.rsplit(".", 1)[0]
    for field in (*spec.secret_fields, *spec.fields):
        if not field.nested_model:
            continue
        nested_key = f"{module_path}.{field.nested_model}"
        assert nested_key in SIGNATURES, f"{spec.id}: {nested_key} is not in the AST snapshot"
        # Presence in SIGNATURES already proves it resolves under that module;
        # nothing further to assert.


def test_the_five_classmethod_python_classes_are_in_the_fixture() -> None:
    """Spot-check the classmethod-path entries `import_target` must resolve at runtime."""
    classmethod_ids = {
        "azure-openai-realtime": "livekit.plugins.openai.realtime.RealtimeModel.with_azure",
        "silero-vad": "livekit.plugins.silero.VAD.load",
        # V4-03: a @staticmethod (since 1.8.2), captured by name like the classmethods.
        "openrouter-llm": "livekit.plugins.openai.LLM.with_openrouter",
    }
    for provider_id, python_class in classmethod_ids.items():
        spec = next(s for s in available_providers() if s.id == provider_id)
        assert spec.python_class == python_class
        assert python_class in SIGNATURES, f"{python_class} is not in the AST snapshot"


def test_a_missing_available_class_fails_instead_of_skipping() -> None:
    """R-V4-55: a doctored fixture without an available entry's class is a failure, not a skip."""
    spec = next(s for s in _PLUGIN_SPECS if s.id == "openai-realtime")
    doctored = {key: sig for key, sig in SIGNATURES.items() if key != spec.python_class}

    assert _signature_for(spec, SIGNATURES) is SIGNATURES[spec.python_class]
    with pytest.raises(AssertionError, match="not in the AST snapshot"):
        _signature_for(spec, doctored)


def test_the_openai_realtime_star_reexport_keeps_its_public_keys() -> None:
    """Upstream #7318 made `realtime_model.py` a star shim; the snapshot follows it one hop."""
    for key in (
        "livekit.plugins.openai.realtime.RealtimeModel",
        "livekit.plugins.openai.realtime.RealtimeModel.with_azure",
        "livekit.plugins.openai.realtime.RealtimeSession",
    ):
        assert key in SIGNATURES, key
    assert {"model", "voice", "modalities"} <= set(
        SIGNATURES["livekit.plugins.openai.realtime.RealtimeModel"]["params"]
    )


def test_with_openrouter_is_in_the_fixture_and_takes_every_openrouter_llm_field() -> None:
    """V4-03: the snapshot captured the static constructor, so the field-name gate above really ran."""
    signature = SIGNATURES["livekit.plugins.openai.LLM.with_openrouter"]
    assert signature["has_var_keyword"] is False
    assert {"site_url", "app_name", "fallback_models", "provider", "temperature", "api_key", "model"} <= set(
        signature["params"]
    )
