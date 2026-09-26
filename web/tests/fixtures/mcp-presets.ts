import type {
  McpOauthStartOut,
  McpOauthStatusOut,
  McpServerDefinition,
  McpTestResult,
  ToolOut,
} from "@/contracts/lkap-contracts";

/**
 * Fixtures for MCP presets and OAuth sign-in (V5-21): `console-mcp-presets.test.tsx` and
 * the sign-in / test-connection additions to `console-mcp-tool-editor.test.tsx`. Shapes
 * mirror `@/contracts/lkap-contracts` exactly — no field is invented here.
 */

export function mcpOauthDefinition(overrides: Partial<McpServerDefinition> = {}): McpServerDefinition {
  return {
    kind: "mcp",
    name: "linear",
    url: "https://mcp.linear.app/mcp",
    auth: { kind: "oauth", registration: "auto", credential_id: null, client_id: null, client_secret_ref: null, scopes: null, subject: "workspace" },
    headers: {},
    credential_id: null,
    allowed_tools: null,
    timeout_s: 5,
    sse_read_timeout_s: 300,
    tool_options: {},
    cached_tools: null,
    cached_at: null,
    ...overrides,
  };
}

export function mcpOauthTool(overrides: Partial<ToolOut> = {}, definitionOverrides: Partial<McpServerDefinition> = {}): ToolOut {
  return {
    id: "tool_oauth_1",
    agent_id: null,
    kind: "mcp",
    name: "linear",
    definition: mcpOauthDefinition(definitionOverrides),
    enabled: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

export function mcpOauthStatus(overrides: Partial<McpOauthStatusOut> = {}): McpOauthStatusOut {
  return { status: "not_connected", scopes: [], worker_supported: true, ...overrides };
}

export function mcpOauthStartRedirect(overrides: Partial<McpOauthStartOut> = {}): McpOauthStartOut {
  return {
    status: "redirect",
    authorization_url: "https://mcp.linear.app/authorize?flow=1",
    redirect_uri: "https://api.example.com/v1/oauth/mcp/callback",
    expires_at: "2026-01-01T00:10:00Z",
    issuer: "https://mcp.linear.app",
    registration: "dcr",
    ...overrides,
  };
}

export function mcpOauthStartNeedsRegistration(overrides: Partial<McpOauthStartOut> = {}): McpOauthStartOut {
  return {
    status: "needs_client_registration",
    authorization_url: null,
    redirect_uri: "https://api.example.com/v1/oauth/mcp/callback",
    expires_at: null,
    issuer: "https://slack.example.com",
    registration: null,
    ...overrides,
  };
}

export function mcpTestResultOk(overrides: Partial<McpTestResult> = {}): McpTestResult {
  return {
    ok: true,
    tool_names: ["list_issues", "create_issue", "search_issues"],
    tool_count: 3,
    duration_ms: 42,
    cached_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

export function mcpTestResultFailed(overrides: Partial<McpTestResult> = {}): McpTestResult {
  return {
    ok: false,
    tool_names: [],
    tool_count: 0,
    duration_ms: 12,
    reason: "needs_auth",
    error: "sign in to this server first",
    ...overrides,
  };
}
