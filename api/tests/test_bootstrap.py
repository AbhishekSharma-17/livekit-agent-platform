"""`lkap_api.bootstrap` is the startup half of the zero-downtime v1 migration (D-V2-6)."""

from __future__ import annotations

import pytest
from conftest import inference_config
from sqlalchemy import func, select

from lkap_api.bootstrap import bootstrap, deployment_type_for
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID, DEFAULT_WORKSPACE_SLUG
from lkap_api.db.models import Agent, LiveKitConnection, User, Workspace, WorkspaceMember, new_id
from lkap_api.db.session import Database
from lkap_api.settings import Settings, get_settings
from lkap_api.vault import Vault


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("wss://example-cloud.livekit.cloud", "cloud"),
        ("wss://DGX-SPARK.LIVEKIT.CLOUD", "cloud"),
        ("ws://localhost:7880", "self_hosted"),
        ("wss://livekit.example.com", "self_hosted"),
        ("wss://livekit.cloud.example.com", "self_hosted"),
    ],
)
def test_deployment_type_for_infers_cloud_only_from_the_livekit_cloud_suffix(url: str, expected: str) -> None:
    assert deployment_type_for(url) == expected


async def test_bootstrap_on_an_empty_database_creates_workspace_owner_and_connection(
    settings: Settings, database: Database
) -> None:
    # The `database` fixture already bootstrapped once, so assert the outcome.
    async with database.session() as session:
        workspace = await session.scalar(select(Workspace).where(Workspace.slug == DEFAULT_WORKSPACE_SLUG))
        owner = await session.scalar(select(User))
        member = await session.scalar(select(WorkspaceMember))
        connection = await session.scalar(select(LiveKitConnection))

    assert workspace is not None
    assert workspace.id == DEFAULT_WORKSPACE_ID
    assert owner is not None
    assert owner.email == settings.bootstrap_owner_email
    assert member is not None
    assert member.role == "owner"
    assert connection is not None
    assert connection.slug == "default"
    assert connection.is_default
    assert connection.deployment_mode == "external"
    assert connection.deployment_type == "cloud"
    assert connection.agent_name == settings.agent_name


async def test_bootstrap_run_twice_creates_no_duplicate_rows(settings: Settings, database: Database) -> None:
    result = await bootstrap(database, settings)

    assert not result.workspace_created
    assert not result.owner_created
    assert not result.connection_created
    async with database.session() as session:
        assert await session.scalar(select(func.count()).select_from(Workspace)) == 1
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        assert await session.scalar(select(func.count()).select_from(LiveKitConnection)) == 1


async def test_bootstrap_encrypts_the_livekit_credentials_with_the_master_key(
    settings: Settings, database: Database
) -> None:
    async with database.session() as session:
        connection = await session.scalar(select(LiveKitConnection))
    assert connection is not None

    vault = Vault(settings.master_key)

    assert vault.decrypt(connection.api_key_ct) == {"api_key": settings.livekit_api_key}
    assert vault.decrypt(connection.api_secret_ct) == {"api_secret": settings.livekit_api_secret}
    assert settings.livekit_api_secret.encode() not in bytes(connection.api_secret_ct)


async def test_bootstrap_without_livekit_credentials_creates_no_connection(
    monkeypatch: pytest.MonkeyPatch, data_dir: object, settings: Settings
) -> None:
    monkeypatch.setenv("LIVEKIT_URL", "")
    monkeypatch.setenv("LIVEKIT_API_KEY", "")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "")
    get_settings.cache_clear()
    bare = get_settings()
    database = Database(bare.resolved_database_url)
    await database.create_all()
    try:
        result = await bootstrap(database, bare)

        assert result.workspace_created
        assert not result.connection_created
        async with database.session() as session:
            assert await session.scalar(select(func.count()).select_from(LiveKitConnection)) == 0
    finally:
        await database.dispose()
        get_settings.cache_clear()


async def test_bootstrap_binds_agents_left_unbound_by_the_migration(
    settings: Settings, database: Database
) -> None:
    async with database.session() as session:
        session.add(
            Agent(
                id=new_id(),
                slug="unbound",
                name="Unbound",
                pack_id="generic",
                ui_panel_id="generic",
                config=inference_config().model_dump(mode="json"),
                connection_id=None,
            )
        )

    result = await bootstrap(database, settings)

    assert result.agents_bound == 1
    async with database.session() as session:
        agent = await session.scalar(select(Agent).where(Agent.slug == "unbound"))
        connection = await session.scalar(select(LiveKitConnection))
    assert agent is not None
    assert connection is not None
    assert agent.connection_id == connection.id


async def test_bootstrap_owner_gets_an_argon2id_hash_once_the_auth_package_exists(
    settings: Settings, database: Database
) -> None:
    async with database.session() as session:
        owner = await session.scalar(select(User))

    assert owner is not None
    # V2-02 shipped `lkap_api.auth.passwords`, so bootstrap now hashes the
    # generated owner password instead of leaving the hash NULL.
    assert owner.password_hash is not None
    assert owner.password_hash.startswith("$argon2id$")


async def test_bootstrap_hashes_the_owner_password_when_the_hasher_is_available(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, data_dir: object
) -> None:
    monkeypatch.setattr("lkap_api.bootstrap._password_hasher", lambda: lambda raw: f"hashed:{raw}")
    monkeypatch.setenv("LKAP_BOOTSTRAP_OWNER_PASSWORD", "correct horse battery staple")
    get_settings.cache_clear()
    configured = get_settings()
    database = Database(configured.resolved_database_url)
    await database.create_all()
    try:
        result = await bootstrap(database, configured)

        assert result.owner_created
        assert result.owner_password is None
        async with database.session() as session:
            owner = await session.scalar(select(User))
        assert owner is not None
        assert owner.password_hash == "hashed:correct horse battery staple"
    finally:
        await database.dispose()
        get_settings.cache_clear()


async def test_bootstrap_generates_a_password_when_none_is_configured(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, data_dir: object
) -> None:
    monkeypatch.setattr("lkap_api.bootstrap._password_hasher", lambda: lambda raw: f"hashed:{raw}")
    get_settings.cache_clear()
    configured = get_settings()
    database = Database(configured.resolved_database_url)
    await database.create_all()
    try:
        result = await bootstrap(database, configured)

        assert result.owner_password is not None
        assert len(result.owner_password) >= 24
        async with database.session() as session:
            owner = await session.scalar(select(User))
        assert owner is not None
        assert owner.password_hash == f"hashed:{result.owner_password}"
    finally:
        await database.dispose()
        get_settings.cache_clear()
