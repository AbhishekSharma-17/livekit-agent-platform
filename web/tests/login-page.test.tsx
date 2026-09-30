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
