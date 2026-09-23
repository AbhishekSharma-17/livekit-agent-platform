// @vitest-environment node
/**
 * `middleware.ts` (V2-14, ask #37): redirects unauthenticated `/console/**`
 * to `/login?next=…`, but only once the dev-only admin-token bypass is off
 * (`adminBypassEnabled` in `lib/auth.ts` — the same flag the console proxy
 * checks, so the two behave as one gate end to end).
 */
import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { middleware } from "@/middleware";

function request(path: string, cookie?: string) {
  const headers = new Headers();
  if (cookie) headers.set("cookie", cookie);
  return new NextRequest(`http://localhost:3000${path}`, { headers });
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("console auth middleware", () => {
  it("lets everything through while the bypass is on (the dev default)", () => {
    vi.stubEnv("LKAP_WEB_ADMIN_BYPASS", "1");
    const response = middleware(request("/console/agents"));
    expect(response.status).toBe(200); // NextResponse.next()
  });

  it("redirects to /login?next=… with no session cookie once the bypass is off", () => {
    vi.stubEnv("LKAP_WEB_ADMIN_BYPASS", "0");
    const response = middleware(request("/console/agents?tab=providers"));
    expect(response.status).toBe(307);
    const location = new URL(response.headers.get("location") ?? "", "http://localhost:3000");
    expect(location.pathname).toBe("/login");
    expect(location.searchParams.get("next")).toBe("/console/agents?tab=providers");
  });

  it("passes through with a session cookie once the bypass is off", () => {
    vi.stubEnv("LKAP_WEB_ADMIN_BYPASS", "0");
    const response = middleware(request("/console/agents", "lkap_session=abc"));
    expect(response.status).toBe(200);
  });
});
