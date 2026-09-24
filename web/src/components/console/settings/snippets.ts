/**
 * Pure string builders for the "Connect an AI agent" dialog
 * (`connect-agent-dialog.tsx`), plus the preset/client/expiry constants the
 * dialog's Step 1 renders. Kept side-effect free and framework-free so
 * `console-settings-snippets.test.ts` can assert on exact output without
 * mounting React.
 *
 * docs/v3/AGENT-ACCESS.md §5 (console) and R-V3-9/R-V3-17: the git remote
 * doesn't exist yet, so every local install line uses
 * `uv run --project <checkout>/mcp lkap-mcp` with `<checkout>` as a literal
 * placeholder the user fills in — never a real path on this machine.
 */

import type { Scope } from "./api-types";

// ---------------------------------------------------------------- presets

export type AgentKeyPresetId = "read_only" | "builder" | "operator";

export interface AgentKeyPreset {
  id: AgentKeyPresetId;
  label: string;
  /** One line shown under the radio card. */
  description: string;
  scopes: Scope[];
}

const READ_ONLY_SCOPES: Scope[] = ["agents:read", "sessions:read", "connections:read", "providers:read", "audit:read"];
const BUILDER_SCOPES: Scope[] = [...READ_ONLY_SCOPES, "agents:write", "sessions:write"];
const OPERATOR_SCOPES: Scope[] = [...BUILDER_SCOPES, "connections:write", "providers:write", "webhooks:write"];

/** R-V3-9: the three console presets. `calls:write` is never part of a preset — it's the separate checkbox. */
export const AGENT_KEY_PRESETS: AgentKeyPreset[] = [
  {
    id: "read_only",
    label: "Read-only",
    description: "Look around: agents, sessions, connections, providers and activity.",
    scopes: READ_ONLY_SCOPES,
  },
  {
    id: "builder",
    label: "Builder",
    description: "Read-only, plus build and test: agents, knowledge bases, tools, test chat.",
    scopes: BUILDER_SCOPES,
  },
  {
    id: "operator",
    label: "Operator",
    description: "Builder, plus connections, provider keys and webhooks.",
    scopes: OPERATOR_SCOPES,
  },
];

export const DEFAULT_AGENT_KEY_PRESET: AgentKeyPresetId = "builder";

/** The separate, off-by-default checkbox (D-V3-8, R-V3-7): outbound phone calls. */
export const CALLS_WRITE_SCOPE: Scope = "calls:write";

export function presetById(id: AgentKeyPresetId): AgentKeyPreset {
  const preset = AGENT_KEY_PRESETS.find((p) => p.id === id);
  if (!preset) throw new Error(`unknown agent key preset '${id}'`);
  return preset;
}

// ---------------------------------------------------------------- clients

export type AgentKeyClientId = "claude-code" | "codex" | "cursor" | "other";

export const AGENT_KEY_CLIENTS: { id: AgentKeyClientId; label: string }[] = [
  { id: "claude-code", label: "Claude Code" },
  { id: "codex", label: "Codex CLI" },
  { id: "cursor", label: "Cursor" },
  { id: "other", label: "Other MCP client" },
];

export const DEFAULT_AGENT_KEY_CLIENT: AgentKeyClientId = "claude-code";

/** The snippet block to preselect for a chosen client ("other" has no dedicated tab — Cursor's generic JSON form fits it). */
export type SnippetClientId = "claude-code" | "codex" | "cursor";

export function snippetClientFor(client: AgentKeyClientId): SnippetClientId {
  return client === "other" ? "cursor" : client;
}

export const SNIPPET_TABS: { id: SnippetClientId; label: string }[] = [
  { id: "claude-code", label: "Claude Code" },
  { id: "codex", label: "Codex CLI" },
  { id: "cursor", label: "Cursor / generic" },
];

// ---------------------------------------------------------------- expiry

export const AGENT_KEY_EXPIRY_OPTIONS: { days: number; label: string }[] = [
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
  { days: 365, label: "365 days" },
];

export const DEFAULT_AGENT_KEY_EXPIRY_DAYS = 30;

export function expiresAtIso(days: number, from: Date = new Date()): string {
  return new Date(from.getTime() + days * 24 * 60 * 60 * 1000).toISOString();
}

// ---------------------------------------------------------------- copy

/**
 * R-V3-3 as widened by R-V3-40 (V3-07-4, verdict C1): the one-time, acknowledged
 * warning shown before "Create key" — verbatim from AGENT-ACCESS.md §5. The
 * inline-secret exposure is wider than "your transcript": the client's own
 * hooks and plugins can also copy a pasted tool call's arguments into their
 * own logs before it ever reaches LKAP, so this leads with the by-reference
 * form (`env:`/`file:`) and treats pasting as the accepted fallback, not the
 * default.
 */
export const TRANSCRIPT_WARNING =
  "Your coding agent stores every tool call, including any LiveKit or vendor secret you paste into the chat, " +
  "in its own local transcript on your machine, and any hooks or plugins it runs may copy that same tool-call " +
  "input into their own logs before LKAP ever sees it. LKAP itself never shows a secret again after you paste " +
  "it. Prefer a reference instead: give the agent an env:NAME or file:~/.config/lkap/dev.env#NAME value. " +
  "Paste a secret only if you accept those local copies.";

/** R-V3-40 (verdict C1): the acknowledgement checkbox label, widened the same way as {@link TRANSCRIPT_WARNING}. */
export const TRANSCRIPT_ACK_LABEL =
  "I understand my coding agent's transcript, hooks and plugins may store what I paste into it.";

/** Shown under every snippet (§5). */
export const KEEP_KEY_NOTE = "Keep the key in your user-level agent config, not in a project file that is committed.";

/** R-V3-17: no git remote yet, so every install line names the checkout by this placeholder. */
export const CHECKOUT_PLACEHOLDER = "<checkout>";

// ---------------------------------------------------------------- snippets

export interface SnippetContext {
  /** The api's own origin (`NEXT_PUBLIC_API_BASE_URL`), e.g. `http://127.0.0.1:8080`. */
  apiOrigin: string;
  /** The raw key, shown once. */
  key: string;
  /** Local (stdio) vs. remote (streamable HTTP, V3-06). */
  remote: boolean;
  /** Defaults to `CHECKOUT_PLACEHOLDER` — there is no real path to fill in from the browser. */
  checkoutPath?: string;
  /** Required when `remote` is true (`LKAP_MCP_PUBLIC_URL`); the Remote choice is disabled without it. */
  publicMcpUrl?: string;
}

function checkout(ctx: SnippetContext): string {
  return ctx.checkoutPath?.trim() || CHECKOUT_PLACEHOLDER;
}

/** Claude Code: `claude mcp add …` (AGENT-ACCESS.md §5, §9.4). */
export function claudeCodeSnippet(ctx: SnippetContext): string {
  if (ctx.remote) {
    const url = ctx.publicMcpUrl ?? "<LKAP_MCP_PUBLIC_URL>";
    return `claude mcp add -s user --transport http lkap ${url} --header "Authorization: Bearer ${ctx.key}"`;
  }
  return (
    `claude mcp add -s user --transport stdio lkap ` +
    `--env LKAP_API_URL=${ctx.apiOrigin} --env LKAP_API_KEY=${ctx.key} ` +
    `-- uv run --project ${checkout(ctx)}/mcp lkap-mcp`
  );
}

/**
 * Codex CLI: the `[mcp_servers.lkap]` block for `~/.codex/config.toml`.
 *
 * The remote form (`url` plus the `http_headers` table) is verified against
 * codex-cli 0.153.4 (R-V3-25). `bearer_token_env_var = "LKAP_API_KEY"` is the
 * documented alternative for a shared machine (`mcp/README.md`, RUNBOOK §20);
 * a literal `bearer_token` is rejected by Codex.
 */
export function codexSnippet(ctx: SnippetContext): string {
  if (ctx.remote) {
    const url = ctx.publicMcpUrl ?? "<LKAP_MCP_PUBLIC_URL>";
    return (
      `[mcp_servers.lkap]\n` +
      `url = "${url}"\n\n` +
      `[mcp_servers.lkap.http_headers]\n` +
      `Authorization = "Bearer ${ctx.key}"\n`
    );
  }
  return (
    `[mcp_servers.lkap]\n` +
    `command = "uv"\n` +
    `args = ["run", "--project", "${checkout(ctx)}/mcp", "lkap-mcp"]\n\n` +
    `[mcp_servers.lkap.env]\n` +
    `LKAP_API_URL = "${ctx.apiOrigin}"\n` +
    `LKAP_API_KEY = "${ctx.key}"\n`
  );
}

/** Cursor / any generic MCP client: the `mcpServers` JSON block. */
export function cursorSnippet(ctx: SnippetContext): string {
  if (ctx.remote) {
    const url = ctx.publicMcpUrl ?? "<LKAP_MCP_PUBLIC_URL>";
    return JSON.stringify(
      { mcpServers: { lkap: { url, headers: { Authorization: `Bearer ${ctx.key}` } } } },
      null,
      2,
    );
  }
  return JSON.stringify(
    {
      mcpServers: {
        lkap: {
          command: "uv",
          args: ["run", "--project", `${checkout(ctx)}/mcp`, "lkap-mcp"],
          env: { LKAP_API_URL: ctx.apiOrigin, LKAP_API_KEY: ctx.key },
        },
      },
    },
    null,
    2,
  );
}

export function snippetFor(tab: SnippetClientId, ctx: SnippetContext): string {
  switch (tab) {
    case "claude-code":
      return claudeCodeSnippet(ctx);
    case "codex":
      return codexSnippet(ctx);
    case "cursor":
      return cursorSnippet(ctx);
  }
}

// ---------------------------------------------------------------- step 3

export function skillInstallLine(checkoutPath?: string): string {
  return `${checkout({ apiOrigin: "", key: "", remote: false, checkoutPath })}/scripts/install_claude_skill.sh`;
}

export const CODEX_AGENTS_NOTE =
  "Copy AGENTS.md from the repo into your project, or point Codex at the checkout.";

export const ASK_AGENT_LINE = "Call lkap_guide and tell me what this workspace has.";
