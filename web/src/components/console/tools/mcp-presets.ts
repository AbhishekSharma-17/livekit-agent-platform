/**
 * MCP server presets (V5-21; T §5 = `docs/research-v4/tools-and-integrations.md` §5,
 * "Preset remote MCP servers"). The console owns this list — the api never reads it
 * (`docs/v5/PLAN-V5.md`'s V5-21 card): it exists only to prefill the "Add MCP server"
 * dialog with a known-good url, transport and auth options.
 *
 * `auth_matrix` members map onto `McpAuth.kind` (`@/contracts/lkap-contracts`):
 * - `"header"` — a static header, typically a vendor API key/PAT (`McpHeaderAuth`).
 * - `"oauth"` — "Sign in with the vendor": automatic client registration, no app to
 *   create first (`McpOAuthAuth{registration: "auto"}`).
 * - `"own_oauth"` — "Your own OAuth app": the vendor needs a pre-registered client id
 *   (`McpOAuthAuth{registration: "preregistered"}`); the dialog shows the return
 *   address to paste into the vendor's app settings.
 *
 * Endpoint and auth facts are the research doc's (accessed 2026-09-24, cited there per
 * vendor); they are a starting point, not a live check — `POST /v1/tools/{id}/test`
 * after saving is what actually proves the server answers. `status: "unverified"`
 * presets (Zendesk, Salesforce; D-V5-10) are never offered in the picker: their
 * endpoints are per-tenant or undocumented for a remote MCP server.
 */

export type McpPresetAuthMode = "header" | "oauth" | "own_oauth";

export interface McpPreset {
  id: string;
  name: string;
  url: string;
  transport: "streamable_http" | "sse" | "http";
  auth_matrix: McpPresetAuthMode[];
  scopes_hint?: string;
  allowed_tools_default: string[];
  notes?: string;
  docs_url: string;
  status: "available" | "unverified";
}

export const MCP_PRESETS: McpPreset[] = [
  {
    id: "github",
    name: "GitHub",
    url: "https://api.githubcopilot.com/mcp/",
    transport: "streamable_http",
    auth_matrix: ["oauth", "header"],
    scopes_hint: "Read-only mode is documented for the local server only. The remote server can write.",
    allowed_tools_default: [],
    notes: "A dev/ops integration, not something a caller-facing voice agent should use.",
    docs_url: "https://github.com/github/github-mcp-server/blob/main/README.md",
    status: "available",
  },
  {
    id: "linear",
    name: "Linear",
    url: "https://mcp.linear.app/mcp",
    transport: "streamable_http",
    auth_matrix: ["oauth", "header"],
    scopes_hint: "A read-only variant is at /mcp/readonly. API keys can be scoped to read.",
    allowed_tools_default: [],
    docs_url: "https://linear.app/docs/mcp",
    status: "available",
  },
  {
    id: "notion",
    name: "Notion",
    url: "https://mcp.notion.com/mcp",
    transport: "streamable_http",
    auth_matrix: ["oauth"],
    allowed_tools_default: [],
    notes: "No API-key option. Sign-in is the only way in.",
    docs_url: "https://developers.notion.com/docs/mcp",
    status: "available",
  },
  {
    id: "atlassian",
    name: "Atlassian (Jira, Confluence, Bitbucket, Loom)",
    url: "https://mcp.atlassian.com/v2/mcp",
    transport: "http",
    auth_matrix: ["oauth", "header"],
    scopes_hint: "Calls consume Rovo credits.",
    allowed_tools_default: [],
    docs_url: "https://support.atlassian.com/rovo/docs/getting-started-with-the-atlassian-remote-mcp-server/",
    status: "available",
  },
  {
    id: "stripe",
    name: "Stripe",
    url: "https://mcp.stripe.com",
    transport: "streamable_http",
    auth_matrix: ["header", "oauth"],
    scopes_hint: "An agent API key. Full/restricted keys stop being accepted 2026-10-31.",
    allowed_tools_default: [],
    notes: "Writes (refunds, payouts) ask a human to confirm through a link. Treat as background, not a voice reply.",
    docs_url: "https://docs.stripe.com/mcp",
    status: "available",
  },
  {
    id: "hubspot",
    name: "HubSpot",
    url: "https://mcp.hubspot.com",
    transport: "http",
    auth_matrix: ["own_oauth"],
    scopes_hint: "Excludes sensitive CRM data properties by default.",
    allowed_tools_default: [],
    docs_url: "https://developers.hubspot.com/ai-tools/mcp",
    status: "available",
  },
  {
    id: "google-workspace",
    name: "Google Workspace",
    url: "https://gmailmcp.googleapis.com/mcp/v1",
    transport: "streamable_http",
    auth_matrix: ["own_oauth"],
    scopes_hint: "Gmail, Calendar, Drive, Docs, Sheets, Slides, Chat and People each have their own endpoint.",
    allowed_tools_default: [],
    notes: "Developer Preview Program membership is required at Google's side. Edit the url for the app you're connecting.",
    docs_url: "https://developers.google.com/workspace/guides/configure-mcp-servers",
    status: "available",
  },
  {
    id: "slack",
    name: "Slack",
    url: "https://mcp.slack.com/mcp",
    transport: "streamable_http",
    auth_matrix: ["own_oauth"],
    scopes_hint: "Directory-published or internal apps only. The workspace admin approves the app.",
    allowed_tools_default: [],
    docs_url: "https://docs.slack.dev/ai/mcp-server/",
    status: "available",
  },
  {
    id: "zapier",
    name: "Zapier",
    url: "https://mcp.zapier.com/api/v1/connect",
    transport: "streamable_http",
    auth_matrix: ["own_oauth", "header"],
    scopes_hint: "One server per client. A connection token also works for a custom client.",
    allowed_tools_default: [],
    docs_url: "https://docs.zapier.com/mcp/overview",
    status: "available",
  },
  {
    id: "cloudflare",
    name: "Cloudflare",
    url: "https://mcp.cloudflare.com/mcp",
    transport: "streamable_http",
    auth_matrix: ["oauth", "header"],
    notes: "Several product-specific endpoints exist (docs, bindings, observability, radar, …). Edit the url for the one you need.",
    allowed_tools_default: [],
    docs_url: "https://developers.cloudflare.com/agents/model-context-protocol/mcp-servers-for-cloudflare/",
    status: "available",
  },
  {
    id: "cal-com",
    name: "Cal.com",
    url: "https://mcp.cal.com/mcp",
    transport: "streamable_http",
    auth_matrix: ["oauth"],
    notes: "The P0 booking pack (docs/v5/_asks.md, V5-25) uses the plain REST API with a key instead. This preset is for the fuller 34-tool server.",
    allowed_tools_default: [],
    docs_url: "https://cal.com/docs/mcp-server",
    status: "available",
  },
  {
    id: "sentry",
    name: "Sentry",
    url: "https://mcp.sentry.dev/mcp",
    transport: "http",
    auth_matrix: ["oauth"],
    allowed_tools_default: [],
    docs_url: "https://mcp.sentry.dev/",
    status: "available",
  },
  {
    id: "intercom",
    name: "Intercom",
    url: "https://mcp.intercom.com/mcp",
    transport: "streamable_http",
    auth_matrix: ["oauth", "header"],
    scopes_hint: "EU workspaces use https://mcp.eu.intercom.com/mcp.",
    allowed_tools_default: [],
    docs_url: "https://developers.intercom.com/docs/guides/mcp",
    status: "available",
  },
  {
    id: "paypal",
    name: "PayPal",
    url: "https://mcp.paypal.com/http",
    transport: "streamable_http",
    auth_matrix: ["oauth"],
    notes: "A sandbox server is at https://mcp.sandbox.paypal.com/http.",
    allowed_tools_default: [],
    docs_url: "https://developer.paypal.com/tools/mcp-server/",
    status: "available",
  },
  {
    id: "square",
    name: "Square",
    url: "https://mcp.squareup.com/mcp",
    transport: "streamable_http",
    auth_matrix: ["oauth"],
    notes: "Production accounts only on the remote server.",
    allowed_tools_default: [],
    docs_url: "https://developer.squareup.com/docs/mcp",
    status: "available",
  },
  {
    id: "asana",
    name: "Asana",
    url: "https://mcp.asana.com/v2/mcp",
    transport: "streamable_http",
    auth_matrix: ["oauth"],
    allowed_tools_default: [],
    docs_url: "https://developers.asana.com/docs/using-asanas-model-control-protocol-mcp-server",
    status: "available",
  },
  {
    id: "shopify-customer-accounts",
    name: "Shopify Customer Accounts",
    url: "https://{shop}/customer/api/mcp",
    transport: "http",
    auth_matrix: ["own_oauth"],
    scopes_hint: "customer-account-mcp-api:full",
    notes: "Replace {shop} with the store's domain. The shopper signs in, not the caller. Not for a phone call.",
    allowed_tools_default: [],
    docs_url: "https://shopify.dev/docs/apps/build/storefront-mcp/servers/customer-account",
    status: "available",
  },
  // Unverified (D-V5-10): never rendered in the picker. Endpoints are placeholders —
  // per-tenant or not documented as a remote MCP server yet.
  {
    id: "zendesk",
    name: "Zendesk",
    url: "https://example.zendesk.com/api/mcp",
    transport: "http",
    auth_matrix: ["oauth"],
    notes: "Replace the subdomain. Early access since 2026-06. Endpoint unverified against Zendesk's own docs.",
    allowed_tools_default: [],
    docs_url: "https://support.zendesk.com/hc/en-us/articles/10497779528730",
    status: "unverified",
  },
  {
    id: "salesforce",
    name: "Salesforce",
    url: "https://example.my.salesforce.com/mcp",
    transport: "http",
    auth_matrix: ["own_oauth"],
    notes: "Per-org endpoint, unverified against Salesforce's own docs (client-credentials OAuth, not a simple sign-in).",
    allowed_tools_default: [],
    docs_url: "https://www.salesforce.com/agentforce/mcp-support/",
    status: "unverified",
  },
];

/** Every preset offered in the picker (`status: "available"` only; D-V5-10). */
export const AVAILABLE_MCP_PRESETS: McpPreset[] = MCP_PRESETS.filter((preset) => preset.status === "available");

export function mcpPresetById(id: string): McpPreset | undefined {
  return MCP_PRESETS.find((preset) => preset.id === id);
}

export const MCP_PRESET_AUTH_LABEL: Record<McpPresetAuthMode, string> = {
  header: "Header (API key)",
  oauth: "Sign in with the vendor",
  own_oauth: "Your own OAuth app",
};
