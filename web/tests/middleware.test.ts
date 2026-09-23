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
  // V2-18 made `middleware` async (it may `fetch` the embed policy for
  // `/s/[slug]?embed=1` — see the "embed CSP" describe block below), so every
  // call site here now awaits it; behaviour for `/console/**` is unchanged.
  it("lets everything through while the bypass is on (the dev default)", async () => {
    vi.stubEnv("LKAP_WEB_ADMIN_BYPASS", "1");
    const response = await middleware(request("/console/agents"));
    expect(response.status).toBe(200); // NextResponse.next()
  });

  it("redirects to /login?next=… with no session cookie once the bypass is off", async () => {
    vi.stubEnv("LKAP_WEB_ADMIN_BYPASS", "0");
    const response = await middleware(request("/console/agents?tab=providers"));
    expect(response.status).toBe(307);
    const location = new URL(response.headers.get("location") ?? "", "http://localhost:3000");
    expect(location.pathname).toBe("/login");
    expect(location.searchParams.get("next")).toBe("/console/agents?tab=providers");
  });

  it("passes through with a session cookie once the bypass is off", async () => {
    vi.stubEnv("LKAP_WEB_ADMIN_BYPASS", "0");
    const response = await middleware(request("/console/agents", "lkap_session=abc"));
    expect(response.status).toBe(200);
  });
});

describe("embed CSP middleware (V2-18)", () => {
  const originalFetch = global.fetch;

  afterEach(() => {
    global.fetch = originalFetch;
  });

  function mockEmbedPolicy(status: number, body?: unknown) {
    global.fetch = vi.fn(async () =>
      new Response(body === undefined ? null : JSON.stringify(body), { status }),
    ) as unknown as typeof fetch;
  }

  it("is a no-op for a normal (non-embed) session page", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://api.test");
    global.fetch = vi.fn() as unknown as typeof fetch;
    const response = await middleware(request("/s/my-agent"));
    expect(response.status).toBe(200);
    expect(response.headers.get("content-security-policy")).toBeNull();
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("allows only 'self' when the agent sets no allowed_origins", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://api.test");
    mockEmbedPolicy(200, { allowed_origins: [] });
    const response = await middleware(request("/s/my-agent?embed=1"));
    expect(response.headers.get("content-security-policy")).toBe("frame-ancestors 'self'");
  });

  it("adds the agent's explicit allowed_origins to 'self'", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://api.test");
    mockEmbedPolicy(200, { allowed_origins: ["https://shop.example"] });
    const response = await middleware(request("/s/my-agent?embed=1"));
    expect(response.headers.get("content-security-policy")).toBe(
      "frame-ancestors 'self' https://shop.example",
    );
  });

  it("allows any site when allowed_origins is ['*']", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://api.test");
    mockEmbedPolicy(200, { allowed_origins: ["*"] });
    const response = await middleware(request("/s/my-agent?embed=1"));
    expect(response.headers.get("content-security-policy")).toBe("frame-ancestors *");
  });

  it("fails closed to 'none' when the api call fails", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://api.test");
    mockEmbedPolicy(404);
    const response = await middleware(request("/s/my-agent?embed=1"));
    expect(response.headers.get("content-security-policy")).toBe("frame-ancestors 'none'");
  });

  it("fails closed to 'none' when NEXT_PUBLIC_API_BASE_URL is unset", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "");
    const response = await middleware(request("/s/my-agent?embed=1"));
    expect(response.headers.get("content-security-policy")).toBe("frame-ancestors 'none'");
  });
});
