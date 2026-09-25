import * as React from "react";

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { AppGallery } from "@/components/console/tools/apps/app-gallery";
import { ConnectAppDialog } from "@/components/console/tools/apps/connect-app-dialog";
import { ActionsDialog } from "@/components/console/tools/apps/actions-dialog";
import { useToolProviderToolkits } from "@/components/console/lib/api-hooks";
import {
  actionFixture,
  actionPage,
  connectionFixture,
  toolkitFixture,
  toolkitPage,
  TOOLKIT_GITHUB_CONNECTED,
  TOOLKIT_SLACK,
} from "./fixtures/apps";

/**
 * The app gallery, the Connect dialog and the Actions dialog (docs/v5/COMPOSIO.md
 * §6): every acceptance bullet on the V5-22 card that starts "Gallery:",
 * "Connect:" or "Actions:".
 */

// jsdom has no ResizeObserver; the shadcn `Select` (category filter, the
// agent pickers) needs one to mount (see console-http-tool-editor.test.tsx —
// the only other suite in this repo that renders one).
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const nativeMatches = Element.prototype.matches;
beforeAll(() => {
  Element.prototype.matches = function matches(this: Element, selector: string) {
    if (selector === ":popover-open" || selector === ":modal") return false;
    return nativeMatches.call(this, selector);
  };
});
afterAll(() => {
  Element.prototype.matches = nativeMatches;
});

// `vi.unstubAllGlobals()` runs after every test (it also clears the fetch
// stub), so the `ResizeObserver` stub is re-applied per test rather than
// once in `beforeAll`.
beforeEach(() => {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
});

interface Call {
  url: string;
  method: string;
  body: Record<string, unknown> | undefined;
}

// No `@testing-library/jest-dom` in this project (see console-create-agent.test.tsx
// for the same pattern) — check the native `disabled` property instead of `.toBeDisabled()`.
function isDisabled(element: HTMLElement): boolean {
  return (element as HTMLButtonElement).disabled;
}

function bodyField(call: Call, path: string[]): unknown {
  let value: unknown = call.body;
  for (const key of path) {
    if (typeof value !== "object" || value === null) return undefined;
    value = (value as Record<string, unknown>)[key];
  }
  return value;
}

function stubApi(overrides: (call: Call) => { status: number; body: unknown } | undefined = () => undefined) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      const call = { url: String(input), method: init?.method ?? "GET", body };
      calls.push(call);
      const override = overrides(call);
      let status = 200;
      let responseBody: unknown;
      if (override) ({ status, body: responseBody } = override);
      else if (call.url.includes("/auth/me")) {
        responseBody = {
          user: { id: "u1", email: "admin@example.test" },
          workspaces: [{ id: "ws1", name: "Test workspace", slug: "test", role: "admin" }],
        };
      } else if (/\/toolkits\/[^/?]+\/actions/.test(call.url)) {
        responseBody = actionPage();
      } else if (/\/toolkits\/[^/?]+$/.test(call.url)) {
        responseBody = toolkitFixture();
      } else if (call.url.includes("/tool-providers/composio/toolkits")) {
        responseBody = toolkitPage();
      } else if (call.url.includes("/tool-providers/composio/connections") && call.method === "GET") {
        responseBody = { items: [], total: 0 };
      } else if (call.url.includes("/agents")) {
        responseBody = { items: [], total: 0 };
      } else responseBody = {};
      return { ok: status < 400, status, json: async () => responseBody } as Response;
    }),
  );
  return calls;
}

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

/**
 * `ConnectAppDialog` supports both a controlled `open`/`onOpenChange` pair
 * and an uncontrolled `trigger`; passing a fixed `open` with a no-op
 * `onOpenChange` would make it un-closeable (`open = openProp ?? openState`
 * always picks the prop once it's not `undefined`), so tests that need to
 * see it close use this real, stateful wrapper instead.
 */
function ControlledConnectDialog({ toolkit }: { toolkit: { slug: string; name: string } }) {
  const [open, setOpen] = React.useState(true);
  return <ConnectAppDialog toolkit={toolkit} open={open} onOpenChange={setOpen} />;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useToolProviderToolkits — the gallery's hook", () => {
  it("sends search, category and connected_only to the toolkits route", async () => {
    const calls = stubApi();
    const { result } = renderHook(
      () => useToolProviderToolkits({ query: "calendar", category: "Productivity", connectedOnly: true, limit: 24 }),
      { wrapper: wrapper() },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const call = calls.find((c) => c.url.includes("/tool-providers/composio/toolkits?"));
    expect(call?.url).toContain("query=calendar");
    expect(call?.url).toContain("category=Productivity");
    expect(call?.url).toContain("connected_only=true");
  });

  it("pages by cursor and appends with fetchNextPage", async () => {
    const page1 = toolkitPage([toolkitFixture()], "cursor2");
    const page2 = toolkitPage([TOOLKIT_SLACK], null);
    const calls = stubApi((call) => {
      if (!call.url.includes("/tool-providers/composio/toolkits")) return undefined;
      return call.url.includes("cursor=cursor2") ? { status: 200, body: page2 } : { status: 200, body: page1 };
    });
    const { result } = renderHook(() => useToolProviderToolkits({}), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.pages).toHaveLength(1);
    await result.current.fetchNextPage();
    await waitFor(() => expect(result.current.data?.pages).toHaveLength(2));
    const items = result.current.data!.pages.flatMap((p) => p.items);
    expect(items.map((i) => i.slug)).toEqual(["googlecalendar", "slack"]);
    expect(calls.some((c) => c.url.includes("cursor=cursor2"))).toBe(true);
  });
});

describe("AppGallery", () => {
  it("filters to search text after the debounce", async () => {
    const calls = stubApi();
    renderWithClient(<AppGallery />);
    await screen.findByText("Google Calendar");
    fireEvent.change(screen.getByLabelText("Search apps"), { target: { value: "slack" } });
    await waitFor(() => expect(calls.some((c) => c.url.includes("query=slack"))).toBe(true), { timeout: 2000 });
  });

  it("the Connected only toggle filters by connected_only", async () => {
    const calls = stubApi();
    renderWithClient(<AppGallery />);
    await screen.findByText("Google Calendar");
    fireEvent.click(screen.getByLabelText("Connected only"));
    await waitFor(() => expect(calls.some((c) => c.url.includes("connected_only=true"))).toBe(true));
  });

  it("Load more appends the next page", async () => {
    const page1 = toolkitPage([toolkitFixture()], "cursor2");
    const page2 = toolkitPage([TOOLKIT_SLACK], null);
    stubApi((call) => {
      if (!call.url.includes("/tool-providers/composio/toolkits")) return undefined;
      return call.url.includes("cursor=cursor2") ? { status: 200, body: page2 } : { status: 200, body: page1 };
    });
    renderWithClient(<AppGallery />);
    await screen.findByText("Google Calendar");
    expect(screen.queryByText("Slack")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    await screen.findByText("Slack");
    expect(screen.getByText("Google Calendar")).toBeTruthy();
  });

  it("shows a connected app's live status row instead of a Connect button", async () => {
    stubApi((call) => {
      if (call.url.includes("/tool-providers/composio/toolkits") && !/\/toolkits\/[^/?]+/.test(call.url)) {
        return { status: 200, body: toolkitPage([TOOLKIT_GITHUB_CONNECTED]) };
      }
      if (call.url.endsWith("/connections/conn_github")) return { status: 200, body: connectionFixture() };
      return undefined;
    });
    renderWithClient(<AppGallery />);
    await screen.findByText("GitHub");
    // "Connected" appears twice (the card's badge and the row's status chip)
    // — the row's own actions are the unambiguous signal.
    expect((await screen.findAllByText("Connected")).length).toBeGreaterThan(0);
    expect(await screen.findByRole("button", { name: "Disconnect" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Actions" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
  });
});

describe("ConnectAppDialog", () => {
  it("Managed: opens the returned URL in a new tab and shows Connected", async () => {
    const openSpy = vi.spyOn(window, "open").mockReturnValue(null);
    stubApi((call) => {
      if (call.url.endsWith("/tool-providers/composio/connections") && call.method === "POST") {
        return {
          status: 201,
          body: { connection_id: "conn_1", status: "initiated", redirect_url: "https://backend.composio.dev/consent", expires_at: null },
        };
      }
      if (call.url.endsWith("/connections/conn_1")) return { status: 200, body: connectionFixture({ id: "conn_1", status: "active" }) };
      return undefined;
    });
    renderWithClient(<ControlledConnectDialog toolkit={{ slug: "googlecalendar", name: "Google Calendar" }} />);

    const dialog = await screen.findByRole("dialog", { name: "Connect Google Calendar" });
    await within(dialog).findByText("Managed — one click");
    fireEvent.click(within(dialog).getByRole("button", { name: "Connect" }));

    await waitFor(() => expect(openSpy).toHaveBeenCalledWith("https://backend.composio.dev/consent", "_blank", "noopener"));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("Failed shows Retry", async () => {
    vi.spyOn(window, "open").mockReturnValue(null);
    const calls = stubApi((call) => {
      if (call.url.endsWith("/tool-providers/composio/connections") && call.method === "POST") {
        return { status: 201, body: { connection_id: "conn_2", status: "failed", redirect_url: null, expires_at: null } };
      }
      if (call.url.endsWith("/connections/conn_2") && call.method === "GET") {
        return { status: 200, body: connectionFixture({ id: "conn_2", status: "failed" }) };
      }
      if (call.url.endsWith("/connections/conn_2/reconnect")) {
        return { status: 200, body: { connection_id: "conn_2", status: "initiated", redirect_url: "https://backend.composio.dev/consent2" } };
      }
      return undefined;
    });
    renderWithClient(<ControlledConnectDialog toolkit={{ slug: "googlecalendar", name: "Google Calendar" }} />);
    const dialog = await screen.findByRole("dialog", { name: "Connect Google Calendar" });
    await within(dialog).findByText("Managed — one click");
    fireEvent.click(within(dialog).getByRole("button", { name: "Connect" }));

    expect(await within(dialog).findByText("Failed")).toBeTruthy();
    const retry = within(dialog).getByRole("button", { name: "Retry" });
    fireEvent.click(retry);
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/connections/conn_2/reconnect"))).toBe(true));
  });

  it("API key: posts fields and never renders the value again", async () => {
    const calls = stubApi((call) => {
      if (/\/toolkits\/[^/?]+$/.test(call.url)) return { status: 200, body: TOOLKIT_SLACK };
      if (call.url.endsWith("/tool-providers/composio/connections") && call.method === "POST") {
        return { status: 201, body: { connection_id: "conn_3", status: "active", redirect_url: null, expires_at: null } };
      }
      return undefined;
    });
    renderWithClient(<ControlledConnectDialog toolkit={{ slug: "slack", name: "Slack" }} />);
    const dialog = await screen.findByRole("dialog", { name: "Connect Slack" });
    const input = await within(dialog).findByLabelText("Bot token");
    fireEvent.change(input, { target: { value: "xoxb-secret-token" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Connect" }));

    await waitFor(() =>
      expect(calls.some((c) => c.method === "POST" && bodyField(c, ["fields", "api_key"]) === "xoxb-secret-token")).toBe(true),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.queryByDisplayValue("xoxb-secret-token")).toBeNull();
  });
});

describe("ActionsDialog", () => {
  it("a destructive action needs the confirm before it can be added", async () => {
    stubApi();
    renderWithClient(
      <ActionsDialog connectionId="conn_github" toolkitSlug="github" toolkitName="GitHub" pickedActions={[]} open onOpenChange={() => {}} />,
    );
    const dialog = await screen.findByRole("dialog", { name: "GitHub actions" });
    await within(dialog).findByText("List repositories");
    fireEvent.click(within(dialog).getByRole("checkbox", { name: /Delete a repository/ }));

    expect(isDisabled(within(dialog).getByRole("button", { name: "Add as tools" }))).toBe(true);
    fireEvent.click(within(dialog).getByRole("checkbox", { name: /I understand/ }));
    expect(isDisabled(within(dialog).getByRole("button", { name: "Add as tools" }))).toBe(false);
  });

  it("Add as tools posts materialise with the picked slugs", async () => {
    const calls = stubApi();
    renderWithClient(
      <ActionsDialog connectionId="conn_github" toolkitSlug="github" toolkitName="GitHub" pickedActions={[]} open onOpenChange={() => {}} />,
    );
    const dialog = await screen.findByRole("dialog", { name: "GitHub actions" });
    await within(dialog).findByText("List repositories");
    fireEvent.click(within(dialog).getByRole("checkbox", { name: /List repositories/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Add as tools" }));

    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/tool-providers/composio/materialise") && c.method === "POST")).toBe(true));
    const call = calls.find((c) => c.url.endsWith("/tool-providers/composio/materialise"))!;
    expect(bodyField(call, ["connection_id"])).toBe("conn_github");
    expect(bodyField(call, ["actions"])).toEqual(["GITHUB_LIST_REPOS"]);
    expect(bodyField(call, ["allow_destructive"])).toBe(false);
  });

  it("shows already-picked actions as checked and locked", async () => {
    stubApi();
    renderWithClient(
      <ActionsDialog
        connectionId="conn_github"
        toolkitSlug="github"
        toolkitName="GitHub"
        pickedActions={[actionFixture().slug]}
        open
        onOpenChange={() => {}}
      />,
    );
    const dialog = await screen.findByRole("dialog", { name: "GitHub actions" });
    const checkbox = await within(dialog).findByRole("checkbox", { name: /List repositories/ });
    expect(isDisabled(checkbox)).toBe(true);
    expect(checkbox.getAttribute("aria-checked")).toBe("true");
  });
});
