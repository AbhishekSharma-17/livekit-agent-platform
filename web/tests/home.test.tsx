import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import Home from "@/app/page";

function stubFetch(impl: (url: string) => Promise<Response> | Response) {
  const fetchMock = vi.fn(impl);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("Home (docs/UI_UX_SPEC.md §3.4, §7.12)", () => {
  it("links to the console and to /login (v2 amendment) and does not repeat its title", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://api.test");
    stubFetch(
      async () =>
        ({
          ok: true,
          status: 200,
          json: async () => ({ ok: true, version: "0.4.2", packs: ["generic", "insurance_claim"] }),
        }) as Response,
    );

    render(await Home());

    const consoleLink = screen.getByRole("link", { name: "Open console" });
    expect(consoleLink.getAttribute("href")).toBe("/console");

    const loginLink = screen.getByRole("link", { name: "Sign in" });
    expect(loginLink.getAttribute("href")).toBe("/login");

    // The wordmark ("LKAP") appears exactly once — the old page repeated its
    // title as both a badge and an <h1> (§1.2).
    expect(screen.getAllByText("LKAP")).toHaveLength(1);
  });

  it("discloses how session pages work without navigating away", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://api.test");
    stubFetch(async () => ({ ok: true, status: 200, json: async () => ({ ok: true }) }) as Response);

    render(await Home());

    expect(screen.queryByText(/\/s\/<slug>/)).toBeNull();
    const trigger = screen.getByRole("button", { name: /how session pages work/i });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });

  it("shows version, pack count and reachability when the health endpoint responds", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://api.test");
    stubFetch(
      async () =>
        ({
          ok: true,
          status: 200,
          json: async () => ({ ok: true, version: "0.4.2", packs: ["generic", "insurance_claim"] }),
        }) as Response,
    );

    render(await Home());

    expect(screen.getByText("v0.4.2 · 2 packs loaded · API reachable")).toBeTruthy();
  });

  it("degrades to 'API unreachable' when the health endpoint fails, without throwing", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://api.test");
    stubFetch(async () => {
      throw new Error("network down");
    });

    render(await Home());

    expect(screen.getByText("API unreachable")).toBeTruthy();
  });

  it("degrades to 'API unreachable' when NEXT_PUBLIC_API_BASE_URL is unset", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "");
    const fetchMock = stubFetch(async () => ({ ok: true, status: 200, json: async () => ({}) }) as Response);

    render(await Home());

    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText("API unreachable")).toBeTruthy();
  });

  it("has no footer doc links (R-V2-3a: no /docs/* route in Phase 1, docs never served from web/public/)", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://api.test");
    stubFetch(async () => ({ ok: true, status: 200, json: async () => ({ ok: true }) }) as Response);

    render(await Home());

    expect(screen.queryByRole("link", { name: "Architecture" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Runbook" })).toBeNull();
    expect(screen.queryByRole("navigation", { name: "Documentation" })).toBeNull();
  });
});
