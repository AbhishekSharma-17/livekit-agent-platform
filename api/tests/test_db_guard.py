"""The tenancy guard and the Postgres compatibility of the v2 DDL.

Two independent contract checks live here because both are about the schema
rather than about any route:

* the "no unscoped tenant query" guard CONTRACTS-V2 §3.1 asks for, and
* that every table compiles on Postgres as well as SQLite. The api CI matrix
  (`.github/workflows/api-postgres.yml`) runs the whole suite against a real
  `postgres:16`; this test is the part that runs everywhere, with no server.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateIndex, CreateTable

from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.guard import (
    TENANT_TABLES,
    UnscopedTenantQuery,
    check_statement,
    tenant_scope_guard,
)
from lkap_api.db.models import Agent, Base, Session, Workspace
from lkap_api.db.session import Database

# ------------------------------------------------------------------ tenancy guard


def test_check_statement_rejects_a_select_on_a_tenant_table_without_a_workspace() -> None:
    with pytest.raises(UnscopedTenantQuery, match="agents"):
        check_statement(select(Agent))


def test_check_statement_allows_a_select_scoped_by_workspace_id() -> None:
    check_statement(select(Agent).where(Agent.workspace_id == DEFAULT_WORKSPACE_ID))


def test_check_statement_allows_a_select_on_a_non_tenant_table() -> None:
    check_statement(select(Workspace).where(Workspace.slug == "default"))


def test_check_statement_names_every_unscoped_tenant_table_it_found() -> None:
    with pytest.raises(UnscopedTenantQuery) as excinfo:
        check_statement(select(Session))

    assert "sessions" in str(excinfo.value)


def test_tenant_tables_covers_every_model_carrying_a_workspace_id() -> None:
    with_workspace = {name for name, table in Base.metadata.tables.items() if "workspace_id" in table.c}
    # Two tables are deliberately excluded. `audit_log` has no foreign key and must
    # stay readable across workspaces for the platform admin; `workspace_members` is
    # what `GET /v1/auth/me` reads *by user* to discover which workspaces exist for
    # that person, which is a cross-workspace query by design (CONTRACTS-V2 §3.1).
    assert with_workspace - TENANT_TABLES == {"audit_log", "workspace_members"}


async def test_tenant_scope_guard_fires_on_a_real_unscoped_orm_query(
    database: Database,
) -> None:
    with tenant_scope_guard(), pytest.raises(UnscopedTenantQuery):
        async with database.session() as session:
            await session.scalar(select(Agent))


async def test_tenant_scope_guard_passes_a_scoped_orm_query(database: Database) -> None:
    with tenant_scope_guard():
        async with database.session() as session:
            await session.scalar(select(Agent).where(Agent.workspace_id == DEFAULT_WORKSPACE_ID))


async def test_tenant_scope_guard_is_removed_when_the_block_exits(database: Database) -> None:
    with tenant_scope_guard():
        pass

    async with database.session() as session:
        assert await session.scalar(select(Agent)) is None


async def test_bootstrap_queries_pass_the_tenant_guard(database: Database, settings: object) -> None:
    from lkap_api.bootstrap import bootstrap
    from lkap_api.settings import Settings

    assert isinstance(settings, Settings)

    with tenant_scope_guard():
        result = await bootstrap(database, settings)

    assert not result.changed


# --------------------------------------------------------- Postgres compatibility


@pytest.mark.parametrize("dialect_name", ["postgresql", "sqlite"])
def test_every_table_compiles_on_both_backends(dialect_name: str) -> None:
    dialect = postgresql.dialect() if dialect_name == "postgresql" else sqlite.dialect()

    for table in Base.metadata.sorted_tables:
        CreateTable(table).compile(dialect=dialect)
        for index in table.indexes:
            CreateIndex(index).compile(dialect=dialect)


def test_no_table_uses_a_sqlite_only_column_type() -> None:
    forbidden = {"sqlite"}

    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            module = type(column.type).__module__
            assert not any(part in module.split(".") for part in forbidden), (
                f"{table.name}.{column.name} uses {type(column.type).__name__}"
            )


def test_the_default_connection_index_is_partial_on_both_backends() -> None:
    index = next(
        ix
        for ix in Base.metadata.tables["livekit_connections"].indexes
        if ix.name == "ix_livekit_connections_default"
    )

    postgres_ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    sqlite_ddl = str(CreateIndex(index).compile(dialect=sqlite.dialect()))

    assert index.unique
    # `is_default` is a real boolean on Postgres; SQLite stores it as 0/1 (and the
    # index already built in migrated SQLite databases says `= 1`).
    assert postgres_ddl.rstrip().endswith("WHERE is_default")
    assert "WHERE is_default = 1" in sqlite_ddl
