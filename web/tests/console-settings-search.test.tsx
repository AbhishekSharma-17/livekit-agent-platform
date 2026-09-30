import * as React from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Highlight, matchesQuery, normalizeText } from "@/components/console/settings/list-search";
import { TeamTab } from "@/components/console/settings/team-tab";

/**
 * Settings list search (docs/ui/DESIGN-SYSTEM.md section 9): shown from 6
 * items, accent-insensitive, every word must match, matches highlighted,
 * Escape clears, a distinct no-matches state with Clear filters, and the
 * query remembered per list (validated on read).
 */

function stubLocalStorage(): Storage {
  const data = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key) => data.get(key) ?? null,
    key: (index) => Array.from(data.keys())[index] ?? null,
    removeItem: (key) => {
      data.delete(key);
    },
    setItem: (key, value) => {
      data.set(key, String(value));
    },
  };
  vi.stubGlobal("localStorage", storage);
  return storage;
}

function jsonResponse(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body } as Response;
}

function member(id: string, name: string, email: string, role = "viewer") {
  return { user_id: id, email, name, role, created_at: "2026-01-01T00:00:00Z" };
}

const MEMBERS = [
  member("u1", "Owner", "owner@local", "owner"),
  member("u2", "Zoë Martin", "zoe@example.test"),
  member("u3", "Arjun Rao", "arjun@example.test"),
  member("u4", "Chen Wei", "chen@example.test"),
  member("u5", "Fatima Khan", "fatima@example.test"),
  member("u6", "Lars Berg", "lars@example.test"),
];

function stubFetch(members = MEMBERS) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/auth/me")) {
        return jsonResponse({
          user: { id: "u1", email: "owner@local", name: "Owner" },
          workspaces: [{ id: "w1", slug: "default", name: "Default", role: "owner" }],
        });
      }
      if (url.includes("/workspaces/w1/members")) return jsonResponse({ items: members, total: members.length });
      return jsonResponse({});
    }),
  );
}

function renderTeam() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TeamTab />
    </QueryClientProvider>,
  );
}

/** The desktop table (the phone cards render the same rows; CSS picks one). */
async function table() {
  return within(await screen.findByRole("table", { name: "Team members" }));
}

let storage: Storage;

beforeEach(() => {
  storage = stubLocalStorage();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("matchesQuery / normalizeText", () => {
  it.each([
    [["Zoë Martin"], "zoe", true],
    [["Zoë Martin"], "ZOË mar", true],
    [["zoe@example.test", "Zoë Martin"], "example martin", true],
    [["Zoë Martin"], "zoe smith", false],
    [["Anything"], "   ", true],
  ])("matches %j against %j → %s", (fields, query, expected) => {
    expect(matchesQuery(fields, query)).toBe(expected);
  });

  it("folds accents and case", () => {
    expect(normalizeText("Émile ÇA")).toBe("emile ca");
  });
});

describe("Highlight", () => {
  it("marks the matched part of the original text, accents kept", () => {
    const { container } = render(<Highlight text="Zoë Martin" query="zoe" />);
    const marks = container.querySelectorAll("mark");
    expect(marks).toHaveLength(1);
    expect(marks[0].textContent).toBe("Zoë");
    expect(container.textContent).toBe("Zoë Martin");
  });

  it("renders plain text without a query", () => {
    const { container } = render(<Highlight text="Zoë Martin" query="" />);
    expect(container.querySelector("mark")).toBeNull();
  });
});

describe("Team list search", () => {
  it("hides the search field below 6 members", async () => {
    stubFetch(MEMBERS.slice(0, 5));
    renderTeam();
    await table();
    expect(screen.queryByRole("searchbox", { name: "Search members" })).toBeNull();
  });

  it("filters accent-insensitively, highlights, and clears on Escape", async () => {
    stubFetch();
    renderTeam();
    const rows = await table();
    const search = screen.getByRole("searchbox", { name: "Search members" });

    fireEvent.change(search, { target: { value: "zoe" } });
    expect(rows.queryByText("arjun@example.test")).toBeNull();
    expect(rows.getByText("Zoë").tagName).toBe("MARK");
    expect(storage.getItem("lkap:settings:search:team")).toBe("zoe");

    fireEvent.keyDown(search, { key: "Escape" });
    expect((search as HTMLInputElement).value).toBe("");
    expect(rows.getByText("arjun@example.test")).toBeTruthy();
    expect(storage.getItem("lkap:settings:search:team")).toBeNull();
  });

  it("shows a distinct no-matches state whose Clear filters brings the list back", async () => {
    stubFetch();
    renderTeam();
    await table();

    fireEvent.change(screen.getByRole("searchbox", { name: "Search members" }), { target: { value: "nobody here" } });
    expect(screen.getByText("No members match “nobody here”")).toBeTruthy();
    expect(screen.queryByRole("table", { name: "Team members" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(await screen.findByRole("table", { name: "Team members" })).toBeTruthy();
  });

  it("remembers the query per list, and ignores a stored value that fails validation", async () => {
    storage.setItem("lkap:settings:search:team", "lars");
    stubFetch();
    renderTeam();
    const rows = await table();
    await waitFor(() =>
      expect((screen.getByRole("searchbox", { name: "Search members" }) as HTMLInputElement).value).toBe("lars"),
    );
    expect(rows.queryByText("zoe@example.test")).toBeNull();
  });

  it("drops an over-long stored query instead of applying it", async () => {
    storage.setItem("lkap:settings:search:team", "x".repeat(500));
    stubFetch();
    renderTeam();
    const rows = await table();
    expect((screen.getByRole("searchbox", { name: "Search members" }) as HTMLInputElement).value).toBe("");
    expect(rows.getByText("zoe@example.test")).toBeTruthy();
  });
});
