import * as React from "react";

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "@/app/login/login-form";
import LoginPage from "@/app/login/page";

/**
 * `/login` and `/login?invite=` (V2-14, ask #37): `POST auth/login` / `POST
 * auth/accept-invite` through the console proxy, a hard navigation to
 * `?next=` on success, and the open-redirect guard on `next`.
 */
let searchParams = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useSearchParams: () => searchParams,
}));

const assign = vi.fn();

beforeEach(() => {
  searchParams = new URLSearchParams();
  assign.mockClear();
  vi.stubGlobal("location", { ...window.location, assign });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function stubFetch(responses: { status: number; body?: unknown }[]) {
  let call = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      const { status, body } = responses[Math.min(call, responses.length - 1)];
      call += 1;
      if (status === 204) return { ok: true, status, json: async () => undefined } as Response;
      const ok = status < 400;
      return {
        ok,
        status,
        json: async () => body ?? { error: { code: "unknown_error", message: "failed" } },
      } as Response;
    }),
  );
}

describe("LoginForm", () => {
  it("signs in and does a hard navigation to a safe ?next=", async () => {
    searchParams = new URLSearchParams("next=/console/agents");
    stubFetch([{ status: 204 }]);
    render(<LoginForm />);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "owner@local" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "hunter22" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(assign).toHaveBeenCalledWith("/console/agents"));
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/console/auth/login");
    expect(JSON.parse(init.body)).toEqual({ email: "owner@local", password: "hunter22" });
  });

  it("falls back to /console for an unsafe ?next= (open-redirect guard)", async () => {
    searchParams = new URLSearchParams("next=https://evil.example");
    stubFetch([{ status: 204 }]);
    render(<LoginForm />);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "owner@local" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "hunter22" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(assign).toHaveBeenCalledWith("/console"));
  });

  it("shows a plain error with a next step on a failed login, never the api's raw text", async () => {
    stubFetch([{ status: 401, body: { error: { code: "unauthorized", message: "invalid email or password" } } }]);
    render(<LoginForm />);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "owner@local" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("That email and password don't match. Check them and try again.");
    expect(screen.queryByText(/invalid email or password/)).toBeNull();
    expect(assign).not.toHaveBeenCalled();
    // Typed values are kept, and focus returns to the password.
    expect((screen.getByLabelText("Email") as HTMLInputElement).value).toBe("owner@local");
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("Password")));
  });

  it("disables the form and shows a busy label while signing in", async () => {
    let release: () => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            release = () => resolve({ ok: true, status: 204, json: async () => undefined } as Response);
          }),
      ),
    );
    render(<LoginForm />);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "owner@local" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "hunter22" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    const busy = await screen.findByRole("button", { name: "Signing in…" });
    expect((busy as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByLabelText("Email") as HTMLInputElement).disabled).toBe(true);
    release();
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/console"));
  });

  it("says the server couldn't be reached when the request fails on the network", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    render(<LoginForm />);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "owner@local" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "hunter22" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("We couldn't reach the server. Check your connection and try again.");
    expect(alert.textContent).not.toContain("Failed to fetch");
  });

  it("says the sign-in service isn't responding on a server error", async () => {
    stubFetch([{ status: 500, body: { error: { code: "internal_error", message: "boom: Traceback" } } }]);
    render(<LoginForm />);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "owner@local" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "hunter22" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("The sign-in service isn't responding.");
    expect(alert.textContent).not.toContain("Traceback");
  });

  it("shows an offline notice while the browser is offline", async () => {
    const onLine = vi.spyOn(window.navigator, "onLine", "get").mockReturnValue(false);
    render(<LoginForm />);

    expect(screen.getByText("You're offline")).toBeTruthy();
    onLine.mockReturnValue(true);
    act(() => {
      window.dispatchEvent(new Event("online"));
    });
    expect(screen.queryByText("You're offline")).toBeNull();
  });

  it("lets the person show and hide the password", () => {
    render(<LoginForm />);

    const input = screen.getByLabelText("Password") as HTMLInputElement;
    const toggle = screen.getByRole("button", { name: "Show password" });
    expect(input.type).toBe("password");
    fireEvent.click(toggle);
    expect(input.type).toBe("text");
    expect(toggle.getAttribute("aria-pressed")).toBe("true");
  });

  it("has no forgot-password link while the api has no reset endpoint (AUDIT D9)", () => {
    render(<LoginForm />);

    expect(screen.queryByRole("link", { name: /forgot/i })).toBeNull();
  });

  it("accepts an invite and signs in", async () => {
    searchParams = new URLSearchParams("invite=tok123");
    stubFetch([{ status: 204 }]);
    render(<LoginForm />);

    expect(screen.getByText("Accept your invite")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "supersecret1" } });
    fireEvent.click(screen.getByRole("button", { name: "Join workspace" }));

    await waitFor(() => expect(assign).toHaveBeenCalledWith("/console"));
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/console/auth/accept-invite");
    expect(JSON.parse(init.body)).toEqual({ token: "tok123", password: "supersecret1", name: undefined });
  });

  it("offers a plain sign-in link when an invite was already used", async () => {
    searchParams = new URLSearchParams("invite=tok123");
    stubFetch([
      { status: 400, body: { error: { code: "bad_request", message: "invalid invite: it has already been used" } } },
    ]);
    render(<LoginForm />);

    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "supersecret1" } });
    fireEvent.click(screen.getByRole("button", { name: "Join workspace" }));

    expect(await screen.findByText("Invite already used")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Sign in" }).getAttribute("href")).toBe("/login");
  });

  it("explains an expired invite in plain words", async () => {
    searchParams = new URLSearchParams("invite=tok123");
    stubFetch([{ status: 400, body: { error: { code: "bad_request", message: "invalid invite: invite has expired" } } }]);
    render(<LoginForm />);

    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "supersecret1" } });
    fireEvent.click(screen.getByRole("button", { name: "Join workspace" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("This invite link has expired. Ask a workspace admin to send a new invite.");
    expect(alert.textContent).not.toContain("invalid invite");
  });
});

describe("LoginPage", () => {
  it("renders one h1, the form card and a decorative showcase", () => {
    render(<LoginPage />);

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1, name: "Sign in" })).toBeTruthy();
    const showcase = document.querySelector('[data-slot="sign-in-showcase"]');
    expect(showcase?.getAttribute("aria-hidden")).toBe("true");
  });
});

/**
 * The animated showcase (UI-R3). jsdom runs no CSS, so these check what the
 * stylesheet keys off: the director's `data-motion`, `data-step` and
 * `data-paused` on the scene root, and that its timers stop when motion
 * isn't welcome, the tab is hidden or the panel isn't shown.
 */
describe("SignInShowcase", () => {
  const ORIGINAL_WIDTH = window.innerWidth;

  function stubMotion(allowed: boolean) {
    vi.stubGlobal(
      "matchMedia",
      vi.fn((query: string) => ({
        matches: query.includes("no-preference") ? allowed : query.includes("reduce") ? !allowed : false,
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    );
  }

  function setWidth(width: number) {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
  }

  function setVisibility(state: DocumentVisibilityState) {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
  }

  function scene(): HTMLElement {
    const el = document.querySelector<HTMLElement>('[data-slot="showcase-scene"]');
    expect(el).not.toBeNull();
    return el!;
  }

  function advance(ms: number) {
    act(() => {
      vi.advanceTimersByTime(ms);
    });
  }

  beforeEach(() => {
    vi.useFakeTimers();
    setWidth(1440);
  });

  afterEach(() => {
    vi.useRealTimers();
    setWidth(ORIGINAL_WIDTH);
    setVisibility("visible");
  });

  it("is a decorative scene: hidden from assistive tech, nothing focusable, the call and the panel in place", () => {
    stubMotion(true);
    render(<LoginPage />);

    const showcase = document.querySelector<HTMLElement>('[data-slot="sign-in-showcase"]')!;
    expect(showcase.getAttribute("aria-hidden")).toBe("true");
    expect(showcase.className).toContain("hidden");
    expect(showcase.className).toContain("min-[1081px]:block");
    expect(showcase.querySelectorAll("a, button, input, [tabindex]")).toHaveLength(0);
    expect(showcase.contains(scene())).toBe(true);
    // The call, the transcript, and the agent's panel filling in.
    expect(showcase.textContent).toContain("Front desk agent");
    expect(showcase.textContent).toContain("Can I move my visit to Thursday?");
    expect(showcase.textContent).toContain("Moved to Thursday, 10:30");
    expect(showcase.querySelectorAll("[data-at]").length).toBe(8);
    expect(showcase.querySelectorAll(".lkap-showcase-wave-bar").length).toBeGreaterThan(20);
  });

  it("holds the composed frame and stops every timer under reduced motion", () => {
    stubMotion(false);
    render(<LoginPage />);

    const root = scene();
    expect(root.getAttribute("data-motion")).toBe("reduce");
    expect(root.getAttribute("data-step")).toBe("9");
    expect(root.hasAttribute("data-paused")).toBe(true);
    const timer = root.querySelector('[data-slot="call-timer"]')!;
    expect(timer.textContent).toBe("01:24");

    advance(60_000);
    expect(root.getAttribute("data-step")).toBe("9");
    expect(timer.textContent).toBe("01:24");
  });

  it("plays the story in a loop from the composed frame when motion is welcome", () => {
    stubMotion(true);
    render(<LoginPage />);

    const root = scene();
    expect(root.getAttribute("data-motion")).toBe("on");
    expect(root.hasAttribute("data-paused")).toBe(false);
    // Starts on the held frame, so nothing resets on load.
    expect(root.getAttribute("data-step")).toBe("9");

    advance(4200);
    expect(root.getAttribute("data-step")).toBe("10");
    advance(900);
    expect(root.getAttribute("data-step")).toBe("0");
    advance(900);
    expect(root.getAttribute("data-step")).toBe("1");
    expect(root.querySelector('[data-slot="call-timer"]')!.textContent).toBe("01:30");
  });

  it("pauses while the tab is hidden and picks up when it's back", () => {
    stubMotion(true);
    render(<LoginPage />);
    const root = scene();

    setVisibility("hidden");
    expect(root.hasAttribute("data-paused")).toBe(true);
    advance(30_000);
    expect(root.getAttribute("data-step")).toBe("9");

    setVisibility("visible");
    expect(root.hasAttribute("data-paused")).toBe(false);
    advance(4200);
    expect(root.getAttribute("data-step")).toBe("10");
  });

  it("doesn't run at 1080 px and below, where the panel is hidden", () => {
    stubMotion(true);
    setWidth(1080);
    render(<LoginPage />);
    const root = scene();

    expect(root.hasAttribute("data-paused")).toBe(true);
    advance(30_000);
    expect(root.getAttribute("data-step")).toBe("9");
  });
});
