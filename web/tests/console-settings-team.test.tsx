import * as React from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TeamTab } from "@/components/console/settings/team-tab";

/**
 * Team tab (V2-14): members list with role changes, invite dialog with a
 * copyable one-time link (CONTRACTS-V2 §3.2, §3.4).
 */
function jsonResponse(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body } as Response;
}

const ME = {
  user: { id: "u1", email: "owner@local", name: "Owner" },
  workspaces: [{ id: "w1", slug: "default", name: "Default", role: "owner" }],
};

const MEMBERS = {
  items: [
    { user_id: "u1", email: "owner@local", name: "Owner", role: "owner", created_at: "2026-01-01T00:00:00Z" },
    {
      user_id: "u2",
      email: "viewer@local",
      name: "Viewer",
      role: "viewer",
      created_at: "2026-01-02T00:00:00Z",
      pending: true,
    },
  ],
  total: 2,
};

function renderTeamTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TeamTab />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/auth/me")) return jsonResponse(ME);
      if (url.includes("/workspaces/w1/members") && (!init?.method || init.method === "GET")) return jsonResponse(MEMBERS);
      if (url.includes("/invites")) {
        return jsonResponse({
          email: "new@example.com",
          role: "viewer",
          workspace_id: "w1",
          token: "tok",
          url: "http://localhost:3000/login?invite=tok",
          expires_at: "2026-02-01T00:00:00Z",
        });
      }
      return jsonResponse({});
    }),
  );
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("TeamTab", () => {
  it("lists members with a pending chip for an unredeemed invite", async () => {
    renderTeamTab();
    expect((await screen.findAllByText("owner@local")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("viewer@local").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Invited").length).toBeGreaterThan(0);
  });

  it("invites someone and shows the one-time link", async () => {
    renderTeamTab();
    await screen.findAllByText("owner@local");

    fireEvent.click(screen.getByRole("button", { name: "Invite" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Email"), { target: { value: "new@example.com" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Send invite" }));

    expect(await within(dialog).findByText("http://localhost:3000/login?invite=tok")).toBeTruthy();
  });

  it("adds people only through Send invite, never the member route that answers 409 use_invite (R-V2-30)", async () => {
    renderTeamTab();
    await screen.findAllByText("owner@local");

    fireEvent.click(screen.getByRole("button", { name: "Invite" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/confirm with\s+their own password if they already have an account/)).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText("Email"), { target: { value: "alice@b.example" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Send invite" }));
    await within(dialog).findByText("http://localhost:3000/login?invite=tok");

    const writes = vi
      .mocked(fetch)
      .mock.calls.filter(([, init]) => init?.method && init.method !== "GET")
      .map(([input, init]) => `${init?.method} ${String(input)}`);
    expect(writes).toHaveLength(1);
    expect(writes[0]).toMatch(/^POST .*\/workspaces\/w1\/invites$/);
  });
});
