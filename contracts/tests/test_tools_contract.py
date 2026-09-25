"""Background-tool contracts (docs/v4/BACKGROUND-TOOLS.md §3): round-trip, bounds, validators, lists."""

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

import lkap_contracts
from lkap_contracts.agent_config import AgentConfig, ToolsConfig, VoiceConfig
from lkap_contracts.export import EXPORTED_MODELS
from lkap_contracts.flow import edge_tool_name
from lkap_contracts.packs import ToolMeta
from lkap_contracts.tool_providers import ROUTER_CONNECTION_TOOLS, ROUTER_EXECUTE_TOOLS
from lkap_contracts.tools import (
    BACKGROUNDABLE_BUILTINS,
    BLOCK_TOOL_NAMES,
    BLOCK_TOOL_TYPES,
    BUILTIN_TOOL_NAMES,
    NEVER_BACKGROUND_TOOLS,
    UPDATABLE_BLOCK_TYPES,
    HttpToolDefinition,
    McpHeaderAuth,
    McpNoAuth,
    McpOAuthAuth,
    McpServerDefinition,
    McpTestResult,
    McpToolSnapshot,
    ToolDefinition,
    ToolExecution,
    never_background,
)

#: The telephony tools live in the worker (`lkap_agent.telephony.TELEPHONY_TOOL_NAMES`);
#: the agent suite asserts the two sets are equal.
TELEPHONY_TOOL_NAMES = frozenset({"transfer_call", "send_dtmf"})


def _http(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "lookup_policy",
        "description": "Look up a policy",
        "parameters": {"type": "object", "properties": {}},
        "method": "GET",
        "url": "https://api.example.com/policy",
    }
    base.update(overrides)
    return base


def test_tool_execution_defaults_round_trip() -> None:
    execution = ToolExecution()
    assert execution.mode is None
    assert execution.auto_threshold_ms == 700
    assert execution.fillers == []
    assert execution.filler_delay_s == 4.0
    assert execution.filler_interval_s == 8.0
    assert execution.cancellable is None
    assert execution.on_duplicate is None
    assert execution.duplicate_scope == "name_and_args"
    assert execution.max_duration_s == 60
    assert execution.report_progress is False
    assert ToolExecution.model_validate_json(execution.model_dump_json()) == execution


def test_tool_execution_full_round_trip() -> None:
    execution = ToolExecution(
        mode="auto",
        announce="Looking that up.",
        auto_threshold_ms=900,
        fillers=["Still checking.", "Almost there."],
        filler_delay_s=3,
        filler_interval_s=6,
        cancellable=True,
        on_duplicate="confirm",
        duplicate_scope="name",
        max_duration_s=120,
        report_progress=True,
    )
    assert ToolExecution.model_validate(execution.model_dump()) == execution


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("auto_threshold_ms", -1),
        ("auto_threshold_ms", 5001),
        ("fillers", ["a", "b", "c", "d", "e", "f"]),
        ("filler_delay_s", 0.4),
        ("filler_delay_s", 31),
        ("filler_interval_s", 0.5),
        ("filler_interval_s", 61),
        ("max_duration_s", 0),
        ("max_duration_s", 601),
        ("mode", "later"),
        ("on_duplicate", "ignore"),
        ("duplicate_scope", "args"),
    ],
)
def test_tool_execution_out_of_bounds_is_rejected(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        ToolExecution.model_validate({field: value})


@pytest.mark.parametrize("value", [0, 5000])
def test_tool_execution_auto_threshold_bounds_are_inclusive(value: int) -> None:
    assert ToolExecution(auto_threshold_ms=value).auto_threshold_ms == value


def test_http_tool_definition_defaults_to_an_unset_execution() -> None:
    definition = HttpToolDefinition.model_validate(_http())
    assert definition.execution == ToolExecution()
    assert HttpToolDefinition.model_validate_json(definition.model_dump_json()) == definition


@pytest.mark.parametrize("mode", ["background", "auto"])
def test_http_tool_silent_reply_with_a_non_blocking_mode_is_an_error(mode: str) -> None:
    with pytest.raises(ValidationError, match="silent_reply"):
        HttpToolDefinition.model_validate(_http(silent_reply=True, execution={"mode": mode}))


@pytest.mark.parametrize("mode", [None, "blocking"])
def test_http_tool_silent_reply_with_a_blocking_mode_is_fine(mode: str | None) -> None:
    definition = HttpToolDefinition.model_validate(_http(silent_reply=True, execution={"mode": mode}))
    assert definition.silent_reply is True


def test_mcp_tool_options_must_be_allowed_tools_when_those_are_set() -> None:
    with pytest.raises(ValidationError, match="outside allowed_tools: search"):
        McpServerDefinition(
            name="crm",
            url="https://mcp.example.com/mcp",
            allowed_tools=["lookup"],
            tool_options={"search": ToolExecution(mode="background")},
        )


def test_mcp_tool_options_are_free_without_allowed_tools() -> None:
    server = McpServerDefinition(
        name="crm",
        url="https://mcp.example.com/mcp",
        tool_options={"search": ToolExecution(mode="background", report_progress=True)},
    )
    assert McpServerDefinition.model_validate(server.model_dump()) == server


def test_agent_config_defaults_keep_todays_behaviour() -> None:
    config = AgentConfig.model_validate({"instructions": "Help.", "pipeline": {}})
    assert config.tools.execution_default == "blocking"
    assert config.tools.builtin_execution == {}
    assert config.voice.thinking_sound == "none"


def test_tools_config_and_voice_round_trip() -> None:
    tools = ToolsConfig(
        execution_default="auto", builtin_execution={"search_knowledge": ToolExecution(mode="auto")}
    )
    assert ToolsConfig.model_validate(tools.model_dump()) == tools
    with pytest.raises(ValidationError):
        VoiceConfig.model_validate({"thinking_sound": "hold_music"})


def test_tool_meta_execution_is_optional() -> None:
    assert ToolMeta(name="start_workflow").execution is None
    meta = ToolMeta(name="start_workflow", execution=ToolExecution(mode="background"))
    assert ToolMeta.model_validate(meta.model_dump()) == meta


def test_never_list_and_backgroundable_builtins_are_disjoint() -> None:
    assert NEVER_BACKGROUND_TOOLS & BACKGROUNDABLE_BUILTINS == frozenset()


def test_backgroundable_builtins_are_builtins() -> None:
    assert frozenset(BUILTIN_TOOL_NAMES) >= BACKGROUNDABLE_BUILTINS


def test_every_never_list_name_is_a_real_tool_name() -> None:
    # V5-47: the Composio tool finder's execute and connection meta tools (COMPOSIO.md D-V5-C7).
    router = frozenset(ROUTER_EXECUTE_TOOLS) | frozenset(ROUTER_CONNECTION_TOOLS)
    real = frozenset(BUILTIN_TOOL_NAMES) | frozenset(BLOCK_TOOL_NAMES) | TELEPHONY_TOOL_NAMES | router
    assert real >= NEVER_BACKGROUND_TOOLS


def test_quartet_block_tools_are_registered_for_their_block_and_never_background() -> None:
    quartet = {
        "request_choice": {"choices"},
        "resolve_choice": {"choices"},
        "set_details": {"details"},
        "show_text": {"markdown"},
        "set_steps": {"steps"},
    }
    for name, types in quartet.items():
        assert name in BLOCK_TOOL_NAMES
        assert BLOCK_TOOL_TYPES[name] == frozenset(types)
        assert never_background(name)
    assert {"details", "markdown", "steps"} <= UPDATABLE_BLOCK_TYPES
    assert "choices" not in UPDATABLE_BLOCK_TYPES


def test_every_builtin_is_either_backgroundable_or_never() -> None:
    names = frozenset(BUILTIN_TOOL_NAMES) | frozenset(BLOCK_TOOL_NAMES)
    assert names == (names & NEVER_BACKGROUND_TOOLS) | BACKGROUNDABLE_BUILTINS


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("end_call", True),
        (edge_tool_name("collect_details"), True),
        ("search_knowledge", False),
        ("lookup_policy", False),
    ],
)
def test_never_background(name: str, expected: bool) -> None:
    assert never_background(name) is expected


def test_tool_execution_is_exported() -> None:
    assert EXPORTED_MODELS["ToolExecution"] is ToolExecution
    for name in (
        "ToolExecution",
        "ToolExecutionMode",
        "DuplicatePolicy",
        "DuplicateScope",
        "BACKGROUNDABLE_BUILTINS",
        "NEVER_BACKGROUND_TOOLS",
        "never_background",
    ):
        assert name in lkap_contracts.__all__


# ------------------------------------------------------------------ V5-09: the MCP auth union
#: A row stored before V5-09: header auth expressed through the top-level fields.
_LEGACY_MCP_ROW: dict[str, Any] = {
    "kind": "mcp",
    "name": "crm",
    "url": "https://mcp.example.com/mcp",
    "headers": {"Authorization": "Bearer {{ secret.token }}"},
    "credential_id": "cred_1",
    "allowed_tools": None,
    "timeout_s": 5,
    "sse_read_timeout_s": 300,
    "tool_options": {},
    "origin": None,
}


def test_mcp_definition_without_auth_defaults_to_no_auth() -> None:
    definition = McpServerDefinition(name="docs", url="https://mcp.example.com/mcp")

    assert isinstance(definition.auth, McpNoAuth)
    assert definition.headers == {}
    assert definition.credential_id is None
    assert definition.cached_tools is None
    assert definition.cached_at is None


def test_mcp_legacy_row_loads_as_header_auth_and_resaves_in_the_new_shape() -> None:
    definition = McpServerDefinition.model_validate(_LEGACY_MCP_ROW)

    assert definition.auth == McpHeaderAuth(
        headers={"Authorization": "Bearer {{ secret.token }}"}, credential_id="cred_1"
    )
    dumped = definition.model_dump(mode="json")
    assert dumped["auth"] == {
        "kind": "header",
        "headers": {"Authorization": "Bearer {{ secret.token }}"},
        "credential_id": "cred_1",
    }
    # The deprecated mirrors stay in the dump for readers not on `auth` yet.
    assert dumped["headers"] == _LEGACY_MCP_ROW["headers"]
    assert dumped["credential_id"] == "cred_1"
    assert McpServerDefinition.model_validate(dumped) == definition


def test_mcp_legacy_row_loads_through_the_tool_union() -> None:
    definition = TypeAdapter(ToolDefinition).validate_python(_LEGACY_MCP_ROW)

    assert isinstance(definition, McpServerDefinition)
    assert isinstance(definition.auth, McpHeaderAuth)


def test_mcp_header_auth_fills_the_deprecated_mirrors() -> None:
    definition = McpServerDefinition(
        name="crm",
        url="https://mcp.example.com/mcp",
        auth=McpHeaderAuth(headers={"x-api-key": "{{ secret.key }}"}, credential_id="cred_2"),
    )

    assert definition.headers == {"x-api-key": "{{ secret.key }}"}
    assert definition.credential_id == "cred_2"


def test_mcp_legacy_fields_with_explicit_no_auth_fold_into_header_auth() -> None:
    definition = McpServerDefinition.model_validate({**_LEGACY_MCP_ROW, "auth": {"kind": "none"}})

    assert isinstance(definition.auth, McpHeaderAuth)
    assert definition.auth.credential_id == "cred_1"


@pytest.mark.parametrize(
    "legacy",
    [
        {"headers": {"Authorization": "Bearer other"}},
        {"credential_id": "cred_other"},
    ],
)
def test_mcp_legacy_fields_that_disagree_with_header_auth_are_an_error(legacy: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="set auth only"):
        McpServerDefinition.model_validate(
            {
                "name": "crm",
                "url": "https://mcp.example.com/mcp",
                "auth": {"kind": "header", "headers": {"Authorization": "Bearer x"}, "credential_id": "c"},
                **legacy,
            }
        )


def test_mcp_oauth_auth_mirrors_its_credential_and_refuses_static_headers() -> None:
    definition = McpServerDefinition(
        name="crm",
        url="https://mcp.example.com/mcp",
        auth=McpOAuthAuth(credential_id="cred_oauth", scopes=["read"]),
    )
    assert definition.credential_id == "cred_oauth"
    assert definition.headers == {}
    assert definition.auth.kind == "oauth"

    with pytest.raises(ValidationError, match="no static headers"):
        McpServerDefinition.model_validate(
            {
                "name": "crm",
                "url": "https://mcp.example.com/mcp",
                "auth": {"kind": "oauth"},
                "headers": {"Authorization": "Bearer x"},
            }
        )


def test_mcp_oauth_auth_defaults_match_the_research_shape() -> None:
    auth = McpOAuthAuth()

    assert auth.model_dump() == {
        "kind": "oauth",
        "credential_id": None,
        "registration": "auto",
        "client_id": None,
        "client_secret_ref": None,
        "scopes": None,
        "subject": "workspace",
    }


def test_mcp_unknown_auth_kind_is_rejected() -> None:
    with pytest.raises(ValidationError):
        McpServerDefinition.model_validate(
            {"name": "crm", "url": "https://mcp.example.com/mcp", "auth": {"kind": "basic"}}
        )


def test_mcp_cached_tools_round_trip() -> None:
    definition = McpServerDefinition.model_validate(
        {
            "name": "crm",
            "url": "https://mcp.example.com/mcp",
            "cached_tools": [
                {"name": "lookup", "description": "Look up", "input_schema": {"type": "object"}},
                {"name": "ping"},
            ],
            "cached_at": "2026-09-25T10:00:00Z",
        }
    )

    assert definition.cached_tools == [
        McpToolSnapshot(name="lookup", description="Look up", input_schema={"type": "object"}),
        McpToolSnapshot(name="ping"),
    ]
    assert McpServerDefinition.model_validate(definition.model_dump(mode="json")) == definition


def test_mcp_models_are_exported() -> None:
    for name in (
        "McpNoAuth",
        "McpHeaderAuth",
        "McpOAuthAuth",
        "McpToolSnapshot",
        "McpTestResult",
    ):
        assert name in EXPORTED_MODELS
        assert name in lkap_contracts.__all__
    assert "McpAuth" in lkap_contracts.__all__
    assert McpTestResult(ok=False, reason="needs_auth").tool_names == []
