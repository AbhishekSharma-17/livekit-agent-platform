"""The result envelope, redaction, scrubbing and the untrusted cap (D-V3-5)."""

from __future__ import annotations

from lkap_mcp.results import UNTRUSTED_CAP, ToolResult, hide_input_values, redact, sanitize, untrusted
from lkap_mcp.secrets import INLINE_PLACEHOLDER, REDACTED, REF_PLACEHOLDER, remember


def test_redact_secret_named_keys_at_any_depth_case_insensitive() -> None:
    value = {
        "API_SECRET": "x",
        "nested": [{"token": "abc", "Authorization": "Bearer y", "password": 7}],
        "secrets": {"api_key": "k1", "other": "k2"},
    }

    assert redact(value) == {
        "API_SECRET": REDACTED,
        "nested": [{"token": REDACTED, "Authorization": REDACTED, "password": REDACTED}],
        "secrets": {"api_key": REDACTED, "other": REDACTED},
    }


def test_redact_keeps_safe_metadata_and_placeholders() -> None:
    value = {
        "secret_prefix": "whsec_ab",
        "has_password": True,
        "secret_key_id": "cred_1",
        "fingerprint": "…1234",
        "api_key": INLINE_PLACEHOLDER,
        "api_secret": REF_PLACEHOLDER,
        "token": None,
    }

    assert redact(value) == value


def test_sanitize_scrubs_seen_values_in_data_errors_and_keys() -> None:
    secret = "lk-secret-0123456789"
    remember(secret)
    result = ToolResult.fail("bad_request", f"LiveKit rejected {secret}", details={secret: [f"x{secret}y"]})

    clean = sanitize(result).model_dump_json()

    assert secret not in clean
    assert REDACTED in clean


def test_untrusted_caps_content_at_eight_thousand_characters() -> None:
    wrapped = untrusted("a" * (UNTRUSTED_CAP + 50), "kb:1")

    assert wrapped.untrusted is True
    assert len(wrapped.content) == UNTRUSTED_CAP and wrapped.truncated


def test_hide_input_values_drops_the_pydantic_echo() -> None:
    message = (
        "1 validation error\nsecrets.k\n  Input should be a valid string "
        "[type=string_type, input_value=123456789, input_type=int]"
    )

    assert "123456789" not in hide_input_values(message)
    assert "input_value=<hidden>" in hide_input_values(message)


def test_needs_confirmation_is_ok_false_with_code_and_hint() -> None:
    result = ToolResult.needs_confirmation("delete agent a")

    assert (result.ok, result.error.code if result.error else None) == (False, "needs_confirmation")
    assert result.error is not None and "confirm=true" in (result.error.hint or "")
