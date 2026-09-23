import type { NextRequest } from "next/server";

/**
 * Server-side auth helpers shared by the console proxy and (from V2-14) the
 * login flow and Next middleware. See docs/v2/CONTRACTS-V2.md §3.1.
 */

/** The api's HttpOnly session cookie; the proxy forwards it untouched. */
export const SESSION_COOKIE = "lkap_session";

/** WP-1's workspace-switcher cookie (`components/console/shell/workspace-switcher.tsx`). */
export const ACTIVE_WORKSPACE_COOKIE = "lkap_workspace";

/** The header the api's `WorkspaceContext` reads (slug or id). */
export const WORKSPACE_HEADER = "X-Workspace";

/**
 * The workspace to act in, from the switcher cookie. `undefined` when the
 * cookie is unset or blank, in which case the api picks the caller's only
 * workspace (or `default` for the break-glass admin token).
 */
export function activeWorkspace(request: NextRequest): string | undefined {
  const raw = request.cookies.get(ACTIVE_WORKSPACE_COOKIE)?.value?.trim();
  if (!raw) return undefined;
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

/**
 * The dev-only break-glass escape hatch (V2-14, docs/v2/_asks.md #37; ruling
 * PLAN-V2 §8 / CONTRACTS-V2 §3.1 "Break-glass admin token").
 *
 * Two independent gates must both be on for `X-Admin-Token` to actually work:
 * this one (whether the *web* server still attaches the header) and the
 * api's own `LKAP_ALLOW_ADMIN_TOKEN` (whether the api still *honours* it).
 * Either one can be flipped off without the other; only both off closes the
 * hatch end to end. Default: **on** unless `LKAP_WEB_ADMIN_BYPASS` says
 * otherwise, mirroring the api's `admin_token_allowed` default (dev: true,
 * anything else: false) — the alternative (default off) would lock the
 * user's own dev console the instant this ships, before they have run
 * `python -m lkap_api.auth set-password` (asks #29). Not `NEXT_PUBLIC_*`:
 * this is read only on the server (the proxy route, `middleware.ts`), never
 * shipped to the browser bundle.
 */
export function adminBypassEnabled(): boolean {
  const raw = process.env.LKAP_WEB_ADMIN_BYPASS;
  if (raw !== undefined) return raw === "1" || raw === "true";
  return process.env.NODE_ENV !== "production";
}

/**
 * Validate a `?next=` redirect target: same-origin relative path only. Blocks
 * an open redirect (`?next=https://evil.example` or the protocol-relative
 * `?next=//evil.example`) from a login link an attacker crafted.
 */
export function safeNextPath(value: string | null | undefined, fallback = "/console"): string {
  if (!value) return fallback;
  if (!value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) return fallback;
  return value;
}
