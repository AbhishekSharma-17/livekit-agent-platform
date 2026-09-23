import * as React from "react";

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "@/app/login/login-form";

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

  it("shows the api's error message on a failed login", async () => {
    stubFetch([{ status: 401, body: { error: { code: "unauthorized", message: "invalid email or password" } } }]);
    render(<LoginForm />);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "owner@local" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("invalid email or password")).toBeTruthy();
    expect(assign).not.toHaveBeenCalled();
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
  });
});
