"""A test guard that fails any query on a tenant table without a workspace predicate.

CONTRACTS-V2 §3.1 requires that "tests include a guard that fails any query on a
tenant table without a ``workspace_id`` predicate". The listener is installed on
every ORM :class:`~sqlalchemy.orm.Session` and inspects each ``select()`` before
it is executed.

``api/tests/conftest.py`` turns it on for the whole suite (autouse, V2-02), with
an ``exempt`` callback that skips statements issued by test code itself (a
test's own assertion reads are not application queries). Deliberate
cross-workspace reads in the application — authentication lookups, worker
``/internal`` routes, sweeps, jobs, the key-rotation CLI — carry
``.execution_options(lkap_cross_workspace=True)``.

Implementation note: SQLAlchemy 2's hook for this is ``do_orm_execute`` on the
session, not the legacy ``before_compile`` on ``Query``, which 2.0-style
``select()`` statements never reach.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import ORMExecuteState, Session
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnClause

#: Tables whose rows belong to exactly one workspace (CONTRACTS-V2 §1.1).
TENANT_TABLES: frozenset[str] = frozenset(
    {
        "agents",
        "credentials",
        "tools",
        "knowledge_bases",
        "sessions",
        "api_keys",
        "livekit_connections",
        "storage_configs",
        "webhook_endpoints",
        "sip_trunks",
        "sip_dispatch_rules",
        "phone_numbers",
        "calls",
        "usage_daily",
        "workspace_providers",
    }
)


#: Execution option that marks a deliberate cross-workspace read (authentication
#: lookups by token hash, the session sweep, worker-facing internal routes).
#: Usage: ``select(ApiKey).where(...).execution_options(lkap_cross_workspace=True)``.
CROSS_WORKSPACE_OPTION = "lkap_cross_workspace"


class UnscopedTenantQuery(AssertionError):
    """Raised when a tenant table is queried without a `workspace_id` predicate."""


def _selected_tenant_tables(statement: Select[Any]) -> set[str]:
    # `get_final_froms()` returns `FromClause`, which only has a `name` when it is
    # a real table (a join or subquery has none), hence the `getattr`.
    names = {getattr(from_clause, "name", None) for from_clause in statement.get_final_froms()}
    return {name for name in names if isinstance(name, str) and name in TENANT_TABLES}


def _mentions_workspace(statement: Select[Any]) -> bool:
    whereclause = statement.whereclause
    if whereclause is None:
        return False
    return any(
        isinstance(element, ColumnClause) and element.name == "workspace_id"
        for element in whereclause.get_children(column_collections=False, omit_attrs=())
    ) or "workspace_id" in str(whereclause)


def check_statement(statement: Select[Any]) -> None:
    """Raise :class:`UnscopedTenantQuery` if `statement` reads a tenant table unscoped.

    Args:
        statement: The ORM ``select()`` about to be executed.

    Raises:
        UnscopedTenantQuery: When a tenant table appears in the FROM clause and
            no ``workspace_id`` predicate constrains it.
    """
    tables = _selected_tenant_tables(statement)
    if not tables or _mentions_workspace(statement):
        return
    raise UnscopedTenantQuery(
        "query on tenant table(s) "
        f"{sorted(tables)} has no workspace_id predicate; "
        "scope it through WorkspaceContext (CONTRACTS-V2 §3.1)"
    )


def _listener(state: ORMExecuteState) -> None:
    if not state.is_select or not isinstance(state.statement, Select):
        return
    if state.execution_options.get(CROSS_WORKSPACE_OPTION) or state.statement.get_execution_options().get(
        CROSS_WORKSPACE_OPTION
    ):
        return
    check_statement(state.statement)


@contextmanager
def tenant_scope_guard(exempt: Callable[[ORMExecuteState], bool] | None = None) -> Iterator[None]:
    """Fail every unscoped tenant-table ORM query for the duration of the block.

    Args:
        exempt: Optional predicate; statements it returns ``True`` for are not
            checked (the test suite exempts reads issued by test code).
    """

    def listener(state: ORMExecuteState) -> None:
        if exempt is not None and exempt(state):
            return
        _listener(state)

    event.listen(Session, "do_orm_execute", listener)
    try:
        yield
    finally:
        event.remove(Session, "do_orm_execute", listener)
