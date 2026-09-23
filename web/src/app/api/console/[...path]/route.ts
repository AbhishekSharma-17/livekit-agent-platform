import { NextRequest, NextResponse } from "next/server";

import { WORKSPACE_HEADER, activeWorkspace, adminBypassEnabled } from "@/lib/auth";

/**
 * Server-side proxy for the console: forwards `/api/console/*` to the
 * `lkap_api` admin API. See docs/CONTRACTS.md §3, §7 and
 * docs/v2/CONTRACTS-V2.md §3.1.
 *
 * Auth (V2-14, closing ask #37's hand-off from V2-02):
 * - Every request header except hop-by-hop ones is forwarded, so the
 *   browser's `lkap_session` cookie reaches the api, and the api's
 *   `Set-Cookie` (login, logout, session rotation) comes back unchanged.
 *   The api prefers the cookie when one is valid.
 * - `X-Admin-Token` from the server-only `LKAP_ADMIN_TOKEN` is attached only
 *   while `adminBypassEnabled()` is on (break-glass, and only actually
 *   honoured by the api while its own `LKAP_ALLOW_ADMIN_TOKEN` is also on —
 *   two independent gates, see `lib/auth.ts`'s docstring). Signing in with a
 *   real password is now the only supported path once either gate is off.
 * - The active workspace chosen in the sidebar switcher (WP-1's
 *   `lkap_workspace` cookie) is sent as `X-Workspace`; without it the api
 *   uses the caller's only (or, for the admin token, the `default`) workspace.
 *
 * Always dynamic (no caching): every call carries auth and often mutates
 * server state.
 */
export const dynamic = "force-dynamic";

const HOP_BY_HOP_REQUEST_HEADERS = new Set([
  "host",
  "connection",
  "content-length",
]);

function apiBaseUrl(): string {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!base) {
    throw new Error("NEXT_PUBLIC_API_BASE_URL is not set");
  }
  return base.replace(/\/+$/, "");
}

function adminToken(): string | undefined {
  return process.env.LKAP_ADMIN_TOKEN;
}

async function proxy(
  request: NextRequest,
  path: string[],
): Promise<NextResponse> {
  const upstreamUrl = new URL(`${apiBaseUrl()}/v1/${path.join("/")}`);
  upstreamUrl.search = request.nextUrl.search;

  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!HOP_BY_HOP_REQUEST_HEADERS.has(key.toLowerCase())) {
      headers.set(key, value);
    }
  });
  if (adminBypassEnabled()) {
    const token = adminToken();
    if (token) headers.set("X-Admin-Token", token);
  }
  const workspace = activeWorkspace(request);
  if (workspace && !headers.has(WORKSPACE_HEADER)) {
    headers.set(WORKSPACE_HEADER, workspace);
  }

  const hasBody = !["GET", "HEAD"].includes(request.method);
  const upstreamResponse = await fetch(upstreamUrl, {
    method: request.method,
    headers,
    body: hasBody ? await request.arrayBuffer() : undefined,
    // @ts-expect-error - `duplex` is required by undici for streamed bodies but missing from the DOM lib types.
    duplex: hasBody ? "half" : undefined,
    cache: "no-store",
  });

  const responseHeaders = new Headers(upstreamResponse.headers);
  responseHeaders.delete("x-admin-token");
  responseHeaders.delete("content-encoding");
  responseHeaders.delete("content-length");

  return new NextResponse(upstreamResponse.body, {
    status: upstreamResponse.status,
    headers: responseHeaders,
  });
}

type RouteParams = { params: Promise<{ path: string[] }> };

async function handle(
  request: NextRequest,
  { params }: RouteParams,
): Promise<NextResponse> {
  const { path } = await params;
  return proxy(request, path);
}

export const GET = handle;
export const POST = handle;
export const PUT = handle;
export const PATCH = handle;
export const DELETE = handle;
