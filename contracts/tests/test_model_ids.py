"""The model-id rule (docs/v4/CUSTOM-MODELS.md D-V4-23, R-V4-21).

A model id is free text under one rule: a secret-looking value is refused
first, without echo, then the syntax rule applies. Every id the registry
itself carries must pass.
"""

import re

import pytest

from lkap_contracts.api_models import ModelIdRules
from lkap_contracts.providers import (
    BARE_TOKEN_MIN_LEN,
    ID_LIKE_FIELD_NAMES,
    MODEL_ID_MAX_LEN,
    MODEL_ID_PATTERN,
    REGISTRY,
    SECRET_LOOKING_REASON,
    SECRET_PREFIXES,
    id_like_field,
    looks_like_secret,
    validate_model_id,
)


def _registry_ids() -> list[str]:
    """Every model id, default and id-like field value the registry carries."""
    ids: set[str] = set()
    for spec in REGISTRY:
        ids.update(model.id for model in spec.models)
        if spec.default_model:
            ids.add(spec.default_model)
        for field in spec.fields:
            if field.type in ("model", "catalog") or id_like_field(field.name):
                if isinstance(field.default, str) and field.default:
                    ids.add(field.default)
                if field.type != "enum" or id_like_field(field.name):
                    ids.update(field.options or [])
        ids.update(voice for voice in spec.capabilities.voices)
    return sorted(ids)


@pytest.mark.parametrize("model_id", _registry_ids())
def test_every_id_the_registry_carries_passes(model_id: str) -> None:
    assert validate_model_id(model_id) is None


@pytest.mark.parametrize(
    "model_id",
    [
        "amazon.nova-2-lite-v1:0",
        "openai/gpt-4o-mini:free",
        "BAAI/bge-small-en-v1.5",
        "cartesia/sonic-3:a0e99841-438c-4a64-b679-ae501e7d6091",
        "accounts/fireworks/models/llama-v3p1-8b-instruct",
        "ft:gpt-4o-mini:org::abc",
        "gemini-2.5-flash-native-audio-preview-12-2025",
        "models@2025+beta",
        "x-" * (MODEL_ID_MAX_LEN // 2),
        "a" * (BARE_TOKEN_MIN_LEN - 1),
    ],
)
def test_real_world_id_shapes_pass(model_id: str) -> None:
    assert validate_model_id(model_id) is None
    assert re.fullmatch(MODEL_ID_PATTERN, model_id) is not None


@pytest.mark.parametrize(
    ("value", "reason_fragment"),
    [
        ("", "empty"),
        ("x-" * (MODEL_ID_MAX_LEN // 2) + "x", "longer than"),
        ("gpt 4", "whitespace"),
        ("gpt-4\t", "whitespace"),
        ("gpt\x00", "control"),
        ("gpt-4ö", "non-ASCII"),
        ("https://example.com/model", "URL"),
        ("httpbin", "URL"),
        ("Http-model", "URL"),
        ("vendor://model", "URL"),
        ("model?x=1", "character"),
        ("model#frag", "character"),
        ("a&b", "character"),
        ("a=b", "character"),
        ("<script>", "character"),
        ('"quoted"', "character"),
        ("'quoted'", "character"),
        ("back`tick", "character"),
    ],
)
def test_each_syntax_rejection_names_a_reason(value: str, reason_fragment: str) -> None:
    reason = validate_model_id(value)
    assert reason is not None
    assert reason_fragment in reason
    assert re.fullmatch(MODEL_ID_PATTERN, value) is None, "the console's regex agrees with the checks"


def _fake_secret(prefix: str) -> str:
    """A secret-shaped value: the prefix plus a distinctive tail."""
    return prefix + "Zq9WkX7vRt3LmN8pYb2HcJ5d"


@pytest.mark.parametrize("prefix", SECRET_PREFIXES)
def test_every_secret_prefix_is_refused_with_a_value_free_reason(prefix: str) -> None:
    value = _fake_secret(prefix)
    reason = validate_model_id(value)

    assert reason == SECRET_LOOKING_REASON
    assert looks_like_secret(value)
    # No part of the value survives: not the tail, not even the prefix itself.
    tail = value[len(prefix) :]
    for size in (6, 4):
        for start in range(len(tail) - size + 1):
            assert tail[start : start + size] not in reason
    assert prefix not in reason


@pytest.mark.parametrize(
    "value",
    [
        "0123456789abcdef0123456789abcdef",  # 32-hex (Tavus/Simli-style keys)
        "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",  # 36 alphanumerics
        "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo+YWJjZA==",  # base64
    ],
)
def test_a_bare_token_is_refused_as_a_secret(value: str) -> None:
    assert validate_model_id(value) == SECRET_LOOKING_REASON


def test_a_long_id_with_separators_is_not_a_bare_token() -> None:
    assert validate_model_id("gemini-2-5-flash-native-audio-preview-12-2025-extra") is None
    assert validate_model_id("a" * 20 + "_" + "b" * 20) is None, "underscore is not a key character class"


def test_the_secret_check_runs_before_the_syntax_check() -> None:
    # `=` alone is a syntax error, but a key-shaped value gets the secret reason first.
    assert validate_model_id("sk-abc=def") == SECRET_LOOKING_REASON


def test_the_pattern_is_javascript_compatible() -> None:
    assert "(?P" not in MODEL_ID_PATTERN
    assert "\\A" not in MODEL_ID_PATTERN and "\\Z" not in MODEL_ID_PATTERN
    assert MODEL_ID_PATTERN.startswith("^") and MODEL_ID_PATTERN.endswith("$")
    re.compile(MODEL_ID_PATTERN)


def test_model_id_rules_default_to_the_contract_constants() -> None:
    rules = ModelIdRules()
    assert rules.pattern == MODEL_ID_PATTERN
    assert rules.secret_prefixes == list(SECRET_PREFIXES)
    assert rules.max_len == MODEL_ID_MAX_LEN
    assert rules.bare_token_min_len == BARE_TOKEN_MIN_LEN


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("voice", True),
        ("voice_id", True),
        ("simli_config.face_id", True),
        ("simli_config.emotion_id", True),
        ("model", False),
        ("language", False),
        ("base_url", False),
    ],
)
def test_id_like_field_matches_the_last_segment(name: str, expected: bool) -> None:
    assert id_like_field(name) is expected
    assert ID_LIKE_FIELD_NAMES >= {"voice", "voice_id", "avatar_id", "face_id", "pal_id", "persona_id"}
