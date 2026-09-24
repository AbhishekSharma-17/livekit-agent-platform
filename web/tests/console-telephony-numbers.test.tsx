import * as React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { NumbersSection, GetNumberDialog } from "@/components/console/telephony/numbers-section";
import { RulesSection } from "@/components/console/telephony/rules-section";
import { TrunkDialog } from "@/components/console/telephony/trunks-section";
import { ATTACH_STATE_META, attachStateOf, LK_PURCHASE_COMMAND } from "@/components/console/telephony/model";
import type {
  AgentOut,
  ConnectionOut,
  DispatchRuleOut,
  NumbersRefreshOut,
  PhoneNumberOut,
  TrunkOut,
} from "@/contracts/lkap-contracts";

/**
 * Numbers section with LiveKit-hosted numbers (V4-05, PHONE-NUMBERS.md §6).
 * The api is mocked at `fetch`; assertions are on what the UI renders and sends.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/console/telephony",
  useSearchParams: () => new URLSearchParams(),
}));

const toasts = vi.hoisted(() => ({ success: vi.fn(), warning: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast: toasts }));

beforeAll(() => {
  const matches = Element.prototype.matches;
  Element.prototype.matches = function (this: Element, selector: string) {
    if (selector === ":popover-open" || selector === ":modal") return false;
    return matches.call(this, selector);
  };
});

class StubResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
beforeEach(() => {
  vi.stubGlobal("ResizeObserver", StubResizeObserver);
  Object.values(toasts).forEach((fn) => fn.mockClear());
});
afterEach(() => vi.unstubAllGlobals());

const CONN_A: ConnectionOut = {
  id: "conn-a",
  slug: "cloud-a",
  name: "Cloud A",
  url: "wss://cloud-a.livekit.cloud",
  is_default: true,
  capabilities: { sip_enabled: true },
};
const CONN_B: ConnectionOut = { ...CONN_A, id: "conn-b", slug: "cloud-b", name: "Cloud B", is_default: false };
const AGENT_A = { id: "agent-a", slug: "front", name: "Front desk", connection_id: "conn-a" } as AgentOut;
const AGENT_B = { id: "agent-b", slug: "other", name: "Other project bot", connection_id: "conn-b" } as AgentOut;
const IN_TRUNK: TrunkOut = {
  id: "trunk-in",
  connection_id: "conn-a",
  direction: "inbound",
  name: "Twilio in",
  lk_trunk_id: "ST_in_1",
  numbers: ["+15551230000"],
  provider_hint: "twilio",
  address: null,
  auth_username: null,
  has_password: false,
  created_at: "2026-09-23T10:00:00Z",
  updated_at: "2026-09-23T10:00:00Z",
};
const TRUNK_NUMBER: PhoneNumberOut = {
  id: "num-trunk",
  e164: "+15551230000",
  source: "trunk",
  trunk_id: "trunk-in",
  inbound_agent_id: null,
  label: "Main line",
  dispatch_rule_id: null,
  attach_state: "not_routed",
};
const HOSTED: PhoneNumberOut = {
  id: "num-hosted",
  e164: "+15550100001",
  source: "livekit",
  trunk_id: null,
  connection_id: "conn-a",
  inbound_agent_id: "agent-a",
  label: "",
  dispatch_rule_id: "rule-hosted",
  lk_number_id: "PN_1",
  lk_status: "active",
  lk_inbound_status: "active",
  lk_rule_ids: ["SDR_1"],
  attach_state: "routed",
  region: "San Francisco, CA",
  lk_synced_at: "2026-09-25T10:00:00Z",
};

interface Recorded {
  method: string;
  path: string;
  body: unknown;
}

function stubApi(overrides: Record<string, unknown> = {}) {
  const requests: Recorded[] = [];
  const routes: Record<string, unknown> = {
    "GET auth/me": {
      user: { id: "u1", email: "admin@example.test" },
      workspaces: [{ id: "ws1", name: "Test workspace", slug: "test", role: "admin" }],
    },
    "GET connections": { items: [CONN_A], total: 1 },
    "GET telephony/trunks": { items: [IN_TRUNK], total: 1 },
    "GET telephony/numbers": { items: [TRUNK_NUMBER, HOSTED], total: 2 },
    ...overrides,
  };
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://web.test");
    const path = url.pathname.replace(/^\/api\/console\//, "");
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    requests.push({ method, path, body });
    const payload = routes[`${method} ${path}`] ?? {};
    return { ok: true, status: 200, json: async () => payload } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return requests;
}

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

async function numbersTable() {
  return within(await screen.findByRole("table", { name: "Phone numbers" }));
}

describe("attach-state chips", () => {
  it.each([
    ["routed", "Routed", "success"],
    ["detached", "Detached", "warning"],
    ["not_routed", "Not routed", "neutral"],
    ["pending", "Pending", "info"],
    ["offline", "Offline", "warning"],
    ["released", "Released", "neutral"],
  ] as const)("%s reads %s in the %s tone", async (state, label, tone) => {
    expect(ATTACH_STATE_META[state]).toMatchObject({ label, tone });
    stubApi({ "GET telephony/numbers": { items: [{ ...HOSTED, attach_state: state }], total: 1 } });
    renderWithClient(<NumbersSection agents={[AGENT_A]} />);
    const table = await numbersTable();
    const chip = await table.findByText(label);
    expect(chip.closest("[data-tone]")?.getAttribute("data-tone")).toBe(tone);
  });

  it("falls back to the rule id for payloads without attach_state", () => {
    expect(attachStateOf({ dispatch_rule_id: "r1" })).toBe("routed");
    expect(attachStateOf({ dispatch_rule_id: null })).toBe("not_routed");
  });
});

describe("NumbersSection with LiveKit-hosted numbers", () => {
  it("shows the source chip, the region and the sync time", async () => {
    stubApi();
    renderWithClient(<NumbersSection agents={[AGENT_A]} />);
    const table = await numbersTable();

    expect(await table.findByText("LiveKit")).toBeTruthy();
    expect(table.getByText("Twilio in")).toBeTruthy();
    expect(table.getByText("San Francisco, CA")).toBeTruthy();
    expect(table.getByText(/Synced/)).toBeTruthy();
  });

  it("refreshes from LiveKit and reports what changed, one line per conflict", async () => {
    const result: NumbersRefreshOut = {
      connection_id: "conn-a",
      seen: 2,
      added: 1,
      updated: 1,
      released: 0,
      conflicts: ["+15551230000"],
      warnings: [],
    };
    const requests = stubApi({ "POST telephony/numbers/refresh": [result] });
    renderWithClient(<NumbersSection agents={[AGENT_A]} />);
    const button = (await screen.findByRole("button", { name: "Refresh from LiveKit" })) as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false));

    fireEvent.click(button);

    await waitFor(() =>
      expect(requests).toContainEqual({
        method: "POST",
        path: "telephony/numbers/refresh",
        body: { connection_id: "conn-a" },
      }),
    );
    await waitFor(() => expect(toasts.success).toHaveBeenCalledWith("Refreshed from LiveKit: 1 added, 1 updated, 0 released"));
    expect(toasts.warning).toHaveBeenCalledWith("+15551230000 is already registered as a trunk number and was skipped");
  });

  it("offers a connection picker when several connections have SIP", async () => {
    const requests = stubApi({
      "GET connections": { items: [CONN_A, CONN_B], total: 2 },
      "POST telephony/numbers/refresh": [],
    });
    renderWithClient(<NumbersSection agents={[AGENT_A]} />);
    const picker = (await screen.findByLabelText("Connection to refresh")) as HTMLSelectElement;
    fireEvent.change(picker, { target: { value: "conn-b" } });
    const button = screen.getByRole("button", { name: "Refresh from LiveKit" }) as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false));

    fireEvent.click(button);

    await waitFor(() =>
      expect(requests).toContainEqual({ method: "POST", path: "telephony/numbers/refresh", body: { connection_id: "conn-b" } }),
    );
  });

  it("disables Refresh when no connection reports SIP", async () => {
    stubApi({ "GET connections": { items: [{ ...CONN_A, capabilities: { sip_enabled: false } }], total: 1 } });
    renderWithClient(<NumbersSection agents={[AGENT_A]} />);
    await numbersTable();

    const button = screen.getByRole("button", { name: "Refresh from LiveKit" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.title).toMatch(/SIP/);
  });

  it("re-attaches a detached number by sending its current agent again", async () => {
    const requests = stubApi({
      "GET telephony/numbers": { items: [{ ...HOSTED, attach_state: "detached" }], total: 1 },
      "PUT telephony/numbers/num-hosted": { ...HOSTED, warnings: [] },
    });
    renderWithClient(<NumbersSection agents={[AGENT_A]} />);
    const table = await numbersTable();
    const reattach = (await table.findByRole("button", { name: "Re-attach +15550100001" })) as HTMLButtonElement;
    await waitFor(() => expect(reattach.disabled).toBe(false));

    fireEvent.click(reattach);

    await waitFor(() =>
      expect(requests).toContainEqual({
        method: "PUT",
        path: "telephony/numbers/num-hosted",
        body: { inbound_agent_id: "agent-a" },
      }),
    );
  });

  it("lists only the agents of the number's own connection in a hosted number's picker", async () => {
    stubApi({ "GET connections": { items: [CONN_A, CONN_B], total: 2 } });
    renderWithClient(<NumbersSection agents={[AGENT_A, AGENT_B]} />);
    const table = await numbersTable();

    const hosted = (await table.findByLabelText("Inbound agent for +15550100001")) as HTMLSelectElement;
    await waitFor(() => expect(Array.from(hosted.options).map((o) => o.text)).toEqual(["Nobody", "Front desk"]));
    const trunk = table.getByLabelText("Inbound agent for +15551230000") as HTMLSelectElement;
    expect(Array.from(trunk.options).map((o) => o.text)).toEqual(["Nobody", "Front desk", "Other project bot"]);
  });

  it("keeps an offline hosted number assignable (a new number starts offline)", async () => {
    stubApi({
      "GET telephony/numbers": {
        items: [{ ...HOSTED, inbound_agent_id: null, attach_state: "offline", lk_status: "offline" }],
        total: 1,
      },
    });
    renderWithClient(<NumbersSection agents={[AGENT_A]} />);
    const picker = (await (await numbersTable()).findByLabelText("Inbound agent for +15550100001")) as HTMLSelectElement;

    await waitFor(() => expect(picker.disabled).toBe(false));
  });

  it("confirms a hosted number's delete with copy that says it stays in LiveKit", async () => {
    const requests = stubApi({ "DELETE telephony/numbers/num-hosted": {} });
    renderWithClient(<NumbersSection agents={[AGENT_A]} />);
    const table = await numbersTable();
    const remove = (await table.findByRole("button", { name: "Delete +15550100001" })) as HTMLButtonElement;
    await waitFor(() => expect(remove.disabled).toBe(false));

    fireEvent.click(remove);

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/stays in your LiveKit project/)).toBeTruthy();
    expect(requests.some((r) => r.method === "DELETE")).toBe(false);
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove number" }));
    await waitFor(() => expect(requests).toContainEqual({ method: "DELETE", path: "telephony/numbers/num-hosted", body: undefined }));
  });

  it("uses trunk copy when deleting a trunk number", async () => {
    stubApi();
    renderWithClient(<NumbersSection agents={[AGENT_A]} />);
    const table = await numbersTable();
    const remove = (await table.findByRole("button", { name: "Delete +15551230000" })) as HTMLButtonElement;
    await waitFor(() => expect(remove.disabled).toBe(false));

    fireEvent.click(remove);

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/trunk keeps the number/)).toBeTruthy();
  });

  it("shows both actions and the three steps when there are no numbers", async () => {
    stubApi({ "GET telephony/numbers": { items: [], total: 0 } });
    renderWithClient(<NumbersSection agents={[AGENT_A]} />);

    expect(await screen.findByText("No numbers yet")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Refresh from LiveKit" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add number" })).toBeTruthy();
    expect(screen.getByText(LK_PURCHASE_COMMAND)).toBeTruthy();
  });
});

describe("GetNumberDialog", () => {
  it("renders the three steps as text only, with no key or url", () => {
    renderWithClient(<GetNumberDialog open onOpenChange={() => {}} />);
    const dialog = screen.getByRole("dialog");

    expect(within(dialog).getAllByRole("listitem")).toHaveLength(3);
    expect(within(dialog).getByText(/Telephony → Phone numbers/)).toBeTruthy();
    expect(within(dialog).getByText(LK_PURCHASE_COMMAND)).toBeTruthy();
    expect(within(dialog).getByText(/US numbers only, inbound only/)).toBeTruthy();
    const text = dialog.textContent ?? "";
    expect(text).not.toMatch(/lkap_|api[-_ ]?key|secret|https?:\/\//i);
    expect(within(dialog).queryByRole("link")).toBeNull();
  });

  it("opens from the section header", async () => {
    stubApi();
    renderWithClient(<NumbersSection agents={[AGENT_A]} />);
    fireEvent.click(await screen.findByRole("button", { name: "Get a number" }));

    expect(await screen.findByRole("heading", { name: "Get a LiveKit phone number" })).toBeTruthy();
  });
});

describe("RulesSection with a trunk-less rule", () => {
  it("names the hosted number where the trunk would be", async () => {
    const rule: DispatchRuleOut = {
      id: "rule-hosted",
      connection_id: "conn-a",
      lk_rule_id: "SDR_1",
      trunk_id: null,
      phone_number_id: "num-hosted",
      agent_id: "agent-a",
      numbers: ["+15550100001"],
      room_prefix: "call-",
      has_pin: false,
      managed_by_number: "+15550100001",
      created_at: "2026-09-25T10:00:00Z",
    };
    stubApi({ "GET telephony/dispatch-rules": { items: [rule], total: 1 } });
    renderWithClient(<RulesSection agents={[AGENT_A]} />);
    const table = within(await screen.findByRole("table", { name: "Dispatch rules" }));

    expect(await table.findByText("LiveKit number +15550100001")).toBeTruthy();
    expect(table.getByText("From number")).toBeTruthy();
  });
});

describe("TrunkDialog with Telnyx (V4-05 addition)", () => {
  it("offers Telnyx and names sip.telnyx.com and the username header for an outbound Telnyx trunk", () => {
    stubApi();
    renderWithClient(<TrunkDialog open onOpenChange={() => {}} connections={[CONN_A]} />);

    const carrier = screen.getByLabelText(/Carrier/) as HTMLSelectElement;
    expect(Array.from(carrier.options).map((o) => o.text)).toContain("Telnyx");
    fireEvent.change(screen.getByLabelText(/Direction/), { target: { value: "outbound" } });
    fireEvent.change(carrier, { target: { value: "telnyx" } });

    expect(screen.getByText("Telnyx: sip.telnyx.com")).toBeTruthy();
    expect(screen.getByText(/X-Telnyx-Username/)).toBeTruthy();
  });
});

describe("dialogs only (R-V3-2)", () => {
  it("imports no sheet in the telephony numbers and rules sections", () => {
    const dir = join(__dirname, "..", "src", "components", "console", "telephony");
    for (const file of ["numbers-section.tsx", "rules-section.tsx", "hooks.ts", "model.ts"]) {
      expect(readFileSync(join(dir, file), "utf8")).not.toMatch(/@\/components\/ui\/sheet/);
    }
  });

  it("never offers to buy or give back a number in the console", () => {
    const dir = join(__dirname, "..", "src", "components", "console", "telephony");
    for (const file of ["numbers-section.tsx", "hooks.ts", "model.ts"]) {
      expect(readFileSync(join(dir, file), "utf8")).not.toMatch(/PurchasePhoneNumber|ReleasePhoneNumbers|numbers\/purchase/);
    }
  });
});
