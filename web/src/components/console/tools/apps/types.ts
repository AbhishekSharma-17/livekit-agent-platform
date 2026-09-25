/**
 * `lkap_contracts.tool_providers` `Literal` type aliases (docs/v5/COMPOSIO.md
 * §3). `lkap_contracts.export` only emits pydantic models into the generated
 * `.d.ts` — plain `Literal` aliases (`ActionRisk`, `AuthOption`,
 * `ConnectMethod`, `ConnectionStatus`, `SubjectKind`) never appear there, so
 * they are mirrored here by hand rather than imported. Every field that uses
 * one of these on the wire (`ToolkitOut.auth`, `AppActionOut.risk`,
 * `AppConnectIn.method`, `AppConnectionOut.status`/`.method`, …) is typed as
 * the matching plain string union in the generated types already; these
 * aliases just give that union a name to import instead of repeating it.
 */

export type ActionRisk = "read" | "write" | "destructive";

export type AuthOption = "oauth_managed" | "oauth_custom" | "api_key" | "bearer" | "basic" | "none";

export type ConnectMethod = "managed" | "custom_oauth" | "api_key" | "none";

export type ConnectionStatus = "active" | "initiated" | "expired" | "failed" | "inactive" | "unknown";

export type SubjectKind = "workspace" | "agent";
