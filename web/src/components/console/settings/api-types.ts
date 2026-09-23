/**
 * TEMPORARY hand-mirror of `api/src/lkap_api/auth/models.py` (the auth, team,
 * invite and API-key request/response shapes) plus
 * `api/src/lkap_api/routers/webhooks.py::WebhookEndpointCreated` and
 * `api/src/lkap_api/webhooks/events.py::KNOWN_EVENTS`.
 *
 * None of these exist in `@/contracts/lkap-contracts` yet:
 * - `docs/v2/_asks.md` #26 asks the coordinator to move `LoginIn`,
 *   `PasswordChangeIn`, `AcceptInviteIn`, `InviteCreate`, `InviteOut`,
 *   `WorkspaceOut`, `WorkspaceUpdate`, `MemberOut`, `MemberCreate`,
 *   `MemberUpdate`, `ApiKeyCreate`, `ApiKeyOut`, `ApiKeyCreated`, `AuditOut`
 *   into `lkap_contracts.api_models` and regenerate — until then this file
 *   is the only place their field names are typed on the web side.
 * - #39 asks for a real `secret: string`-carrying contracts model for
 *   `POST /v1/webhooks`'s response (`WebhookEndpointCreated`); the api ships
 *   a local subclass with no contracts equivalent.
 *
 * Field names, optionality and literal unions are copied by hand from the
 * Python source above — re-check both files together whenever either one
 * changes. Delete this module (and switch every import to
 * `@/contracts/lkap-contracts`) once ask #26/#39 land and the generated
 * types carry these shapes.
 */

// ---- roles.py ----

export type Role = "viewer" | "builder" | "admin" | "owner";

export type Scope =
  | "agents:read"
  | "agents:write"
  | "sessions:read"
  | "sessions:write"
  | "calls:write"
  | "connections:read"
  | "connections:write"
  | "providers:read"
  | "providers:write"
  | "webhooks:write"
  | "*";

export const SCOPES: Scope[] = [
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
  "*",
];

export const ROLE_LABEL: Record<Role, string> = {
  viewer: "Viewer",
  builder: "Builder",
  admin: "Admin",
  owner: "Owner",
};

/** The generated `Page` schema is untyped (`{items: unknown[]}`); this is the real, generic shape every list route here returns. */
export interface Page<T> {
  items: T[];
  total: number;
}

// ---- auth/models.py ----

export interface LoginIn {
  email: string;
  password: string;
}

export interface PasswordChangeIn {
  current: string;
  new: string;
}

export interface AcceptInviteIn {
  token: string;
  password: string;
  name?: string | null;
}

export interface InviteCreate {
  email: string;
  role?: Role;
  name?: string;
}

export interface InviteOut {
  email: string;
  role: Role;
  workspace_id: string;
  token: string;
  url: string;
  expires_at: string;
}

export interface WorkspaceOut {
  id: string;
  slug: string;
  name: string;
  settings: Record<string, unknown>;
  role: Role;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceUpdate {
  name?: string | null;
  settings?: Record<string, unknown> | null;
}

export interface MemberOut {
  user_id: string;
  email: string;
  name: string;
  role: Role;
  created_at: string;
  disabled?: boolean;
  /** Invited but has never set a password. */
  pending?: boolean;
}

export interface MemberCreate {
  email: string;
  role?: Role;
}

export interface MemberUpdate {
  role: Role;
}

export interface ApiKeyCreate {
  name: string;
  scopes: Scope[];
  expires_at?: string | null;
}

export interface ApiKeyOut {
  id: string;
  workspace_id: string;
  name: string;
  prefix: string;
  scopes: string[];
  created_by?: string | null;
  created_at: string;
  last_used_at?: string | null;
  revoked_at?: string | null;
  expires_at?: string | null;
}

/** The only time `key` is ever returned — shown once, never stored client-side beyond the reveal dialog. */
export interface ApiKeyCreated extends ApiKeyOut {
  key: string;
}

export interface AuditOut {
  id: number;
  workspace_id: string | null;
  actor_type: "user" | "api_key" | "system";
  actor_id: string | null;
  action: string;
  target_type: string;
  target_id: string | null;
  payload: Record<string, unknown>;
  ts: string;
}

// ---- routers/webhooks.py::WebhookEndpointCreated (ask #39) ----

/**
 * `WebhookEndpointOut` (already generated in `@/contracts/lkap-contracts`)
 * plus the plaintext signing secret, shown exactly once by
 * `POST /v1/webhooks`.
 */
export interface WebhookEndpointCreated {
  id: string;
  url: string;
  events: string[];
  description: string;
  enabled: boolean;
  secret_prefix: string;
  created_at: string | null;
  updated_at: string | null;
  secret: string;
}

// ---- webhooks/events.py::KNOWN_EVENTS ----

export const KNOWN_WEBHOOK_EVENTS: string[] = [
  "session.started",
  "session.ended",
  "session.qa_completed",
  "recording.ready",
  "call.started",
  "call.ended",
];

export const WEBHOOK_EVENT_LABEL: Record<string, string> = {
  "session.started": "Session started",
  "session.ended": "Session ended",
  "session.qa_completed": "QA completed",
  "recording.ready": "Recording ready",
  "call.started": "Call started",
  "call.ended": "Call ended",
};
