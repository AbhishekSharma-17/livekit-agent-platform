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
export function middleware(request: NextRequest) {
  if (adminBypassEnabled()) return NextResponse.next();
  if (request.cookies.get(SESSION_COOKIE)?.value) return NextResponse.next();

  const next = `${request.nextUrl.pathname}${request.nextUrl.search}`;
  const url = new URL("/login", request.url);
  url.searchParams.set("next", next);
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/console/:path*"],
};
