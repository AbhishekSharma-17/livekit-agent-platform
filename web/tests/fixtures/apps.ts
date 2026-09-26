import type {
  AppActionOut,
  AppActionPage,
  AppConnectionOut,
  AppConnectionPage,
  AppKeyTestOut,
  AppsStatusOut,
  ToolkitOut,
  ToolkitPage,
} from "@/contracts/lkap-contracts";
import type { ToolProviderCategoryOut, ToolProviderCategoryPage } from "@/components/console/lib/api-hooks";

/**
 * Fixtures for Tools -> Apps (Composio, docs/v5/COMPOSIO.md §6, V5-22):
 * `console-apps.test.tsx`, `console-apps-enable.test.tsx` and the Composio
 * row added to `console-credentials-page.test.tsx`. Shapes mirror
 * `contracts/generated/ts/lkap-contracts.d.ts` exactly — no field is
 * invented here.
 */

export function toolkitFixture(overrides: Partial<ToolkitOut> = {}): ToolkitOut {
  return {
    slug: "googlecalendar",
    name: "Google Calendar",
    logo: "https://logos.composio.dev/googlecalendar.png",
    categories: ["Productivity", "Scheduling"],
    description: "Create events, find free slots and manage calendars.",
    auth: ["oauth_managed", "oauth_custom"],
    tools_count: 24,
    connected: false,
    connection_id: null,
    auth_fields: {},
    auth_guide_url: null,
    oauth_redirect_uri: null,
    ...overrides,
  };
}

export const TOOLKIT_SLACK = toolkitFixture({
  slug: "slack",
  name: "Slack",
  categories: ["Communication"],
  auth: ["api_key"],
  auth_fields: { api_key: [{ name: "api_key", label: "Bot token", secret: true, required: true }] },
});

export const TOOLKIT_GITHUB_CONNECTED = toolkitFixture({
  slug: "github",
  name: "GitHub",
  categories: ["Developer tools"],
  auth: ["oauth_managed"],
  connected: true,
  connection_id: "conn_github",
});

export function toolkitPage(items: ToolkitOut[] = [toolkitFixture(), TOOLKIT_SLACK], nextCursor: string | null = null): ToolkitPage {
  return { items, next_cursor: nextCursor, total: items.length };
}

/** `GET /v1/tool-providers/composio/categories` — the gallery's complete category list. */
export function categoryPage(
  items: ToolProviderCategoryOut[] = [
    { id: "scheduling", name: "Scheduling" },
    { id: "communication", name: "Communication" },
    { id: "developer-tools", name: "Developer tools" },
  ],
): ToolProviderCategoryPage {
  return { items };
}

/**
 * A single account of an app — realistically, the *only* account a real
 * connection ever has until R-V5-13's "Add another account": the backend's
 * lazy backfill (`AppConnection.label()`, `api/src/lkap_api/tool_providers/service.py`)
 * makes the first (and, here, only) connection the default with the app's
 * own name as its label.
 */
export function connectionFixture(overrides: Partial<AppConnectionOut> = {}): AppConnectionOut {
  return {
    id: "conn_github",
    provider: "composio",
    toolkit: "github",
    toolkit_name: "GitHub",
    subject: "ws:ws1",
    status: "active",
    method: "managed",
    connected_at: "2026-09-10T10:00:00Z",
    last_checked_at: "2026-09-20T10:00:00Z",
    needs_reconnect: false,
    picked_actions: [],
    agents_using: 0,
    label: "GitHub",
    is_default: true,
    ...overrides,
  };
}

/**
 * Two accounts of the same app (R-V5-13, `console-app-accounts.test.tsx`):
 * "Work" is the default, connected first; "Personal" a second account of
 * the same toolkit, its own connection row.
 */
export function connectionFixtureAccounts(overrides: Partial<AppConnectionOut> = {}): [AppConnectionOut, AppConnectionOut] {
  return [
    connectionFixture({ ...overrides, id: "conn_work", label: "Work", is_default: true }),
    connectionFixture({ ...overrides, id: "conn_personal", label: "Personal", is_default: false }),
  ];
}

export function connectionPage(items: AppConnectionOut[] = [connectionFixture()]): AppConnectionPage {
  return { items, total: items.length };
}

export function actionFixture(overrides: Partial<AppActionOut> = {}): AppActionOut {
  return {
    slug: "GITHUB_LIST_REPOS",
    name: "List repositories",
    description: "List the repositories a user or org can see.",
    parameters: {},
    important: true,
    tags: ["read"],
    risk: "read",
    version: "1",
    ...overrides,
  };
}

export const ACTION_DELETE_REPO = actionFixture({
  slug: "GITHUB_DELETE_REPO",
  name: "Delete a repository",
  description: "Permanently deletes a repository.",
  important: false,
  tags: [],
  risk: "destructive",
});

export function actionPage(items: AppActionOut[] = [actionFixture(), ACTION_DELETE_REPO], nextCursor: string | null = null): AppActionPage {
  return { items, next_cursor: nextCursor, total: items.length };
}

export function appsStatusFixture(overrides: Partial<AppsStatusOut> = {}): AppsStatusOut {
  return {
    enabled: true,
    credential_id: "cred_composio",
    fingerprint: "…a1b2",
    last_test_ok: true,
    last_test_at: "2026-09-24T10:00:00Z",
    last_test_message: "Key works",
    connections: 1,
    paused_tools: 0,
    ...overrides,
  };
}

export const STATUS_NOT_SET_UP: AppsStatusOut = appsStatusFixture({
  credential_id: null,
  fingerprint: null,
  last_test_ok: null,
  last_test_at: null,
  last_test_message: null,
  connections: 0,
  enabled: true,
});

export const STATUS_DISABLED: AppsStatusOut = appsStatusFixture({ enabled: false });

export function keyTestOk(overrides: Partial<AppKeyTestOut> = {}): AppKeyTestOut {
  return { ok: true, account_name: null, project_name: "Acme Workspace", toolkits_count: 512, message: "Key works", ...overrides };
}

export function keyTestFailed(message = "Incorrect API key provided"): AppKeyTestOut {
  return { ok: false, account_name: null, project_name: null, toolkits_count: null, message };
}
