"""The agent side of connected apps (V5-47, docs/v5/COMPOSIO.md §3, D-V5-C6/C7)."""

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from lkap_contracts.agent_config import AgentConfig, ToolsConfig
from lkap_contracts.api_models import ToolCreate
from lkap_contracts.export import EXPORTED_MODELS
from lkap_contracts.tool_providers import (
    ROUTER_EXCLUDED_TOOLS,
    AppsMode,
    AppsRouterOptions,
    router_allowed_tools,
)
from lkap_contracts.tools import (
    McpServerDefinition,
    McpServerOrigin,
    ProviderToolDefinition,
    ToolDefinition,
    ToolExecution,
    never_background,
)

ADAPTER: TypeAdapter[ToolDefinition] = TypeAdapter(ToolDefinition)


def _provider(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "kind": "provider",
        "provider": "composio",
        "name": "googlecalendar_find_free_slots",
        "description": "Find free slots.",
        "parameters": {"type": "object", "properties": {"day": {"type": "string"}}},
        "tool_slug": "GOOGLECALENDAR_FIND_FREE_SLOTS",
        "toolkit": "googlecalendar",
        "connection_id": "conn1",
        "credential_id": "key1",
        "subject": "ws:w1",
    }
    body.update(overrides)
    return body


def test_provider_definition_round_trips_through_the_discriminator() -> None:
    parsed = ADAPTER.validate_python(_provider())

    assert isinstance(parsed, ProviderToolDefinition)
    assert ADAPTER.validate_python(parsed.model_dump(mode="json")) == parsed


def test_provider_definition_voice_defaults_match_d_v5_c8() -> None:
    parsed = ProviderToolDefinition.model_validate(_provider())

    assert parsed.max_result_chars == 1500
    assert parsed.result_path == "data"
    assert parsed.silent_reply is False
    assert parsed.schema_version is None
    assert parsed.connected_account_id is None
    assert parsed.headers == {"x-api-key": "{{ secret.api_key }}"}


@pytest.mark.parametrize("name", ["1starts_with_digit", "has-dash", "x" * 65, ""])
def test_provider_definition_refuses_a_bad_tool_name(name: str) -> None:
    with pytest.raises(ValidationError):
        ProviderToolDefinition.model_validate(_provider(name=name))


def test_provider_definition_refuses_a_non_blocking_destructive_action() -> None:
    with pytest.raises(ValidationError, match="destructive"):
        ProviderToolDefinition.model_validate(_provider(risk="destructive", execution={"mode": "background"}))


def test_provider_definition_refuses_silent_reply_with_background() -> None:
    with pytest.raises(ValidationError, match="silent_reply"):
        ProviderToolDefinition.model_validate(_provider(silent_reply=True, execution={"mode": "auto"}))


def test_mcp_definition_carries_an_optional_origin() -> None:
    plain = McpServerDefinition(name="docs", url="https://mcp.example.com/mcp")
    tagged = McpServerDefinition(
        name="composio_tool_finder",
        url="https://backend.composio.dev/tool_router/abc/mcp",
        origin=McpServerOrigin(kind="router", remote_id="trs_1"),
    )

    assert plain.origin is None
    assert tagged.origin is not None
    assert tagged.origin.provider == "composio"
    assert ADAPTER.validate_python(tagged.model_dump(mode="json")) == tagged


def test_origin_kind_is_server_or_router() -> None:
    with pytest.raises(ValidationError):
        McpServerOrigin.model_validate({"kind": "other", "remote_id": "x"})


def test_apps_mode_defaults_to_off() -> None:
    apps = ToolsConfig().apps

    assert apps == AppsMode()
    assert apps.mode == "off"
    assert apps.allowed_toolkits == []
    assert apps.denied_actions == []
    assert apps.router == AppsRouterOptions(search=True, execute=True, manage_connections=False)


def test_a_config_saved_before_apps_existed_resolves_to_off() -> None:
    """Compatibility: no `tools.apps` key in stored JSON means `off` and nothing else changes."""
    stored = {
        "instructions": "Help.",
        "pipeline": {"mode": "cascaded"},
        "tools": {"tool_ids": ["t1"], "max_tool_steps": 3},
    }

    config = AgentConfig.model_validate(stored)

    assert config.tools.apps.mode == "off"
    assert config.tools.tool_ids == ["t1"]


def test_router_allowed_tools_follow_the_flags() -> None:
    assert router_allowed_tools(AppsRouterOptions()) == [
        "COMPOSIO_SEARCH_TOOLS",
        "COMPOSIO_GET_TOOL_SCHEMAS",
        "COMPOSIO_MULTI_EXECUTE_TOOL",
    ]
    with_connections = router_allowed_tools(AppsRouterOptions(manage_connections=True))
    assert "COMPOSIO_MANAGE_CONNECTIONS" in with_connections
    assert "COMPOSIO_WAIT_FOR_CONNECTIONS" in with_connections
    assert router_allowed_tools(AppsRouterOptions(search=False, execute=False)) == []


def test_router_never_attaches_the_excluded_meta_tools() -> None:
    every = router_allowed_tools(AppsRouterOptions(manage_connections=True))
    assert not set(every) & set(ROUTER_EXCLUDED_TOOLS)


@pytest.mark.parametrize(
    "name", ["COMPOSIO_MULTI_EXECUTE_TOOL", "COMPOSIO_MANAGE_CONNECTIONS", "COMPOSIO_WAIT_FOR_CONNECTIONS"]
)
def test_router_execute_and_connection_tools_are_never_backgrounded(name: str) -> None:
    assert never_background(name)


def test_router_search_tools_may_run_in_the_background() -> None:
    assert not never_background("COMPOSIO_SEARCH_TOOLS")


def test_tool_create_accepts_the_provider_kind() -> None:
    definition = ProviderToolDefinition.model_validate(_provider())
    created = ToolCreate(kind="provider", name="x_y", definition=definition)
    assert created.definition.kind == "provider"


def test_new_models_are_exported() -> None:
    for model in (ProviderToolDefinition, McpServerOrigin, AppsMode, AppsRouterOptions):
        assert EXPORTED_MODELS[model.__name__] is model


def test_mcp_tool_options_still_limited_to_allowed_tools_with_an_origin() -> None:
    with pytest.raises(ValidationError):
        McpServerDefinition(
            name="s",
            url="https://backend.composio.dev/x",
            allowed_tools=["A"],
            tool_options={"B": ToolExecution(mode="auto")},
            origin=McpServerOrigin(kind="server", remote_id="trs_1"),
        )
