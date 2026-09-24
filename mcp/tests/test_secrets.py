"""``SecretInput`` parsing and resolution (D-V3-4, R-V3-3)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lkap_mcp.secrets import (
    INLINE_PLACEHOLDER,
    REDACTED,
    REF_PLACEHOLDER,
    SecretInputError,
    parse_secret,
    remember,
    resolve_secret,
    scrub_text,
)

VALUE = "sk-live-0123456789abcdef"


@pytest.mark.parametrize(
    ("raw", "kind", "placeholder"),
    [
        (VALUE, "inline", INLINE_PLACEHOLDER),
        ("raw:env:NOT_A_REF_VALUE", "inline", INLINE_PLACEHOLDER),
        ("env:LIVEKIT_API_KEY", "env", REF_PLACEHOLDER),
        ("file:/etc/lkap/dev.env#LIVEKIT_API_SECRET", "file", REF_PLACEHOLDER),
        ("file:~/lkap/secret.txt", "file", REF_PLACEHOLDER),
    ],
)
def test_parse_secret_forms_yield_kind_and_plan_placeholder(raw: str, kind: str, placeholder: str) -> None:
    parsed = parse_secret(raw, field_name="api_key")

    assert (parsed.kind, parsed.placeholder) == (kind, placeholder)
    assert VALUE not in repr(parsed)


def test_parse_secret_raw_prefix_keeps_the_literal_value() -> None:
    parsed = parse_secret("raw:file:looks-like-a-ref", field_name="api_key")

    assert resolve_secret(parsed).value == "file:looks-like-a-ref"


@pytest.mark.parametrize("raw", ["env:lower_case", "env:", "file:relative/path", "file:/x#1bad"])
def test_parse_secret_malformed_reference_is_invalid_secret_ref(raw: str) -> None:
    with pytest.raises(SecretInputError) as caught:
        parse_secret(raw, field_name="api_secret")

    assert caught.value.code == "invalid_secret_ref"
    assert "raw:" in (caught.value.hint or "")


def test_parse_secret_inline_refused_names_the_reference_forms_and_is_still_scrubbed() -> None:
    with pytest.raises(SecretInputError) as caught:
        parse_secret(VALUE, field_name="api_secret", inline_allowed=False)

    assert caught.value.code == "inline_secret_refused"
    assert "env:NAME" in (caught.value.hint or "") and "file:" in (caught.value.hint or "")
    assert VALUE not in str(caught.value)
    assert scrub_text(f"echo {VALUE}") == f"echo {REDACTED}"


def test_parse_secret_file_ref_in_http_mode_is_ref_unavailable_in_http_mode() -> None:
    with pytest.raises(SecretInputError) as caught:
        parse_secret("file:/tmp/x.env#K", field_name="api_key", file_allowed=False)

    assert caught.value.code == "ref_unavailable_in_http_mode"


def test_resolve_secret_env_reference_reads_the_process_env_and_remembers_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LKAP_TEST_SECRET", VALUE)

    resolved = resolve_secret(parse_secret("env:LKAP_TEST_SECRET", field_name="api_key"))

    assert resolved.value == VALUE
    assert scrub_text(VALUE) == REDACTED


def test_resolve_secret_missing_env_is_secret_ref_unresolved_without_a_value() -> None:
    os.environ.pop("LKAP_TEST_ABSENT", None)
    with pytest.raises(SecretInputError) as caught:
        resolve_secret(parse_secret("env:LKAP_TEST_ABSENT", field_name="api_key"))

    assert caught.value.code == "secret_ref_unresolved"
    assert "LKAP_TEST_ABSENT" in caught.value.message


def test_resolve_secret_dotenv_key_handles_export_quotes_and_comments(tmp_path: Path) -> None:
    env_file = tmp_path / "dev.env"
    env_file.write_text(
        f"# comment\nexport OTHER=1\nLIVEKIT_API_SECRET=\"{VALUE}\"\nLIVEKIT_API_KEY = 'APIabc123456'\n"
    )
    env_file.chmod(0o600)

    secret = resolve_secret(parse_secret(f"file:{env_file}#LIVEKIT_API_SECRET", field_name="api_secret"))
    key = resolve_secret(parse_secret(f"file:{env_file}#LIVEKIT_API_KEY", field_name="api_key"))

    assert (secret.value, key.value) == (VALUE, "APIabc123456")
    assert secret.warnings == ()


def test_resolve_secret_whole_file_is_trimmed_and_group_readable_file_warns(tmp_path: Path) -> None:
    path = tmp_path / "secret.txt"
    path.write_text(f"  {VALUE}\n")
    path.chmod(0o644)

    resolved = resolve_secret(parse_secret(f"file:{path}", field_name="api_key"))

    assert resolved.value == VALUE
    assert resolved.warnings and "chmod 600" in resolved.warnings[0]
    assert VALUE not in resolved.warnings[0]


@pytest.mark.parametrize("missing", ["#NOPE", ""])
def test_resolve_secret_missing_file_or_key_is_secret_ref_unresolved(tmp_path: Path, missing: str) -> None:
    path = tmp_path / ("dev.env" if missing else "absent.env")
    if missing:
        path.write_text(f"OTHER={VALUE}\n")
    with pytest.raises(SecretInputError) as caught:
        resolve_secret(parse_secret(f"file:{path}{missing}", field_name="api_secret"))

    assert caught.value.code == "secret_ref_unresolved"
    assert VALUE not in caught.value.message


def test_scrub_text_ignores_values_shorter_than_the_minimum() -> None:
    remember("short")

    assert scrub_text("a short note") == "a short note"
