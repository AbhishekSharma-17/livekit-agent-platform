"""D-V3-10 / R-V3-8: the hand-written docs must be accurate and sanitised.

Every provider id, tool name, block type, node kind, scope, route and model
name the docs mention must resolve against the real contracts, the generated
registry and the api's OpenAPI document; the docs must carry no real
hostname, IP literal, token name, `launch.json` reference or `other-project-agent`
(R-V2-3a's reason, restated for v3 by R-V3-8). Only RFC 2606 placeholder
hosts (`example.com`, `*.example`, `*.test`, …) and the literal
`<project>.livekit.cloud` are allowed.

**Written to run standalone, and still does.** This file was authored before
`mcp/pyproject.toml` and `lkap_mcp/server.py` existed — V3-01 (the MCP server
core) landed in the same wave as this card, in parallel, per
`docs/v3/PLAN-V3.md`, and both packages' work is reconciled here. So this
file:
  - imports `lkap_mcp.docs` (this package's own loader, `mcp/src/lkap_mcp/
    docs/__init__.py`) via a `sys.path` insert rather than relying on the
    package being installed, so it never needed V3-01's tree to run;
  - imports `lkap_contracts` the same way (a real runtime dependency of
    `lkap_mcp`, per the V3-01 card);
  - hardcodes the MCP tool catalog (`MCP_TOOLS` below) from
    `docs/v3/AGENT-ACCESS.md` §4, cross-checked against V3-01's real
    `lkap_mcp.catalog.declared_specs()` (its docstring: "for doc-lint,
    V3-03, and the snapshot, V3-05") by name and by exact input field name —
    see `test_hardcoded_tool_catalog_matches_registry_once_v301_lands` and
    `test_hardcoded_tool_fields_match_registry_signatures_once_v301_lands`.
    Both cross-checks pass as of this writing (60/60 tools, field-for-field);
    the hardcoded catalog stays because most of this file's other checks
    (recipe args, coverage) need it as plain data, not a live import, and
    because the file must still make sense read standalone;
  - imports `lkap_api.auth.roles.SCOPES` for the one bit of ground truth
    that lives only in the api and is never duplicated into `lkap_contracts`
    (`web/src/components/console/settings/api-types.ts` hand-duplicates it
    too — see `docs/v3/_asks.md`); this is safe because `lkap_api.auth.roles`
    has no import-time side effects (no app boot, no env vars needed),
    unlike `lkap_api.main`, which this file never imports;
  - hardcodes `OPENAPI_PATHS` (harvested once from a running `create_app()`,
    see the comment above it) rather than booting the api app at collection
    time, to keep this test fast and independent of `LIVEKIT_*`/`LKAP_*` env.

Runs as an ordinary test in the package's own gate:

    cd mcp && uv run pytest tests/test_docs_lint.py -q

and also runs standalone from the `api` package's venv (no `mcp` sdk, no
`httpx` needed there — every cross-check against V3-01's tree skips cleanly
when it is not importable). `--noconftest` skips `mcp/tests/conftest.py`
(V3-01/V3-02's scratch-api and MCP-client fixtures), which this file does
not use and the `api` venv cannot import:

    cd api && uv run pytest ../mcp/tests/test_docs_lint.py -q --noconftest
"""

from __future__ import annotations

import filecmp
import json
import re
import sys
from pathlib import Path
from typing import Any, Final, get_args

import pytest

# --------------------------------------------------------------------------- paths

TESTS_DIR = Path(__file__).resolve().parent
MCP_ROOT = TESTS_DIR.parent
REPO_ROOT = MCP_ROOT.parent
DOCS_DIR = MCP_ROOT / "src" / "lkap_mcp" / "docs"
CONCEPTS_DIR = DOCS_DIR / "concepts"
RECIPES_DIR = DOCS_DIR / "recipes"
GENERATED_DIR = MCP_ROOT / "src" / "lkap_mcp" / "generated"
CONTRACTS_GENERATED_DIR = REPO_ROOT / "contracts" / "generated"
README_PATH = REPO_ROOT / "README.md"
#: The api's starter-template catalogue (V4-01): its prose and JSON ship to every
#: workspace, so they get the same sanitisation checks as the docs (R-V3-8).
TEMPLATE_CATALOG_DIR = REPO_ROOT / "api" / "src" / "lkap_api" / "templates" / "catalog"

#: Files outside `docs/` that must pass the same sanitisation and identifier
#: checks (V3-08, R-V3-16, docs/v3/_asks.md): the packaged Claude Code skill,
#: its recipe copies (byte-identical to `mcp/src/lkap_mcp/docs/recipes/`,
#: `test_generated_dir_matches_contracts_generated`-style parity is asserted
#: separately below), and the repo-root Codex/generic guidance. Missing
#: entries are skipped, not an error.
_CLAUDE_PLUGIN_DIR: Final[Path] = MCP_ROOT / "claude-plugin"
EXTRA_LINT_FILES: Final[list[Path]] = [
    _CLAUDE_PLUGIN_DIR / "skills" / "lkap" / "SKILL.md",
    *sorted((_CLAUDE_PLUGIN_DIR / "skills" / "lkap" / "recipes").glob("*.md")),
    REPO_ROOT / "AGENTS.md",
    REPO_ROOT / "llms.txt",
]

# `lkap_mcp` and `lkap_contracts` are real dependencies of this package once
# `mcp/pyproject.toml` installs it (they are, as of V3-01) — but this file is
# also run standalone against a bare `src` checkout (see the module
# docstring), hence the explicit `sys.path` insert rather than relying only
# on the installed package.
sys.path.insert(0, str(MCP_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "contracts" / "src"))

import lkap_contracts.agent_config as agent_config  # noqa: E402
import lkap_contracts.blocks as blocks_module  # noqa: E402
import lkap_contracts.common as common  # noqa: E402
import lkap_contracts.connections as connections  # noqa: E402
import lkap_contracts.dispatch as dispatch  # noqa: E402
import lkap_contracts.fleet as fleet  # noqa: E402
import lkap_contracts.flow as flow_module  # noqa: E402
import lkap_contracts.migrate as migrate  # noqa: E402
import lkap_contracts.packs as packs_module  # noqa: E402
import lkap_contracts.pricing as pricing  # noqa: E402
import lkap_contracts.providers as providers_module  # noqa: E402
import lkap_contracts.qa as qa_module  # noqa: E402
import lkap_contracts.telephony as telephony  # noqa: E402
import lkap_contracts.templates as templates_module  # noqa: E402
import lkap_contracts.tools as tools_module  # noqa: E402
import lkap_contracts.ui_protocol as ui_protocol  # noqa: E402

from lkap_mcp import docs as loader  # noqa: E402

try:
    from lkap_api.auth.roles import SCOPES as _LIVE_SCOPES  # noqa: E402
except ImportError:  # pragma: no cover - only when not run from the api venv
    _LIVE_SCOPES = None

# --------------------------------------------------------------------------- ground truth

BLOCK_TYPES: Final[tuple[str, ...]] = get_args(ui_protocol.BlockType)
NODE_KINDS: Final[tuple[str, ...]] = get_args(flow_module.NodeKind)
BUILTIN_TOOL_NAMES: Final[tuple[str, ...]] = tools_module.BUILTIN_TOOL_NAMES
BLOCK_TOOL_NAMES: Final[tuple[str, ...]] = tools_module.BLOCK_TOOL_NAMES

#: The 12 known api-key scopes (`Scope`, `auth/roles.py`). Hardcoded as a
#: fallback so this test can run without the api venv; cross-checked against
#: the live import whenever it is importable, see the test below.
_FALLBACK_SCOPES: Final[frozenset[str]] = frozenset(
    {
        "*",
        "agents:read",
        "agents:write",
        "sessions:read",
        "sessions:write",
        "calls:write",
        "connections:read",
        "connections:write",
        "providers:read",
        "providers:write",
        "webhooks:write",
        "audit:read",
    }
)
SCOPES: Final[frozenset[str]] = frozenset(_LIVE_SCOPES) if _LIVE_SCOPES is not None else _FALLBACK_SCOPES

with open(GENERATED_DIR / "providers.json", encoding="utf-8") as _f:
    _PROVIDERS_DOC = json.load(_f)
PROVIDER_IDS: Final[frozenset[str]] = frozenset(p["id"] for p in _PROVIDERS_DOC["providers"])

#: From `packs/src/packs/{generic,insurance_claim}/manifest.py` (2026-09-24).
#: `mcp/` never depends on `packs` (it calls `GET /v1/packs`, never imports
#: pack code — that is the whole point of the pack/api boundary), so this is
#: a small, dated, hand-maintained fixture rather than a live import.
PACK_IDS: Final[frozenset[str]] = frozenset({"generic", "insurance_claim"})
PACK_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {"lookup_policy", "sync_claim_packet", "pin_evidence_photo", "draw_incident_sketch"}
)

#: Every model `contracts/src/lkap_contracts/export.py::EXPORTED_MODELS` exports
#: (harvested 2026-09-24; the sync test below catches drift against the
#: schema files actually shipped in `generated/schemas/`).
EXPORTED_MODEL_NAMES: Final[frozenset[str]] = frozenset(
    p.stem.removesuffix(".schema") for p in (GENERATED_DIR / "schemas").glob("*.schema.json")
)

#: Api-local models that are never exported to `lkap_contracts` (they live in
#: `api/src/lkap_api/auth/models.py`) but are still real, doc-worthy types.
EXTRA_API_MODEL_NAMES: Final[frozenset[str]] = frozenset(
    {
        "ApiKeySelfOut",
        "ApiKeySelfWorkspace",
        "ApiKeyOut",
        "ApiKeyCreate",
        "ApiKeyCreated",
        "AuditOut",
        "WorkspaceOut",
    }
)

#: Shapes that are not `lkap_contracts` models: `SecretInput`, `ResolvedSecret`
#: (`secrets.py`), `PlannedRequest`, `Untrusted`, `ToolResult`, `ErrorInfo`
#: (`results.py`) and `Patch` (`tools/agents.py`) are real V3-01 classes/type
#: aliases (verified 2026-09-24); `FastMCP` is the third-party server class
#: (`mcp.server.fastmcp`). None of these are `type` instances discoverable by
#: `_is_real_contract_type` (some are `Annotated[...]` aliases), so they are
#: named here once instead.
V3_TYPE_NAMES: Final[frozenset[str]] = frozenset(
    {
        "SecretInput",
        "ResolvedSecret",
        "ToolResult",
        "Untrusted",
        "PlannedRequest",
        "ErrorInfo",
        "Patch",
        "FastMCP",
    }
)

#: Contract modules searched (via `getattr`) for a PascalCase name not in the
#: exported-schema set — covers real `lkap_contracts` classes that are only
#: ever nested inside another model's schema (`PipelineConfig`, `VoiceConfig`,
#: …) rather than exported as their own top-level file.
_CONTRACTS_MODULES = (
    agent_config,
    connections,
    flow_module,
    tools_module,
    blocks_module,
    telephony,
    packs_module,
    providers_module,
    common,
    ui_protocol,
    dispatch,
    fleet,
    migrate,
    pricing,
    qa_module,
    templates_module,
)


def _is_real_contract_type(name: str) -> bool:
    return any(isinstance(getattr(mod, name, None), type) for mod in _CONTRACTS_MODULES)


#: Harvested once from `lkap_api.main.create_app(...).openapi()["paths"]`
#: (2026-09-24, HEAD 315b42f, after V3-00 landed) with `{param}` normalized
#: to `{}`. V3-05's scenario test and the contracts gate exercise the real
#: app; this test only needs to know a mentioned route is real, not spy on
#: every future one, so a hardcoded, dated snapshot is deliberate — booting
#: `lkap_api.main` here would need `LIVEKIT_*`/`LKAP_MASTER_KEY`/token env
#: just to import it, which this file has no reason to require.
_RAW_OPENAPI_PATHS: Final[tuple[str, ...]] = (
    "/hooks/livekit/{connection_id}",
    "/internal/v1/connections/{connection_id}/worker-env",
    "/internal/v1/fleet/desired",
    "/internal/v1/kb/search",
    "/internal/v1/sessions/start",
    "/internal/v1/sessions/{session_id}/events",
    "/internal/v1/sessions/{session_id}/metrics",
    "/internal/v1/sessions/{session_id}/qa",
    "/internal/v1/sessions/{session_id}/recording",
    "/internal/v1/sessions/{session_id}/recording/start",
    "/internal/v1/sessions/{session_id}/resolved",
    "/internal/v1/sessions/{session_id}/summary",
    "/internal/v1/telephony/calls/report",
    "/internal/v1/telephony/sessions/{session_id}/transfer",
    "/internal/v1/workers/register",
    "/internal/v1/workers/{instance_key}/heartbeat",
    "/v1/agents",
    "/v1/agents/{agent_id}",
    "/v1/agents/{agent_id}/archive",
    "/v1/agents/{agent_id}/flow/validate",
    "/v1/agents/{agent_id}/limits",
    "/v1/agents/{agent_id}/unarchive",
    "/v1/agents/{agent_id}/validate",
    "/v1/agents/{agent_id}/versions",
    "/v1/agents/{agent_id}/versions/{config_version}",
    "/v1/agents/{agent_id}/versions/{config_version}/restore",
    "/v1/agents/{id_or_slug}",
    "/v1/agents/{id_or_slug}/connect",
    "/v1/agents/{id_or_slug}/embed-policy",
    "/v1/agents/{id_or_slug}/text-sessions",
    "/v1/analytics/summary",
    "/v1/api-keys",
    "/v1/api-keys/self",
    "/v1/api-keys/{api_key_id}",
    "/v1/audit",
    "/v1/auth/accept-invite",
    "/v1/auth/login",
    "/v1/auth/logout",
    "/v1/auth/me",
    "/v1/auth/password",
    "/v1/calls",
    "/v1/calls/{call_id}",
    "/v1/calls/{call_id}/dtmf",
    "/v1/calls/{call_id}/hangup",
    "/v1/calls/{call_id}/transfer",
    "/v1/connections",
    "/v1/connections/test",
    "/v1/connections/{connection_id}",
    "/v1/connections/{connection_id}/default",
    "/v1/connections/{connection_id}/deploy-bundle",
    "/v1/connections/{connection_id}/fleet",
    "/v1/connections/{connection_id}/rotate",
    "/v1/connections/{connection_id}/test",
    "/v1/connections/{connection_id}/worker-env",
    "/v1/credentials",
    "/v1/credentials/{credential_id}",
    "/v1/credentials/{credential_id}/test",
    "/v1/flows/node-specs",
    "/v1/health",
    "/v1/knowledge-bases",
    "/v1/knowledge-bases/{kb_id}",
    "/v1/knowledge-bases/{kb_id}/documents",
    "/v1/knowledge-bases/{kb_id}/documents/import",
    "/v1/knowledge-bases/{kb_id}/documents/{document_id}",
    "/v1/knowledge-bases/{kb_id}/search",
    "/v1/packs",
    "/v1/providers",
    "/v1/providers/{provider_id}",
    "/v1/providers/{provider_id}/catalog",
    "/v1/providers/{provider_id}/settings",
    "/v1/sessions",
    "/v1/sessions/{session_id}",
    "/v1/sessions/{session_id}/events",
    "/v1/sessions/{session_id}/qa",
    "/v1/sessions/{session_id}/recording",
    "/v1/telephony/dispatch-rules",
    "/v1/telephony/dispatch-rules/{rule_id}",
    "/v1/telephony/numbers",
    "/v1/telephony/numbers/{number_id}",
    "/v1/telephony/trunks",
    "/v1/telephony/trunks/{trunk_id}",
    "/v1/telephony/trunks/{trunk_id}/sync",
    # V4-01: the starter templates (`lkap_api/templates/router.py`).
    "/v1/templates",
    "/v1/templates/{template_id}",
    # V5-18: connected apps (`lkap_api/tool_providers/router.py`).
    "/v1/tool-providers/composio/callback",
    "/v1/tool-providers/composio/connections",
    "/v1/tool-providers/composio/connections/{connection_id}",
    "/v1/tool-providers/composio/connections/{connection_id}/reconnect",
    "/v1/tool-providers/composio/disable",
    "/v1/tool-providers/composio/enable",
    "/v1/tool-providers/composio/key/test",
    "/v1/tool-providers/composio/materialise",
    "/v1/tool-providers/composio/status",
    "/v1/tool-providers/composio/toolkits",
    "/v1/tool-providers/composio/toolkits/{slug}",
    "/v1/tool-providers/composio/toolkits/{slug}/actions",
    "/v1/tool-providers/composio/tools/{tool_id}/refresh-schema",
    "/v1/tools",
    "/v1/tools/{tool_id}",
    "/v1/tools/{tool_id}/dry-run",
    "/v1/webhooks",
    "/v1/webhooks/deliveries/{delivery_id}/redeliver",
    "/v1/webhooks/{endpoint_id}",
    "/v1/webhooks/{endpoint_id}/deliveries",
    "/v1/webhooks/{endpoint_id}/test",
    "/v1/workspaces",
    "/v1/workspaces/{workspace_id}",
    "/v1/workspaces/{workspace_id}/invites",
    "/v1/workspaces/{workspace_id}/members",
    "/v1/workspaces/{workspace_id}/members/{user_id}",
    # V4-15: cost estimates, price quotes and workspace prices (`lkap_api/routers/costs.py`).
    "/v1/agents/{agent_id}/cost-estimate",
    "/v1/cost-estimates",
    "/v1/cost-estimates/assumptions",
    "/v1/pricing/quotes",
    "/v1/workspace/prices",
)


def _normalize_route(path: str) -> str:
    """Replace every `{param}` with `{}` so a doc's own param name doesn't matter."""
    return re.sub(r"\{[^}/]+\}", "{}", path)


OPENAPI_PATHS: Final[frozenset[str]] = frozenset(_normalize_route(p) for p in _RAW_OPENAPI_PATHS)

#: Env vars a doc may name (AGENT-ACCESS.md §6 `settings.py`, §9.2). Anything
#: else shaped like `LKAP_*`/`LIVEKIT_*` in backticks is very likely a typo.
ENV_VAR_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "LKAP_API_URL",
        "LKAP_API_KEY",
        "LKAP_WORKSPACE",
        "LKAP_MCP_READ_ONLY",
        "LKAP_MCP_INLINE_SECRETS",
        "LKAP_MCP_ALLOW_DIAL",
        "LKAP_MCP_CLIENT",
        "LKAP_MCP_MAX_CHATS",
        "LKAP_MCP_HTTP_HOST",
        "LKAP_MCP_HTTP_PORT",
        "LKAP_MCP_PUBLIC_URL",
        "LKAP_NET_ALLOW_PRIVATE_HOSTS",
        "LKAP_HTTP_TOOL_USER_AGENT",
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
    }
)

#: Non-provider, non-tool kebab-case tokens a doc is allowed to backtick.
#: Kept empty deliberately: every doc in this package was written avoiding
#: hyphenated English compounds inside backticks specifically so this list
#: stays empty and the kebab check stays meaningful. Add an entry here only
#: alongside a comment explaining why it is not a provider-id typo.
KEBAB_ALLOWLIST: Final[frozenset[str]] = frozenset()

#: The MCP tool catalog, hardcoded from `docs/v3/AGENT-ACCESS.md` §4 as
#: `{tool_name: {allowed top-level input field names}}`, cross-checked
#: field-for-field against the real `lkap_mcp.catalog.declared_specs()`
#: (V3-01) by `test_hardcoded_tool_fields_match_registry_signatures_once_v301_lands`
#: below — verified passing 60/60 tools as of 2026-09-24.
MCP_TOOLS: Final[dict[str, frozenset[str]]] = {
    # 4.1 discovery and identity
    "lkap_guide": frozenset(),
    "lkap_explain": frozenset({"topic"}),
    "lkap_describe": frozenset({"kind", "id"}),
    "lkap_search_docs": frozenset({"query", "limit"}),
    "me": frozenset(),
    "workspace_get": frozenset(),
    "activity": frozenset({"limit", "since", "mine", "action_prefix"}),
    # 4.2 connections and fleet
    "connection_list": frozenset(),
    "connection_get": frozenset({"id", "include_worker_env", "env_format"}),
    "connection_create": frozenset(
        {
            "name",
            "slug",
            "url",
            "api_key",
            "api_secret",
            "deployment_type",
            "agent_name",
            "deployment_mode",
            "worker_image",
            "use_inference",
            "is_default",
            "test_first",
            "plan",
        }
    ),
    "connection_test": frozenset({"id"}),
    "connection_fleet": frozenset({"id", "action", "replicas", "confirm", "plan"}),
    "connection_rotate": frozenset({"id", "api_key", "api_secret", "confirm", "plan"}),
    # 4.3 providers and keys
    "provider_list": frozenset({"kind", "enabled", "installed_on", "query", "availability"}),
    "provider_catalog": frozenset(
        {"provider_id", "kind", "key_id", "query", "limit", "offset", "model", "search_vendor", "refresh"}
    ),
    "provider_settings": frozenset({"provider_id", "enabled", "default_key_id", "plan"}),
    "provider_key_create": frozenset({"provider_id", "label", "secrets", "test", "plan"}),
    "provider_key_list": frozenset({"provider_id"}),
    "provider_key_test": frozenset({"key_id"}),
    "provider_test_model": frozenset(
        {"provider_id", "model", "key_id", "connection_id", "fields", "probes", "force", "plan"}
    ),
    "provider_model_declare": frozenset({"provider_id", "model", "capabilities", "plan"}),
    # 4.4 agents
    "agent_list": frozenset({"query", "mode", "published", "archived", "connection_id", "limit"}),
    "agent_get": frozenset({"id_or_slug", "include_config", "include_validation"}),
    "agent_create": frozenset(
        {"name", "template_id", "pack_id", "description", "connection_id", "config", "patch", "plan"}
    ),
    "agent_update": frozenset(
        {
            "id_or_slug",
            "patch",
            "config",
            "name",
            "description",
            "connection_id",
            "validate_first",
            "save_with_errors",
            "plan",
        }
    ),
    "agent_validate": frozenset({"id_or_slug"}),
    "agent_publish": frozenset({"id_or_slug", "published", "plan"}),
    "agent_archive": frozenset({"id_or_slug", "archive", "confirm"}),
    "agent_versions": frozenset({"id_or_slug", "get", "restore", "confirm", "plan"}),
    "agent_attach": frozenset({"id_or_slug", "kb_ids", "tool_ids", "remove", "plan"}),
    "agent_apps_mode": frozenset(
        {
            "id_or_slug",
            "mode",
            "allowed_toolkits",
            "denied_actions",
            "reviewed_actions",
            "router",
            "accounts",
            "plan",
        }
    ),
    "agent_limits": frozenset({"id_or_slug", "limits", "allowed_origins", "plan"}),
    "agent_flow_validate": frozenset({"id_or_slug", "flow"}),
    # 4.5 knowledge bases
    "kb_list": frozenset(),
    "kb_get": frozenset({"kb_id", "include_documents"}),
    "kb_create": frozenset({"name", "description", "embedder_id", "plan"}),
    "kb_add_document": frozenset(
        {"kb_id", "text", "filename", "file_path", "url", "wait", "timeout_s", "plan"}
    ),
    "kb_search": frozenset({"kb_id", "query", "top_k"}),
    # 4.6 tools
    "tool_list": frozenset({"agent_id", "kind"}),
    "tool_get": frozenset({"tool_id"}),
    "tool_create_http": frozenset(
        {
            "name",
            "description",
            "parameters",
            "url",
            "method",
            "headers",
            "body_template",
            "allowed_hosts",
            "result_path",
            "timeout_s",
            "max_result_chars",
            "silent_reply",
            "secret_key_id",
            "agent_id",
            "dry_run_args",
            "execution",
            "plan",
        }
    ),
    "tool_create_mcp": frozenset(
        {
            "name",
            "url",
            "auth",
            "headers",
            "allowed_tools",
            "secret_key_id",
            "timeout_s",
            "agent_id",
            "tool_options",
            "plan",
        }
    ),
    "tool_update": frozenset({"tool_id", "patch", "execution", "plan"}),
    "tool_test": frozenset({"tool_id"}),
    "tool_dry_run": frozenset({"tool_id", "arguments"}),
    # 4.7 test chat
    "chat_start": frozenset({"agent_id_or_slug", "participant_name", "wait_for_greeting", "timeout_s"}),
    "chat_send": frozenset({"chat_id", "text", "timeout_s"}),
    "chat_rewind": frozenset({"chat_id", "turn_index", "replace_text", "timeout_s"}),
    "chat_end": frozenset({"chat_id"}),
    # 4.8 sessions, transcripts and qa
    "session_list": frozenset({"agent_id", "status", "channel", "connection_id", "since", "until", "limit"}),
    "session_get": frozenset({"session_id", "include_transcript", "include_recording_url"}),
    "session_events": frozenset({"session_id", "after_id", "types", "limit"}),
    "session_rescore": frozenset({"session_id", "confirm"}),
    # V4-15 costs (docs/v4/COSTS.md §6)
    "cost_estimate": frozenset(
        {
            "agent_id_or_slug",
            "template_id",
            "config",
            "session_minutes",
            "assumptions",
            "channel",
            "workspace_averages",
        }
    ),
    "pricing_quote": frozenset({"provider_id", "model"}),
    "cost_summary": frozenset({"range"}),
    # 4.9 webhooks
    "webhook_list": frozenset(),
    "webhook_create": frozenset({"url", "events", "description", "plan"}),
    "webhook_test": frozenset({"endpoint_id"}),
    "webhook_deliveries": frozenset({"endpoint_id", "redeliver", "limit"}),
    # 4.10 telephony
    "telephony_overview": frozenset(),
    "call_list": frozenset({"direction", "status", "session_id", "limit"}),
    "call_get": frozenset({"call_id"}),
    "call_place": frozenset({"agent_id", "to_e164", "trunk_id", "variables", "confirm", "plan"}),
    "call_control": frozenset({"call_id", "action", "digits", "to", "confirm"}),
    # 4.11 generic
    "lkap_delete": frozenset({"kind", "id", "parent_id", "purge", "confirm", "plan"}),
    "api_request": frozenset({"method", "path", "query", "body", "confirm", "plan"}),
    # V5-18: connected apps (Composio)
    "apps_list": frozenset({"query", "category", "connected_only", "cursor", "limit"}),
    "apps_actions": frozenset({"toolkit", "query", "important", "cursor"}),
    "apps_connect": frozenset({"toolkit", "method", "subject", "agent_id", "fields", "alias", "plan"}),
    "apps_connections": frozenset(),
    "apps_connection_status": frozenset({"id"}),
    "apps_connection_rename": frozenset({"id", "label", "plan"}),
    "apps_connection_set_default": frozenset({"id", "plan"}),
    "apps_disconnect": frozenset({"id", "purge", "confirm", "plan"}),
    "apps_add_tools": frozenset({"connection_id", "actions", "agent_id", "allow_destructive", "plan"}),
}

ALL_CALLABLE_NAMES: Final[frozenset[str]] = (
    frozenset(MCP_TOOLS) | frozenset(BUILTIN_TOOL_NAMES) | frozenset(BLOCK_TOOL_NAMES) | PACK_TOOL_NAMES
)

FORBIDDEN_SUBSTRINGS: Final[tuple[str, ...]] = (
    "other-project-agent",
    "example-cloud",
    "dev-admin",
    "dev-service",
    "launch.json",
    "livekit.cloud/",
)

_IPV4_RE = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
_URL_RE = re.compile(r"(?:https?|wss?)://([^/\s\"'`\)]+)")
#: CommonMark treats a line ending inside a code span as a space, so a span
#: legitimately wraps a markdown line (`` `agent_create(name,\npatch=None)` ``
#: renders as one span) — this must match `re.DOTALL` to see it. Fenced
#: ` ```code``` ` blocks are stripped first (see `_backtick_tokens`) so this
#: never mismatches a fence's own backticks as an inline span.
_BACKTICK_RE = re.compile(r"`([^`]+)`", re.DOTALL)
_FENCED_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_WHITESPACE_RUN_RE = re.compile(r"\s+")
_CALL_TOKEN_RE = re.compile(r"^([a-z_][a-z0-9_]*)\(")
_PASCAL_TOKEN_RE = re.compile(r"^[A-Z][a-z][A-Za-z0-9]*$")
_KEBAB_TOKEN_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)+$")
_SCOPE_TOKEN_RE = re.compile(r"^[a-z][a-z_]*:[a-z*]+$")
_ROUTE_TOKEN_RE = re.compile(r"^(GET|POST|PUT|DELETE|PATCH) (/\S+)$")
_ENV_TOKEN_RE = re.compile(r"^(LKAP|LIVEKIT)_[A-Z0-9_]+$")


def _allowed_host(host: str) -> bool:
    host = host.split(":", 1)[0]  # drop a port, if any
    if host == "<project>.livekit.cloud":
        return True
    if host in {"example.com", "example.org", "example.net", "example"}:
        return True
    if host.endswith((".example.com", ".example.org", ".example.net")):
        return True
    if host.endswith((".example", ".test", ".invalid", ".localhost")):
        return True
    return host == "localhost"


# --------------------------------------------------------------------------- doc collection


def _all_doc_paths() -> list[Path]:
    paths = [DOCS_DIR / "guide.md", DOCS_DIR / "blocks.md"]
    paths += sorted(CONCEPTS_DIR.glob("*.md"))
    paths += sorted(RECIPES_DIR.glob("*.md"))
    paths += [p for p in EXTRA_LINT_FILES if p.exists()]
    return paths


DOC_PATHS: Final[list[Path]] = _all_doc_paths()

#: The starter catalogue's `instructions.md`, `seeds/*.md` and `template.json` files:
#: sanitisation only (they are prompts and sample content, not docs, so the
#: identifier checks do not apply — `check-up` is a service name, not a provider id).
CATALOG_PATHS: Final[list[Path]] = sorted(
    [*TEMPLATE_CATALOG_DIR.rglob("*.md"), *TEMPLATE_CATALOG_DIR.rglob("*.json")]
)
SANITISED_PATHS: Final[list[Path]] = [*DOC_PATHS, *CATALOG_PATHS]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- sanitisation (R-V3-8)


def _lint_id(path: Path) -> str:
    return str(path.relative_to(TEMPLATE_CATALOG_DIR)) if TEMPLATE_CATALOG_DIR in path.parents else path.name


def test_the_template_catalogue_is_linted() -> None:
    assert {p.name for p in CATALOG_PATHS} >= {"template.json", "instructions.md", "product_faq.md"}


@pytest.mark.parametrize("path", SANITISED_PATHS, ids=_lint_id)
def test_no_forbidden_substrings(path: Path) -> None:
    text = _read(path)
    for needle in FORBIDDEN_SUBSTRINGS:
        assert needle not in text, f"{path.name} contains forbidden text {needle!r}"


@pytest.mark.parametrize("path", SANITISED_PATHS, ids=_lint_id)
def test_no_ip_literals(path: Path) -> None:
    text = _read(path)
    hits = _IPV4_RE.findall(text)
    assert not hits, f"{path.name} contains an IPv4 literal: {hits}"


@pytest.mark.parametrize("path", SANITISED_PATHS, ids=_lint_id)
def test_urls_use_only_placeholder_hosts(path: Path) -> None:
    text = _read(path)
    for host in _URL_RE.findall(text):
        assert _allowed_host(host), (
            f"{path.name} references host {host!r}; only RFC 2606 placeholders "
            "(example.com, *.example, *.test, …) and the literal <project>.livekit.cloud are allowed"
        )


_TOKEN_NAME_RE = re.compile(r"\b(?:LKAP|LIVEKIT)_[A-Z0-9_]+\b|\{\{\s*secret\.")


@pytest.mark.parametrize("path", CATALOG_PATHS, ids=_lint_id)
def test_catalogue_names_no_token_or_env_var(path: Path) -> None:
    """Starter prose and tool seeds never name a platform env var, a token or a secret placeholder."""
    hits = _TOKEN_NAME_RE.findall(_read(path))
    assert not hits, f"{_lint_id(path)} names {hits}"


# --------------------------------------------------------------------------- template ids (V4-01)

_TEMPLATE_ID_RES: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r'"template_id"\s*:\s*"([^"]+)"'),
    re.compile(r'template_id\s*=\s*"([^"]+)"'),
)


def _template_ids_mentioned() -> set[str]:
    found: set[str] = set()
    for path in DOC_PATHS:
        text = _read(path)
        for pattern in _TEMPLATE_ID_RES:
            found.update(pattern.findall(text))
    return found


def test_recipes_mention_template_ids() -> None:
    assert {"blank", "insurance_claim", "receptionist"} <= _template_ids_mentioned()


@pytest.fixture
def scratch_admin(request: pytest.FixtureRequest) -> Any:
    """The scratch api's admin client from ``conftest.py``; skipped under ``--noconftest``."""
    try:
        return request.getfixturevalue("admin")
    except pytest.FixtureLookupError:
        pytest.skip("the scratch api fixtures (mcp/tests/conftest.py) are not loaded")


async def test_every_template_id_in_the_docs_resolves_against_the_scratch_api(scratch_admin: Any) -> None:
    response = await scratch_admin.get("/v1/templates")
    assert response.status_code == 200, response.text
    live = {item["template"]["id"] for item in response.json()["items"]}

    unknown = _template_ids_mentioned() - live
    assert not unknown, f"docs name template id(s) the api does not serve: {sorted(unknown)}"


# --------------------------------------------------------------------------- identifier resolution (D-V3-10)


def _backtick_tokens(path: Path) -> list[str]:
    """Every inline code span, fenced ```code``` blocks stripped first.

    A span that wrapped a markdown line comes back with the line ending
    collapsed to a single space, per CommonMark's code-span rule.
    """
    text_without_fences = _FENCED_BLOCK_RE.sub(" ", _read(path))
    return [_WHITESPACE_RUN_RE.sub(" ", t).strip() for t in _BACKTICK_RE.findall(text_without_fences)]


@pytest.mark.parametrize("path", DOC_PATHS, ids=lambda p: p.name)
def test_tool_call_mentions_resolve(path: Path) -> None:
    for token in _backtick_tokens(path):
        match = _CALL_TOKEN_RE.match(token)
        if not match:
            continue
        name = match.group(1)
        assert name in ALL_CALLABLE_NAMES, f"{path.name}: `{token}` calls an unknown tool {name!r}"


@pytest.mark.parametrize("path", DOC_PATHS, ids=lambda p: p.name)
def test_route_mentions_resolve(path: Path) -> None:
    for token in _backtick_tokens(path):
        match = _ROUTE_TOKEN_RE.match(token)
        if not match:
            continue
        route = _normalize_route(match.group(2))
        assert route in OPENAPI_PATHS, f"{path.name}: route `{token}` does not match any real api route"


@pytest.mark.parametrize("path", DOC_PATHS, ids=lambda p: p.name)
def test_provider_id_mentions_resolve(path: Path) -> None:
    for token in _backtick_tokens(path):
        if not _KEBAB_TOKEN_RE.match(token):
            continue
        # A doc's own recipe/concept names are also kebab-case (`connect-livekit`,
        # `pipeline-modes`); they are a real, checked identifier space (every
        # loader.recipe()/concept() call already validates them), not a typo bucket.
        resolved = (
            token in PROVIDER_IDS
            or token in KEBAB_ALLOWLIST
            or token in loader.RECIPE_NAMES
            or token in loader.CONCEPT_TOPICS
        )
        assert resolved, (
            f"{path.name}: `{token}` looks like a provider id but is not in the registry, "
            "a recipe/concept name, or KEBAB_ALLOWLIST"
        )


@pytest.mark.parametrize("path", DOC_PATHS, ids=lambda p: p.name)
def test_scope_mentions_resolve(path: Path) -> None:
    for token in _backtick_tokens(path):
        if not _SCOPE_TOKEN_RE.match(token):
            continue
        assert token in SCOPES, f"{path.name}: `{token}` is not a real scope"


@pytest.mark.parametrize("path", DOC_PATHS, ids=lambda p: p.name)
def test_model_name_mentions_resolve(path: Path) -> None:
    for token in _backtick_tokens(path):
        if not _PASCAL_TOKEN_RE.match(token):
            continue
        resolved = (
            token in EXPORTED_MODEL_NAMES
            or token in EXTRA_API_MODEL_NAMES
            or token in V3_TYPE_NAMES
            or _is_real_contract_type(token)
        )
        assert resolved, f"{path.name}: `{token}` does not resolve to any known model or type"


@pytest.mark.parametrize("path", DOC_PATHS, ids=lambda p: p.name)
def test_env_var_mentions_resolve(path: Path) -> None:
    for token in _backtick_tokens(path):
        if not _ENV_TOKEN_RE.match(token):
            continue
        assert token in ENV_VAR_ALLOWLIST, f"{path.name}: `{token}` is not a documented env var"


# --------------------------------------------------------------------------- coverage (D-V3-10, R-V3-8)


def test_every_block_type_covered_in_panels_doc() -> None:
    tokens = set(_backtick_tokens(CONCEPTS_DIR / "panels-and-blocks.md"))
    missing = set(BLOCK_TYPES) - tokens
    assert not missing, f"panels-and-blocks.md is missing block type(s): {sorted(missing)}"


def test_every_node_kind_covered_in_flows_doc() -> None:
    tokens = set(_backtick_tokens(CONCEPTS_DIR / "flows.md"))
    missing = set(NODE_KINDS) - tokens
    assert not missing, f"flows.md is missing node kind(s): {sorted(missing)}"


def test_every_scope_covered_in_roles_doc() -> None:
    tokens = set(_backtick_tokens(CONCEPTS_DIR / "roles-and-scopes.md"))
    missing = SCOPES - tokens
    assert not missing, f"roles-and-scopes.md is missing scope(s): {sorted(missing)}"


def test_every_pack_id_mentioned_somewhere() -> None:
    mentioned: set[str] = set()
    for path in DOC_PATHS:
        mentioned.update(_backtick_tokens(path))
    missing = PACK_IDS - mentioned
    assert not missing, f"no doc mentions pack id(s): {sorted(missing)}"


def test_every_mcp_tool_mentioned_with_call_syntax() -> None:
    called: set[str] = set()
    for path in DOC_PATHS:
        for token in _backtick_tokens(path):
            match = _CALL_TOKEN_RE.match(token)
            if match:
                called.add(match.group(1))
    missing = set(MCP_TOOLS) - called
    assert not missing, f"no doc calls tool(s) with `name(...)` syntax: {sorted(missing)}"


# --------------------------------------------------------------------------- recipe args vs. tool fields

_JSON_BLOCK_RE = re.compile(r"```json\n(.*?)\n```", re.DOTALL)


def _pair_calls_with_json(text: str) -> list[tuple[str, str, dict]]:
    """Every ```json block paired with the nearest *preceding* ``name(`` mention."""
    tokens: list[tuple[int, str, str]] = []
    for match in re.finditer(r"`([a-z_][a-z0-9_]*)\(", text):
        tokens.append((match.start(), "call", match.group(1)))
    for match in _JSON_BLOCK_RE.finditer(text):
        tokens.append((match.start(), "json", match.group(1)))
    tokens.sort(key=lambda t: t[0])

    pairs: list[tuple[str, str, dict]] = []
    last_call: str | None = None
    for _, kind, value in tokens:
        if kind == "call":
            last_call = value
            continue
        if last_call is None:
            continue
        data = json.loads(value)  # a syntax error here is itself a real doc bug
        if isinstance(data, dict):
            pairs.append((last_call, value, data))
        last_call = None
    return pairs


@pytest.mark.parametrize("name", loader.RECIPE_NAMES)
def test_recipe_json_args_match_tool_fields(name: str) -> None:
    text = loader.recipe(name)
    for tool_name, _raw, args in _pair_calls_with_json(text):
        allowed = MCP_TOOLS.get(tool_name)
        if allowed is None:
            continue  # not a top-level MCP tool call (e.g. a nested FlowSpec/panel value)
        extra = set(args) - allowed
        assert not extra, (
            f"{name}.md: `{tool_name}(...)` example has field(s) {sorted(extra)} not in its "
            f"input model; allowed={sorted(allowed)}"
        )


# --------------------------------------------------------------------------- shape


@pytest.mark.parametrize("path", sorted(CONCEPTS_DIR.glob("*.md")), ids=lambda p: p.name)
def test_concept_doc_is_short_and_ends_with_related_sections(path: Path) -> None:
    text = _read(path)
    lines = text.splitlines()
    assert len(lines) <= 250, f"{path.name} is {len(lines)} lines, over the 250-line cap"
    assert re.search(r"^## Related tools$", text, re.MULTILINE), f"{path.name} has no '## Related tools'"
    assert re.search(r"^## Related schemas$", text, re.MULTILINE), f"{path.name} has no '## Related schemas'"


def test_every_concept_topic_and_recipe_and_prompt_loads() -> None:
    loader.guide()
    for topic in loader.CONCEPT_TOPICS:
        assert loader.concept(topic)
    for name in loader.RECIPE_NAMES:
        assert loader.recipe(name)
    for name in loader.PROMPT_NAMES:
        prompt = loader.prompt(name)
        assert prompt.render()  # renders with every argument blank, without raising


def test_guide_is_within_token_budget() -> None:
    text = loader.guide()
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        n_tokens = len(enc.encode(text))
    except ImportError:
        n_tokens = len(text) // 4  # a conservative stand-in ratio for English prose
    assert n_tokens < 1800, f"guide.md is ~{n_tokens} tokens, over the 1,800 budget"


# --------------------------------------------------------------------------- generated sync (D-V3-10)


def test_generated_dir_matches_contracts_generated() -> None:
    for name in ("providers.json", "builtin_tools.json"):
        mcp_copy = GENERATED_DIR / name
        source = CONTRACTS_GENERATED_DIR / name
        assert filecmp.cmp(mcp_copy, source, shallow=False), f"{name} is out of sync with contracts/generated"

    mcp_schemas = {p.name for p in (GENERATED_DIR / "schemas").glob("*.schema.json")}
    source_schemas = {p.name for p in (CONTRACTS_GENERATED_DIR / "schemas").glob("*.schema.json")}
    assert mcp_schemas == source_schemas, (
        f"schema file sets differ: only in mcp={mcp_schemas - source_schemas}, "
        f"only in contracts={source_schemas - mcp_schemas}"
    )
    for name in mcp_schemas:
        mcp_schema = GENERATED_DIR / "schemas" / name
        source_schema = CONTRACTS_GENERATED_DIR / "schemas" / name
        assert filecmp.cmp(mcp_schema, source_schema, shallow=False), (
            f"schemas/{name} is out of sync with contracts/generated"
        )


def test_skill_recipes_match_source_recipes() -> None:
    """V3-08 (R-V3-16): the skill's recipes have one source, this package's own.

    `scripts/export_contracts.sh` copies `RECIPES_DIR` byte-identical into
    `mcp/claude-plugin/skills/lkap/recipes/`; skipped (not failed) until
    V3-08's plugin directory exists, exactly like `EXTRA_LINT_FILES` above.
    """
    skill_recipes_dir = _CLAUDE_PLUGIN_DIR / "skills" / "lkap" / "recipes"
    if not skill_recipes_dir.is_dir():
        pytest.skip("mcp/claude-plugin/skills/lkap/recipes not present (V3-08 not landed yet)")

    source_names = {p.name for p in RECIPES_DIR.glob("*.md")}
    copy_names = {p.name for p in skill_recipes_dir.glob("*.md")}
    assert copy_names == source_names, (
        f"recipe copy set differs from the source: only in copy={copy_names - source_names}, "
        f"only in source={source_names - copy_names}"
    )
    for name in source_names:
        assert filecmp.cmp(RECIPES_DIR / name, skill_recipes_dir / name, shallow=False), (
            f"skills/lkap/recipes/{name} is out of sync with docs/recipes/{name} "
            "— re-run scripts/export_contracts.sh"
        )


# --------------------------------------------------------------------------- README


def test_readme_documents_connecting_an_ai_agent() -> None:
    text = README_PATH.read_text(encoding="utf-8")
    assert re.search(r"^## Connect an AI coding agent$", text, re.MULTILINE), (
        "README.md is missing the '## Connect an AI coding agent' section"
    )


# --------------------------------------------------------------------------- forward-compat sync checks


def _real_declared_specs() -> list[Any] | None:
    """``lkap_mcp.catalog.declared_specs()`` when V3-01's tree is importable, else ``None``.

    ``catalog.py`` exists specifically "for doc-lint (V3-03) and the snapshot
    (V3-05)" per its own module docstring — this is that consumer.
    """
    try:
        from lkap_mcp import catalog as real_catalog  # type: ignore[import-not-found]
    except ImportError:
        return None
    return list(real_catalog.declared_specs())


def test_hardcoded_tool_catalog_matches_registry_once_v301_lands() -> None:
    specs = _real_declared_specs()
    if specs is None:
        pytest.skip("lkap_mcp.catalog not importable in this environment (run from mcp/'s own venv)")
    real_names = {spec.name for spec in specs}
    assert set(MCP_TOOLS) == real_names, (
        f"MCP_TOOLS has drifted from the real registry: only in docs={set(MCP_TOOLS) - real_names}, "
        f"only in registry={real_names - set(MCP_TOOLS)}"
    )


def test_hardcoded_tool_fields_match_registry_signatures_once_v301_lands() -> None:
    specs = _real_declared_specs()
    if specs is None:
        pytest.skip("lkap_mcp.catalog not importable in this environment (run from mcp/'s own venv)")
    import inspect

    mismatches = []
    for spec in specs:
        real_fields = frozenset(inspect.signature(spec.fn).parameters)
        doc_fields = MCP_TOOLS.get(spec.name)
        if doc_fields is not None and real_fields != doc_fields:
            mismatches.append((spec.name, sorted(real_fields - doc_fields), sorted(doc_fields - real_fields)))
    assert not mismatches, "field-name drift (tool, real-only, doc-only): " + repr(mismatches)


def test_hardcoded_scopes_match_live_import_when_available() -> None:
    if _LIVE_SCOPES is None:
        pytest.skip("lkap_api.auth.roles not importable in this environment")
    assert _FALLBACK_SCOPES == frozenset(_LIVE_SCOPES), (
        "the hardcoded SCOPES fallback has drifted from auth/roles.py"
    )
