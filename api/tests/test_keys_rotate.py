"""`python -m lkap_api.keys rotate` must move every secret onto the new key, or none."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from lkap_api.db.models import (
    Credential,
    LiveKitConnection,
    SipTrunk,
    StorageConfig,
    WebhookEndpoint,
    new_id,
)
from lkap_api.db.session import Database
from lkap_api.keys import CIPHER_COLUMNS, RotationError, generate_master_key, rotate
from lkap_api.settings import Settings
from lkap_api.vault import Vault, fingerprint


async def _seed_secrets(database: Database, settings: Settings) -> None:
    vault = Vault(settings.master_key)
    async with database.session() as session:
        connection = await session.scalar(select(LiveKitConnection))
        assert connection is not None
        session.add(
            Credential(
                id=new_id(),
                provider_id="openai-llm",
                label="OpenAI",
                ciphertext=vault.encrypt({"api_key": "sk-secret"}),
                fingerprint=fingerprint({"api_key": "sk-secret"}),
            )
        )
        storage = StorageConfig(
            id=new_id(),
            name="recordings",
            kind="s3",
            access_key_ct=vault.encrypt({"access_key": "AKIA-secret"}),
            secret_key_ct=vault.encrypt({"secret_key": "s3-secret"}),
        )
        session.add(storage)
        session.add(
            WebhookEndpoint(
                id=new_id(),
                url="https://hooks.example.com/lkap",
                secret_ct=vault.encrypt({"secret": "whsec-secret"}),
            )
        )
        session.add(
            SipTrunk(
                id=new_id(),
                connection_id=connection.id,
                direction="inbound",
                name="twilio",
                auth_password_ct=vault.encrypt({"password": "sip-secret"}),
            )
        )


async def test_rotate_re_encrypts_every_secret_with_the_new_key(
    settings: Settings, database: Database
) -> None:
    await _seed_secrets(database, settings)
    new_key = generate_master_key()

    counts = await rotate(database, settings.master_key, new_key)

    assert set(counts) == {f"{spec.table}.{spec.column}" for spec in CIPHER_COLUMNS}
    assert counts["credentials.ciphertext"] == 1
    assert counts["livekit_connections.api_key_ct"] == 1
    assert counts["livekit_connections.api_secret_ct"] == 1
    assert counts["storage_configs.access_key_ct"] == 1
    assert counts["storage_configs.secret_key_ct"] == 1
    assert counts["webhook_endpoints.secret_ct"] == 1
    assert counts["sip_trunks.auth_password_ct"] == 1

    new_vault = Vault(new_key)
    async with database.session() as session:
        credential = await session.scalar(select(Credential))
        connection = await session.scalar(select(LiveKitConnection))
        storage = await session.scalar(select(StorageConfig))
        endpoint = await session.scalar(select(WebhookEndpoint))
        trunk = await session.scalar(select(SipTrunk))

    assert credential is not None
    assert new_vault.decrypt(credential.ciphertext) == {"api_key": "sk-secret"}
    assert connection is not None
    assert new_vault.decrypt(connection.api_key_ct) == {"api_key": settings.livekit_api_key}
    assert new_vault.decrypt(connection.api_secret_ct) == {"api_secret": settings.livekit_api_secret}
    assert storage is not None
    assert storage.access_key_ct is not None
    assert storage.secret_key_ct is not None
    assert new_vault.decrypt(storage.access_key_ct) == {"access_key": "AKIA-secret"}
    assert new_vault.decrypt(storage.secret_key_ct) == {"secret_key": "s3-secret"}
    assert endpoint is not None
    assert new_vault.decrypt(endpoint.secret_ct) == {"secret": "whsec-secret"}
    assert trunk is not None
    assert trunk.auth_password_ct is not None
    assert new_vault.decrypt(trunk.auth_password_ct) == {"password": "sip-secret"}


async def test_rotate_leaves_nothing_readable_with_the_old_key(
    settings: Settings, database: Database
) -> None:
    await _seed_secrets(database, settings)
    new_key = generate_master_key()

    await rotate(database, settings.master_key, new_key)

    old_vault = Vault(settings.master_key)
    async with database.session() as session:
        credential = await session.scalar(select(Credential))
    assert credential is not None
    with pytest.raises(Exception, match="could not be decrypted"):
        old_vault.decrypt(credential.ciphertext)


async def test_rotate_with_a_wrong_old_key_changes_nothing(settings: Settings, database: Database) -> None:
    await _seed_secrets(database, settings)
    async with database.session() as session:
        before = (await session.scalar(select(Credential))).ciphertext  # type: ignore[union-attr]

    # A valid Fernet key that simply is not the one the rows were written with.
    wrong_old_key = generate_master_key()

    with pytest.raises(RotationError, match="could not be decrypted"):
        await rotate(database, wrong_old_key, generate_master_key())

    async with database.session() as session:
        after = (await session.scalar(select(Credential))).ciphertext  # type: ignore[union-attr]
    assert bytes(after) == bytes(before)
    assert Vault(settings.master_key).decrypt(after) == {"api_key": "sk-secret"}


async def test_rotate_refuses_an_invalid_new_key(settings: Settings, database: Database) -> None:
    with pytest.raises(RotationError, match="--new is not a valid Fernet key"):
        await rotate(database, settings.master_key, "not-a-fernet-key")


async def test_rotate_refuses_the_same_key_twice(settings: Settings, database: Database) -> None:
    with pytest.raises(RotationError, match="nothing to rotate"):
        await rotate(database, settings.master_key, settings.master_key)


async def test_rotate_skips_null_ciphertext_columns(settings: Settings, database: Database) -> None:
    async with database.session() as session:
        session.add(StorageConfig(id=new_id(), name="local dev", kind="local"))

    counts = await rotate(database, settings.master_key, generate_master_key())

    assert counts["storage_configs.access_key_ct"] == 0
    assert counts["storage_configs.secret_key_ct"] == 0
