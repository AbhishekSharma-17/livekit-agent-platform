import { NextResponse, type NextRequest } from "next/server";

import { SESSION_COOKIE, adminBypassEnabled } from "@/lib/auth";

/**
 * Redirects unauthenticated `/console/**` to `/login?next=…` (V2-14, closing
 * ask #37; CONTRACTS-V2 §3.1 "Web: Next.js middleware redirects
 * unauthenticated `/console/**` to `/login`").
 *
 * This is a coarse, cookie-*presence* check only — it never validates the
 * session (that needs a database round trip the edge runtime shouldn't make
 * on every navigation). A stale or forged cookie still reaches the api,
 * which is the real authority and 401s; the query cache's global `onError`
 * (`components/console/lib/query-provider.tsx`) catches that case and
 * redirects too. This middleware exists so a signed-out visitor never even
 * sees a flash of the console shell before that client-side redirect fires.
 *
 * `adminBypassEnabled()` is the same flag the console proxy checks
 * (`app/api/console/[...path]/route.ts`) — both must be considered together
 * (see `lib/auth.ts`'s docstring): while it's on, the admin token still gets
 * the caller in even with no cookie, so gating navigation on the cookie
 * alone would lock out exactly the break-glass path this flag exists for.
 */
function consoleAuthRedirect(request: NextRequest): NextResponse | null {
  if (adminBypassEnabled()) return null;
  if (request.cookies.get(SESSION_COOKIE)?.value) return null;

  const next = `${request.nextUrl.pathname}${request.nextUrl.search}`;
  const url = new URL("/login", request.url);
  url.searchParams.set("next", next);
  return NextResponse.redirect(url);
}

// How long `/s/[slug]?embed=1` waits for the agent's `allowed_origins` before
// failing closed (V2-18). The edge runtime has no direct database access, so
// this is one small `fetch` to the public api per embedded page load.
const EMBED_POLICY_TIMEOUT_MS = 2000;

/** `frame-ancestors` value for `origins` (CONTRACTS-V2 §3.3: empty = console/test
 * only, `["*"]` = any). `'self'` always applies — the console's own **Test
 * chat** dialog (V2-18) embeds `/s/[slug]?embed=1` from the same origin, the
 * same way "empty `allowed_origins`" already means "the platform's own web
 * origins" for `connect`'s server-side origin check (`routers/connect.py`). */
function frameAncestors(origins: string[]): string {
  if (origins.includes("*")) return "frame-ancestors *";
  const extra = origins.filter((origin) => origin && origin !== "*");
  return `frame-ancestors 'self'${extra.length ? ` ${extra.join(" ")}` : ""}`;
}

/**
 * `GET /v1/agents/{slug}/embed-policy` (V2-18, `routers/text_sessions.py`) — a
 * tiny, unauthenticated, public-only-by-design endpoint returning just
 * `{allowed_origins}`, so the edge middleware never needs a database
 * connection or an admin token to build the embed CSP header.
 *
 * Returns `null` — the caller's cue to fail all the way closed
 * (`frame-ancestors 'none'`, not even `'self'`) — on any error, timeout or
 * non-200: an unreachable api, an unknown/unpublished agent or a slow
 * response must never allow embedding by default.
 */
async function fetchAllowedOrigins(slug: string): Promise<string[] | null> {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!base) return null;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), EMBED_POLICY_TIMEOUT_MS);
  try {
    const response = await fetch(
      `${base.replace(/\/+$/, "")}/v1/agents/${encodeURIComponent(slug)}/embed-policy`,
      { signal: controller.signal, cache: "no-store" },
    );
    if (!response.ok) return null;
    const body: unknown = await response.json();
    const origins =
      body && typeof body === "object" ? (body as { allowed_origins?: unknown }).allowed_origins : null;
    return Array.isArray(origins) ? origins.filter((o): o is string => typeof o === "string") : null;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * `Content-Security-Policy: frame-ancestors` on `/s/[slug]?embed=1` (V2-18,
 * UI_UX_SPEC-V2-AMENDMENTS §2.6): the browser-enforced half of the widget's
 * origin policy. `connect`/`text-sessions` additionally 403 a disallowed
 * `Origin` server-side (`routers/connect.py`) — that check is the one that
 * actually stops a session from starting; this header is what stops the page
 * from being framed there at all, so a disallowed site never gets that far.
 */
async function embedCsp(request: NextRequest): Promise<NextResponse | null> {
  if (request.nextUrl.searchParams.get("embed") !== "1") return null;
  const slug = request.nextUrl.pathname.split("/")[2];
  if (!slug) return null;

  const origins = await fetchAllowedOrigins(slug);
  const response = NextResponse.next();
  response.headers.set(
    "Content-Security-Policy",
    origins === null ? "frame-ancestors 'none'" : frameAncestors(origins),
  );
  return response;
}

export async function middleware(request: NextRequest) {
  if (request.nextUrl.pathname.startsWith("/s/")) {
    return (await embedCsp(request)) ?? NextResponse.next();
  }
  return consoleAuthRedirect(request) ?? NextResponse.next();
}

export const config = {
  matcher: ["/console/:path*", "/s/:path*"],
};
