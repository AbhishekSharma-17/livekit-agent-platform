"""Vault encryption, fingerprints and the master-key CLI."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from lkap_api.keys import generate_master_key, main
from lkap_api.vault import UNKNOWN_FINGERPRINT, Vault, VaultError, fingerprint


def test_vault_round_trips_a_secret_bag() -> None:
    vault = Vault(Fernet.generate_key().decode())
    secrets = {"api_key": "sk-test-abcd1234", "org": "acme"}

    ciphertext = vault.encrypt(secrets)

    assert b"sk-test-abcd1234" not in ciphertext
    assert vault.decrypt(ciphertext) == secrets


def test_vault_encrypt_is_non_deterministic() -> None:
    vault = Vault(Fernet.generate_key().decode())

    assert vault.encrypt({"api_key": "x"}) != vault.encrypt({"api_key": "x"})


def test_vault_rejects_ciphertext_from_another_key() -> None:
    written = Vault(Fernet.generate_key().decode()).encrypt({"api_key": "secret"})
    other = Vault(Fernet.generate_key().decode())

    with pytest.raises(VaultError):
        other.decrypt(written)


def test_vault_rejects_an_invalid_master_key() -> None:
    with pytest.raises(ValueError, match="LKAP_MASTER_KEY"):
        Vault("not-a-fernet-key")


@pytest.mark.parametrize(
    ("secrets", "primary", "expected"),
    [
        ({"api_key": "sk-live-9f3a"}, "api_key", "…9f3a"),
        ({"api_key": "sk-live-9f3a", "org": "acme"}, "api_key", "…9f3a"),
        ({"TOKEN": "abcdef"}, None, "…cdef"),
        ({}, "api_key", UNKNOWN_FINGERPRINT),
        ({"api_key": ""}, "api_key", UNKNOWN_FINGERPRINT),
    ],
)
def test_fingerprint_reveals_only_four_characters(
    secrets: dict[str, str], primary: str | None, expected: str
) -> None:
    assert fingerprint(secrets, primary_field=primary) == expected


def test_generate_master_key_is_usable() -> None:
    key = generate_master_key()

    assert Vault(key).decrypt(Vault(key).encrypt({"a": "b"})) == {"a": "b"}


def test_keys_cli_prints_a_key(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["generate"])

    printed = capsys.readouterr().out.strip()
    assert exit_code == 0
    assert Vault(printed) is not None


def test_keys_cli_rejects_unknown_commands(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["bogus"]) == 2
    assert "usage" in capsys.readouterr().err


def test_keys_cli_rotate_without_keys_names_the_environment_variables(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """V2-21: keys come from LKAP_OLD/NEW_MASTER_KEY so they never sit in argv."""
    monkeypatch.delenv("LKAP_OLD_MASTER_KEY", raising=False)
    monkeypatch.delenv("LKAP_NEW_MASTER_KEY", raising=False)

    assert main(["rotate"]) == 1
    assert "LKAP_OLD_MASTER_KEY" in capsys.readouterr().err
