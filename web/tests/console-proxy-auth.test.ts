// @vitest-environment node
/**
 * The console proxy during the v2 auth transition (V2-02): the session cookie
 * and the workspace switcher's choice reach the api, the break-glass token is
 * still attached, and the api's Set-Cookie comes back to the browser.
 */
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GET, POST } from "@/app/api/console/[...path]/route";
import { activeWorkspace } from "@/lib/auth";

type FetchArgs = [URL, RequestInit & { headers: Headers }];

function request(
  path: string,
  init: {
    method?: string;
    cookie?: string;
    headers?: Record<string, string>;
  } = {},
) {
  const headers = new Headers(init.headers);
  if (init.cookie) headers.set("cookie", init.cookie);
  return new NextRequest(`http://localhost:3000/api/console/${path}`, {
    method: init.method ?? "GET",
    headers,
    body: init.method === "POST" ? "{}" : undefined,
  });
}

function params(path: string) {
  return { params: Promise.resolve({ path: path.split("/") }) };
}

describe("console proxy auth forwarding", () => {
  const fetchMock = vi.fn<(...args: FetchArgs) => Promise<Response>>();

  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://api.test:8080/");
    vi.stubEnv("LKAP_ADMIN_TOKEN", "test-admin");
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("sends the lkap_workspace cookie as X-Workspace and forwards the session cookie", async () => {
    await GET(
      request("agents", { cookie: "lkap_session=abc; lkap_workspace=beta" }),
      params("agents"),
    );

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("http://api.test:8080/v1/agents");
    expect(init.headers.get("X-Workspace")).toBe("beta");
    expect(init.headers.get("cookie")).toContain("lkap_session=abc");
    expect(init.headers.get("X-Admin-Token")).toBe("test-admin");
  });

  it("stops attaching X-Admin-Token once the escape hatch is turned off", async () => {
    vi.stubEnv("LKAP_WEB_ADMIN_BYPASS", "0");
    await GET(request("agents", { cookie: "lkap_session=abc" }), params("agents"));

    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers.has("X-Admin-Token")).toBe(false);
  });

  it("attaches X-Admin-Token while the bypass is explicitly on, even outside dev", async () => {
    vi.stubEnv("LKAP_WEB_ADMIN_BYPASS", "1");
    await GET(request("agents"), params("agents"));

    expect(fetchMock.mock.calls[0][1].headers.get("X-Admin-Token")).toBe("test-admin");
  });

  it("never throws when LKAP_ADMIN_TOKEN is unset and the bypass is off", async () => {
    vi.stubEnv("LKAP_WEB_ADMIN_BYPASS", "0");
    vi.stubEnv("LKAP_ADMIN_TOKEN", "");
    const response = await GET(request("agents"), params("agents"));
    expect(response.status).toBe(200);
    expect(fetchMock.mock.calls[0][1].headers.has("X-Admin-Token")).toBe(false);
  });

  it("sends no X-Workspace without the switcher cookie", async () => {
    await GET(request("agents"), params("agents"));

    expect(fetchMock.mock.calls[0][1].headers.has("X-Workspace")).toBe(false);
  });

  it("keeps an explicit X-Workspace header over the cookie", async () => {
    await GET(
      request("agents", {
        cookie: "lkap_workspace=beta",
        headers: { "X-Workspace": "default" },
      }),
      params("agents"),
    );

    expect(fetchMock.mock.calls[0][1].headers.get("X-Workspace")).toBe(
      "default",
    );
  });

  it("passes the api's Set-Cookie back to the browser", async () => {
    fetchMock.mockResolvedValue(
      new Response(null, {
        status: 204,
        headers: {
          "set-cookie": "lkap_session=new; HttpOnly; Path=/; SameSite=lax",
        },
      }),
    );

    const response = await POST(
      request("auth/login", { method: "POST" }),
      params("auth/login"),
    );

    expect(response.status).toBe(204);
    expect(response.headers.get("set-cookie")).toContain("lkap_session=new");
  });
});

describe("console proxy CSRF and path guards (V2-21)", () => {
  const fetchMock = vi.fn<(...args: FetchArgs) => Promise<Response>>();

  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://api.test:8080/");
    vi.stubEnv("LKAP_ADMIN_TOKEN", "test-admin");
    vi.stubEnv("LKAP_WEB_ADMIN_BYPASS", "1");
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it.each(["cross-site", "same-site"])(
    "refuses a %s write before attaching the admin token",
    async (site) => {
      const response = await POST(
        request("connections", { method: "POST", headers: { "sec-fetch-site": site } }),
        params("connections"),
      );

      expect(response.status).toBe(403);
      expect((await response.json()).error.code).toBe("forbidden");
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("forwards same-origin writes and reads from anywhere", async () => {
    const write = await POST(
      request("agents", { method: "POST", headers: { "sec-fetch-site": "same-origin" } }),
      params("agents"),
    );
    const read = await GET(
      request("agents", { headers: { "sec-fetch-site": "cross-site" } }),
      params("agents"),
    );

    expect(write.status).toBe(200);
    expect(read.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("refuses path segments that would climb out of /v1", async () => {
    const response = await GET(request("x"), {
      params: Promise.resolve({ path: ["..", "internal", "v1", "sessions"] }),
    });

    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("encodes each segment so an encoded slash stays inside one segment", async () => {
    await GET(request("x"), { params: Promise.resolve({ path: ["agents", "a b"] }) });

    expect(String(fetchMock.mock.calls[0][0])).toBe("http://api.test:8080/v1/agents/a%20b");
  });
});

describe("activeWorkspace", () => {
  it("decodes the cookie value and ignores blanks", () => {
    expect(
      activeWorkspace(request("x", { cookie: "lkap_workspace=acme%20co" })),
    ).toBe("acme co");
    expect(
      activeWorkspace(request("x", { cookie: "lkap_workspace=" })),
    ).toBeUndefined();
    expect(activeWorkspace(request("x"))).toBeUndefined();
  });
});
