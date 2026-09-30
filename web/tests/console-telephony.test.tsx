import * as React from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { CallNumberDialog, CallNumberDialogHost, CallNumberMenuItem } from "@/components/console/telephony/call-number";
import { telephonyExtension } from "@/components/console/telephony/extension";
import {
  callStatusMeta,
  connectionForAgent,
  isLiveCall,
  normalizeE164,
  splitNumbers,
} from "@/components/console/telephony/model";
import { TelephonyPage } from "@/components/console/telephony/telephony-page";
import {
  DialingPolicyCard,
  PLUS_ONE_WARNING,
  parseDraft,
  plusOneAlone,
  policyFromSettings,
} from "@/components/console/telephony/dialing-policy";
import { PhoneCallsCard, SmsContactsCard, VoicemailCard } from "@/components/console/telephony/tools-section";
import { EditorContextProvider, type EditorContextValue } from "@/components/console/agents/editor/editor-context";
import type { EditorIssue } from "@/components/console/agents/editor/validation-map";
import { TELEPHONY_TOOLS, BUILTIN_TOOLS } from "@/components/console/lib/constants";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import { FormProvider, useForm } from "react-hook-form";
import { TrunkDialog } from "@/components/console/telephony/trunks-section";
import type { AgentOut, CallOut, ConnectionOut, DispatchRuleOut, PhoneNumberOut, TrunkOut } from "@/contracts/lkap-contracts";

/**
 * `/console/telephony` and "Call a number" (V2-17). The api is mocked at
 * `fetch`; assertions are on the requests the UI sends.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/console/telephony",
  useSearchParams: () => new URLSearchParams(),
}));

beforeAll(() => {
  // Radix popovers in jsdom (editor README "Testing notes").
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
  // The custom Select (Radix) in jsdom, as in tests/ui-select.test.tsx.
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.releasePointerCapture = vi.fn();
});
afterEach(() => vi.unstubAllGlobals());

/** Open a custom Select from the keyboard and pick an option (its listbox is portaled to the body). */
function choose(trigger: HTMLElement, option: string | RegExp) {
  fireEvent.keyDown(trigger, { key: "ArrowDown" });
  fireEvent.click(screen.getByRole("option", { name: option }));
}

const SIP_CONN: ConnectionOut = {
  id: "conn-1",
  slug: "cloud-a",
  name: "cloud-a",
  url: "wss://cloud-a.livekit.cloud",
  is_default: true,
  capabilities: { sip_enabled: true },
};
const AGENT: AgentOut = { id: "agent-1", slug: "support", name: "Support bot", connection_id: "conn-1" } as AgentOut;
const IN_TRUNK: TrunkOut = {
  id: "trunk-in",
  connection_id: "conn-1",
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
const OUT_TRUNK: TrunkOut = { ...IN_TRUNK, id: "trunk-out", direction: "outbound", name: "Twilio out", lk_trunk_id: "ST_out_1" };
const NUMBER: PhoneNumberOut = {
  id: "num-1",
  e164: "+15551230000",
  trunk_id: "trunk-in",
  inbound_agent_id: null,
  label: "Main line",
  dispatch_rule_id: null,
};
const RULE: DispatchRuleOut = {
  id: "rule-1",
  connection_id: "conn-1",
  lk_rule_id: "SDR_1",
  trunk_id: "trunk-in",
  agent_id: "agent-1",
  numbers: [],
  room_prefix: "call-",
  has_pin: false,
  managed_by_number: null,
  created_at: "2026-09-23T10:00:00Z",
};
const CALL: CallOut = {
  id: "call-1",
  direction: "outbound",
  from_e164: "+15551230000",
  to_e164: "+15557654321",
  status: "answered",
  session_id: "sess-1",
  answered_at: "2026-09-23T10:00:00Z",
  started_at: "2026-09-23T09:59:50Z",
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
    "GET connections": { items: [SIP_CONN], total: 1 },
    "GET agents": { items: [AGENT], total: 1 },
    "GET telephony/trunks": { items: [IN_TRUNK, OUT_TRUNK], total: 2 },
    "GET telephony/numbers": { items: [NUMBER], total: 1 },
    "GET telephony/dispatch-rules": { items: [RULE], total: 1 },
    "GET calls": { items: [CALL], total: 1 },
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

describe("telephony model", () => {
  it("normalizes pasted numbers and splits lists", () => {
    expect(normalizeE164("+1 (555) 123-4567")).toBe("+15551234567");
    expect(splitNumbers("+1 555 123 0000, +15559990000\n")).toEqual(["+15551230000", "+15559990000"]);
  });

  it("labels statuses and knows which calls are live", () => {
    expect(callStatusMeta("answered")).toEqual({ label: "In call", tone: "live" });
    expect(callStatusMeta("no_answer").tone).toBe("warning");
    expect(isLiveCall({ status: "answered" })).toBe(true);
    expect(isLiveCall({ status: "ringing" })).toBe(false);
  });

  it("uses the agent's own connection, else the default", () => {
    const other: ConnectionOut = { ...SIP_CONN, id: "conn-2", is_default: false };
    expect(connectionForAgent({ connection_id: "conn-2" }, [SIP_CONN, other])?.id).toBe("conn-2");
    expect(connectionForAgent({ connection_id: null }, [other, SIP_CONN])?.id).toBe("conn-1");
  });

  it("plugs a Test call item and its dialog host into the editor", () => {
    expect(telephonyExtension.slots?.testCallItems).toEqual([CallNumberMenuItem]);
    expect(telephonyExtension.slots?.headerActions).toEqual([CallNumberDialogHost]);
  });
});

describe("TelephonyPage", () => {
  it("lists trunks, numbers, rules and calls", async () => {
    stubApi();
    renderWithClient(<TelephonyPage />);

    const trunks = within(await screen.findByRole("table", { name: "SIP trunks" }));
    expect(trunks.getByText("Twilio in")).toBeTruthy();
    expect(trunks.getByText("ST_out_1")).toBeTruthy();
    const numbers = within(await screen.findByRole("table", { name: "Phone numbers" }));
    expect(numbers.getByText("+15551230000")).toBeTruthy();
    expect(numbers.getByText("Not routed")).toBeTruthy();
    const rules = within(await screen.findByRole("table", { name: "Dispatch rules" }));
    expect(rules.getByText("Every number on the trunk")).toBeTruthy();
    const calls = within(await screen.findByRole("table", { name: "Calls" }));
    expect(calls.getByText("In call")).toBeTruthy();
    expect(calls.getByRole("link", { name: "Open" }).getAttribute("href")).toBe("/console/sessions/sess-1");
  });

  it("routes a number to an agent from the inbound agent picker", async () => {
    const requests = stubApi();
    renderWithClient(<TelephonyPage />);
    const table = within(await screen.findByRole("table", { name: "Phone numbers" }));
    const picker = (await table.findByRole("combobox", { name: "Inbound agent for +15551230000" })) as HTMLButtonElement;
    await waitFor(() => expect(picker.disabled).toBe(false));

    choose(picker, "Support bot");

    await waitFor(() =>
      expect(requests).toContainEqual({
        method: "PUT",
        path: "telephony/numbers/num-1",
        body: { inbound_agent_id: "agent-1" },
      }),
    );
  });

  it("warns when no connection has SIP and disables Add trunk", async () => {
    stubApi({ "GET connections": { items: [{ ...SIP_CONN, capabilities: { sip_enabled: false } }], total: 1 } });
    renderWithClient(<TelephonyPage />);

    expect(await screen.findByText(/None of your connections reports SIP/)).toBeTruthy();
    expect(((await screen.findByRole("button", { name: "Add trunk" })) as HTMLButtonElement).disabled).toBe(true);
  });

  // -------------------------------------------------- V2-20-5: role gating
  it("offers a builder (below the admin floor) no Add trunk/Add number/Add rule, only a read-only note", async () => {
    stubApi(meWith("builder"));
    renderWithClient(<TelephonyPage />);
    await screen.findByRole("table", { name: "Phone numbers" });

    // `/v1/telephony` writes need `admin` (auth/roles.py::ROUTE_POLICY),
    // stricter than every other console page's `builder` floor. Decision D12:
    // controls the person can't use are not rendered; the page's primary
    // becomes a note that names the next step.
    expect(await screen.findByText(/Ask an admin to change trunks, numbers or rules/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Add trunk" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add number" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add rule" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: /Inbound agent for/ })).toBeNull();
  });

  it("enables Add trunk/Add number/Add rule for an admin", async () => {
    stubApi(meWith("admin"));
    renderWithClient(<TelephonyPage />);
    await screen.findByRole("table", { name: "Phone numbers" });

    expect(((await screen.findByRole("button", { name: "Add trunk" })) as HTMLButtonElement).disabled).toBe(false);
    expect(((await screen.findByRole("button", { name: "Add number" })) as HTMLButtonElement).disabled).toBe(false);
    expect(((await screen.findByRole("button", { name: "Add rule" })) as HTMLButtonElement).disabled).toBe(false);
  });

  it("hangs up a live call from the calls log", async () => {
    const requests = stubApi({ "POST calls/call-1/hangup": { ...CALL, status: "completed" } });
    renderWithClient(<TelephonyPage />);
    const calls = within(await screen.findByRole("table", { name: "Calls" }));

    fireEvent.click(calls.getByRole("button", { name: "Hang up" }));

    await waitFor(() => expect(requests.some((r) => r.method === "POST" && r.path === "calls/call-1/hangup")).toBe(true));
  });

  // ----------------------------------- V5-32/36: AMD result and transfer outcome (#211)
  it("shows what answered the call and how it was handed over, with the full summary behind a dialog", async () => {
    const withOutcome: CallOut = {
      ...CALL,
      status: "transferred",
      amd_result: "machine-vm",
      transfer_mode: "warm",
      transfer_summary: "Ada Lee, claim C-1001, wants a callback about the delayed repair estimate.",
    };
    stubApi({ "GET calls": { items: [withOutcome], total: 1 } });
    renderWithClient(<TelephonyPage />);
    const calls = within(await screen.findByRole("table", { name: "Calls" }));

    expect(await calls.findByText("Answered by: voicemail")).toBeTruthy();
    expect(calls.getByText("Introduced first")).toBeTruthy();

    fireEvent.click(calls.getByRole("button", { name: "View summary" }));
    const dialog = await screen.findByRole("dialog", { name: "Call summary" });
    expect(within(dialog).getByText(/wants a callback about the delayed repair estimate/)).toBeTruthy();
  });

  it("shows nothing extra for a call where detection never ran", async () => {
    stubApi();
    renderWithClient(<TelephonyPage />);
    const calls = within(await screen.findByRole("table", { name: "Calls" }));
    await calls.findByText("In call");

    expect(calls.queryByText(/Answered by:/)).toBeNull();
    expect(calls.queryByRole("button", { name: "View summary" })).toBeNull();
  });

  // ------------------------------------------------ S3: calls search and the viewer's read-only log
  it("shows a live call's status but no call controls to a viewer (calls writes need a builder)", async () => {
    stubApi(meWith("viewer"));
    renderWithClient(<TelephonyPage />);
    const calls = within(await screen.findByRole("table", { name: "Calls" }));

    expect(await calls.findByText("In call")).toBeTruthy();
    await screen.findByText(/Ask an admin to change trunks, numbers or rules/);
    expect(calls.queryByRole("button", { name: "Hang up" })).toBeNull();
    expect(calls.queryByRole("button", { name: "Transfer" })).toBeNull();
  });

  it("searches the calls log once it has 6 calls, with a no-matches state", async () => {
    const data = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      get length() {
        return data.size;
      },
      clear: () => data.clear(),
      getItem: (key: string) => data.get(key) ?? null,
      key: (index: number) => Array.from(data.keys())[index] ?? null,
      removeItem: (key: string) => void data.delete(key),
      setItem: (key: string, value: string) => void data.set(key, String(value)),
    } satisfies Storage);
    const many: CallOut[] = Array.from({ length: 6 }, (_, index) => ({
      ...CALL,
      id: `call-${index}`,
      status: "completed",
      to_e164: `+1555765432${index}`,
    }));
    stubApi({ "GET calls": { items: many, total: many.length } });
    renderWithClient(<TelephonyPage />);
    const search = await screen.findByRole("searchbox", { name: "Search calls" });

    fireEvent.change(search, { target: { value: "+15557654324" } });
    await waitFor(() =>
      expect(within(screen.getByRole("table", { name: "Calls" })).getAllByRole("row")).toHaveLength(2),
    );

    fireEvent.change(search, { target: { value: "+4420" } });
    expect(await screen.findByText("No calls match “+4420”")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Clear filters" })).toBeTruthy();
  });
});

describe("TrunkDialog", () => {
  it("creates an outbound trunk with its address and numbers", async () => {
    const requests = stubApi({ "POST telephony/trunks": OUT_TRUNK });
    const onOpenChange = vi.fn();
    renderWithClient(<TrunkDialog open onOpenChange={onOpenChange} connections={[SIP_CONN]} />);

    choose(screen.getByLabelText(/Direction/), "Outbound (place calls)");
    fireEvent.change(screen.getByLabelText(/^Name/), { target: { value: "Twilio out" } });
    fireEvent.change(screen.getByLabelText(/^Numbers/), { target: { value: "+1 555 123 9999" } });
    fireEvent.change(screen.getByLabelText(/SIP address/), { target: { value: "example.pstn.twilio.com" } });
    fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: "s3cret" } });
    fireEvent.click(screen.getByRole("button", { name: "Create trunk" }));

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(requests.find((r) => r.method === "POST")).toEqual({
      method: "POST",
      path: "telephony/trunks",
      body: {
        connection_id: "conn-1",
        direction: "outbound",
        name: "Twilio out",
        numbers: ["+15551239999"],
        provider_hint: "twilio",
        address: "example.pstn.twilio.com",
        auth_username: null,
        auth_password: "s3cret",
      },
    });
  });

  it("rejects a number that is not E.164 without calling the api", async () => {
    const requests = stubApi();
    renderWithClient(<TrunkDialog open onOpenChange={vi.fn()} connections={[SIP_CONN]} />);

    fireEvent.change(screen.getByLabelText(/^Name/), { target: { value: "In" } });
    fireEvent.change(screen.getByLabelText(/^Numbers/), { target: { value: "555-1234" } });
    fireEvent.click(screen.getByRole("button", { name: "Create trunk" }));

    expect((await screen.findByRole("alert")).textContent).toContain("5551234 is not an E.164 number");
    expect(requests.filter((r) => r.method === "POST")).toEqual([]);
  });
});

describe("Call a number", () => {
  it("places the call, then shows its live status and controls", async () => {
    const requests = stubApi({
      "POST calls": { ...CALL, status: "dialing" },
      "GET calls/call-1": CALL,
    });
    renderWithClient(<CallNumberDialog agent={AGENT} open onOpenChange={vi.fn()} />);

    const input = await screen.findByLabelText(/Phone number/);
    fireEvent.change(input, { target: { value: "+1 555 765 4321" } });
    fireEvent.click(screen.getByRole("button", { name: "Call" }));

    expect(await screen.findByText("In call")).toBeTruthy();
    expect(requests.find((r) => r.method === "POST")).toEqual({
      method: "POST",
      path: "calls",
      body: { agent_id: "agent-1", to_e164: "+15557654321", trunk_id: "trunk-out" },
    });
    expect(screen.getByRole("button", { name: "Hang up" })).toBeTruthy();
  });

  it("explains why it cannot call when the connection has no SIP", async () => {
    stubApi({ "GET connections": { items: [{ ...SIP_CONN, capabilities: {} }], total: 1 } });
    renderWithClient(<CallNumberDialog agent={AGENT} open onOpenChange={vi.fn()} />);

    expect(await screen.findByText(/SIP is not enabled on cloud-a/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Call" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("opens the dialog host from the Test call menu item", async () => {
    stubApi();
    renderWithClient(
      <>
        <DropdownMenu>
          <DropdownMenuTrigger>More test options</DropdownMenuTrigger>
          <DropdownMenuContent>
            <CallNumberMenuItem agent={AGENT} dirty={false} />
          </DropdownMenuContent>
        </DropdownMenu>
        <CallNumberDialogHost agent={AGENT} />
      </>,
    );
    expect(screen.queryByRole("dialog")).toBeNull();

    fireEvent.keyDown(screen.getByText("More test options"), { key: "Enter" });
    fireEvent.click(await screen.findByRole("menuitem", { name: /Call a number/ }));

    expect(await screen.findByRole("dialog", { name: "Call a number" })).toBeTruthy();
  });
});

// ------------------------------------------------ V2-19: dialing policy (R-V2-23)
function meWith(role: "owner" | "admin" | "builder" | "viewer") {
  return {
    "GET auth/me": {
      user: { id: "u-1", email: "a@b.c", name: "A" },
      workspaces: [{ id: "ws-1", slug: "default", name: "Default", role }],
    },
  };
}

describe("DialingPolicyCard", () => {
  it("says outbound calls are off when the workspace has no policy", async () => {
    stubApi({ ...meWith("admin"), "GET workspaces": { items: [{ id: "ws-1", settings: {} }], total: 1 } });
    renderWithClient(<DialingPolicyCard />);

    expect(await screen.findByText("Outbound calls off")).toBeTruthy();
    expect(screen.getByText(/nobody can place or transfer a call/)).toBeTruthy();
  });

  it("saves the whole telephony object through PUT /v1/workspaces/{id}", async () => {
    const requests = stubApi({
      ...meWith("admin"),
      "GET workspaces": {
        items: [{ id: "ws-1", settings: { timezone: "UTC", telephony: { allowed_prefixes: ["+1"] } } }],
        total: 1,
      },
    });
    renderWithClient(<DialingPolicyCard />);
    expect(await screen.findByText("Outbound calls on")).toBeTruthy();
    const prefixes = await screen.findByLabelText("Allowed number prefixes");
    await waitFor(() => expect((prefixes as HTMLInputElement).value).toBe("+1"));

    fireEvent.change(prefixes, { target: { value: "+1, +44 20" } });
    fireEvent.change(screen.getByLabelText(/Allowed SIP hosts/), { target: { value: "PBX.example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Save policy" }));

    await waitFor(() => expect(requests.some((r) => r.method === "PUT")).toBe(true));
    const put = requests.find((r) => r.method === "PUT");
    expect(put?.path).toBe("workspaces/ws-1");
    expect(put?.body).toEqual({
      settings: {
        telephony: {
          allowed_prefixes: ["+1", "+4420"],
          allowed_sip_hosts: ["pbx.example.com"],
          max_calls_per_min: 10,
          max_concurrent_outbound: 5,
        },
      },
    });
  });

  it("refuses a malformed prefix without calling the api", async () => {
    const requests = stubApi({ ...meWith("owner"), "GET workspaces": { items: [{ id: "ws-1", settings: {} }], total: 1 } });
    renderWithClient(<DialingPolicyCard />);

    fireEvent.change(await screen.findByLabelText("Allowed number prefixes"), { target: { value: "1-555" } });
    fireEvent.click(screen.getByRole("button", { name: "Save policy" }));

    expect(await screen.findByText(/"1555" is not a prefix/)).toBeTruthy();
    expect(requests.some((r) => r.method === "PUT")).toBe(false);
  });

  it("is read-only for builders", async () => {
    stubApi({ ...meWith("builder"), "GET workspaces": { items: [{ id: "ws-1", settings: {} }], total: 1 } });
    renderWithClient(<DialingPolicyCard />);

    expect(await screen.findByText("Only admins and owners can change the policy.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Save policy" })).toBeNull();
    expect((screen.getByLabelText("Allowed number prefixes") as HTMLInputElement).readOnly).toBe(true);
  });

  it("warns that +1 alone covers only the US and Canada (R-V2-29)", async () => {
    stubApi({
      ...meWith("admin"),
      "GET workspaces": { items: [{ id: "ws-1", settings: { telephony: { allowed_prefixes: ["+1"] } } }], total: 1 },
    });
    renderWithClient(<DialingPolicyCard />);

    expect(await screen.findByText(PLUS_ONE_WARNING)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Allowed number prefixes"), { target: { value: "+1, +1876" } });
    await waitFor(() => expect(screen.queryByText(PLUS_ONE_WARNING)).toBeNull());
    fireEvent.change(screen.getByLabelText("Allowed number prefixes"), { target: { value: "+44" } });
    expect(screen.queryByText(PLUS_ONE_WARNING)).toBeNull();
  });

  it("knows when +1 is the only NANP prefix", () => {
    expect(plusOneAlone(["+1"])).toBe(true);
    expect(plusOneAlone(["+1", "+44"])).toBe(true);
    expect(plusOneAlone(["+1", "+1787"])).toBe(false);
    expect(plusOneAlone(["+1876"])).toBe(false);
    expect(plusOneAlone([])).toBe(false);
  });

  it("parses stored settings leniently and validates drafts", () => {
    expect(policyFromSettings(undefined).allowed_prefixes).toEqual([]);
    expect(policyFromSettings({ telephony: { allowed_prefixes: ["+1", 2], max_calls_per_min: "x" } })).toEqual({
      allowed_prefixes: ["+1"],
      allowed_sip_hosts: [],
      max_calls_per_min: 10,
      max_concurrent_outbound: 5,
    });
    expect(parseDraft({ prefixes: "", hosts: "", perMin: "-1", concurrent: "2.5" }).errors).toEqual({
      perMin: "Whole numbers only",
      concurrent: "Whole numbers only",
    });
  });

  it("is on the telephony page", async () => {
    stubApi({ ...meWith("admin"), "GET workspaces": { items: [{ id: "ws-1", settings: {} }], total: 1 } });
    renderWithClient(<TelephonyPage />);

    expect(await screen.findByRole("region", { name: "Outbound dialing policy" })).toBeTruthy();
  });
});

// ------------------------------------- V2-19: phone tools + destinations (R-V2-21/25)
function PhoneCardHarness({
  targets = [],
  disabled = [],
  onValues,
  issues = [],
}: {
  targets?: { label: string; to: string; mode?: "cold" | "warm" }[];
  disabled?: string[];
  onValues: (values: AgentEditorForm) => void;
  issues?: EditorIssue[];
}) {
  const form = useForm<AgentEditorForm>({
    defaultValues: {
      config: {
        tools: { builtin_disabled: disabled, http_request_enabled: false, tool_ids: [], max_tool_steps: 3 },
        telephony: { transfer_targets: targets },
      },
    } as unknown as AgentEditorForm,
  });
  const values = form.watch();
  React.useEffect(() => {
    onValues(values as AgentEditorForm);
  });
  const ctx: EditorContextValue = {
    agent: {} as AgentOut,
    sections: [],
    activeSection: "tools",
    goToSection: vi.fn(),
    issues,
    focusIssue: vi.fn(),
  };
  return (
    <EditorContextProvider value={ctx}>
      <FormProvider {...form}>
        <PhoneCallsCard />
      </FormProvider>
    </EditorContextProvider>
  );
}

describe("PhoneCallsCard", () => {
  it("lists the two phone tools, outside the ordinary built-ins", () => {
    expect(TELEPHONY_TOOLS.map((t) => t.name)).toEqual(["send_dtmf", "transfer_call"]);
    expect(BUILTIN_TOOLS.some((t) => TELEPHONY_TOOLS.some((p) => p.name === t.name))).toBe(false);
  });

  it("toggles write tools.builtin_disabled, and transfer needs a destination", async () => {
    let latest: AgentEditorForm | undefined;
    render(<PhoneCardHarness onValues={(v) => (latest = v)} />);

    const transfer = screen.getByRole("switch", { name: "Transfer calls" });
    expect(transfer.getAttribute("data-disabled")).not.toBeNull();
    expect(screen.getByText(/Phone calls only\. Add a transfer destination first/)).toBeTruthy();

    fireEvent.click(screen.getByRole("switch", { name: "Press phone keys" }));
    await waitFor(() => expect(latest?.config.tools.builtin_disabled).toEqual(["send_dtmf"]));

    fireEvent.click(screen.getByRole("button", { name: "Add destination" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Front desk" } });
    fireEvent.change(screen.getByLabelText("Number or SIP address"), { target: { value: "+15550003333" } });
    await waitFor(() =>
      expect(latest?.config.telephony.transfer_targets).toEqual([
        { label: "Front desk", to: "+15550003333", mode: "cold" },
      ]),
    );
    expect(screen.getByRole("switch", { name: "Transfer calls" }).getAttribute("data-disabled")).toBeNull();
  });

  it("removes a destination", async () => {
    let latest: AgentEditorForm | undefined;
    render(
      <PhoneCardHarness
        targets={[{ label: "Sales", to: "+15550001111" }, { label: "Desk", to: "sip:desk@pbx.example.com" }]}
        onValues={(v) => (latest = v)}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Remove destination 1" }));

    await waitFor(() =>
      expect(latest?.config.telephony.transfer_targets).toEqual([
        { label: "Desk", to: "sip:desk@pbx.example.com", mode: "cold" },
      ]),
    );
  });

  // ----------------------------------- V5-32/36: transfer mode (#211)
  it("defaults a target's mode to 'Put through directly' and switches to warm", async () => {
    let latest: AgentEditorForm | undefined;
    render(<PhoneCardHarness targets={[{ label: "Sales", to: "+15550001111" }]} onValues={(v) => (latest = v)} />);

    const select = screen.getByRole("combobox", { name: "How the call is handed over" });
    expect(select.textContent).toBe("Put through directly");

    choose(select, "Introduce the caller first (LiveKit Cloud)");

    await waitFor(() =>
      expect(latest?.config.telephony.transfer_targets).toEqual([
        { label: "Sales", to: "+15550001111", mode: "warm" },
      ]),
    );
  });

  it("shows a mode warning as a hint, never a red field error", () => {
    const issue: EditorIssue = {
      key: "w1",
      path: "telephony.transfer_targets.0.mode",
      message: "This falls back to a standard transfer (needs LiveKit Cloud with one outbound line).",
      severity: "warning",
      section: "tools",
      source: "server",
    };
    render(
      <PhoneCardHarness
        targets={[{ label: "Sales", to: "+15550001111", mode: "warm" }]}
        onValues={() => {}}
        issues={[issue]}
      />,
    );
    const warning = screen.getByText(/falls back to a standard transfer/);
    expect(warning.className).toContain("text-warning-text");
    expect(warning.className).not.toContain("text-danger-text");
  });
});

// ----------------------------------- V5-32/36: Voicemail card (#211)
function VoicemailCardHarness({ onValues }: { onValues: (values: AgentEditorForm) => void }) {
  const form = useForm<AgentEditorForm>({
    defaultValues: {
      config: { telephony: { amd: { enabled: false, on_machine: "hangup", message: null, ivr_detection: false } } },
    } as unknown as AgentEditorForm,
  });
  const values = form.watch();
  React.useEffect(() => {
    onValues(values as AgentEditorForm);
  });
  return (
    <FormProvider {...form}>
      <VoicemailCard />
    </FormProvider>
  );
}

describe("VoicemailCard", () => {
  it("says outbound calls only, and starts collapsed", () => {
    render(<VoicemailCardHarness onValues={() => {}} />);
    expect(screen.getByText(/Outbound calls only/)).toBeTruthy();
    expect(screen.queryByLabelText("When a machine answers")).toBeNull();
  });

  it("turning detection on reveals the on-machine choice and the ivr switch, and writes config.telephony.amd", async () => {
    let latest: AgentEditorForm | undefined;
    render(<VoicemailCardHarness onValues={(v) => (latest = v)} />);

    fireEvent.click(screen.getByRole("switch", { name: "Detect answering machines" }));
    await waitFor(() => expect(latest?.config.telephony.amd?.enabled).toBe(true));
    expect(screen.getByLabelText("When a machine answers")).toBeTruthy();
    expect(screen.getByLabelText("Message")).toHaveProperty("disabled", true);

    choose(screen.getByLabelText("When a machine answers"), "Leave a message");
    await waitFor(() => expect(latest?.config.telephony.amd?.on_machine).toBe("leave_message"));
    expect(screen.getByLabelText("Message")).toHaveProperty("disabled", false);

    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "We'll call back shortly." } });
    await waitFor(() => expect(latest?.config.telephony.amd?.message).toBe("We'll call back shortly."));

    fireEvent.click(screen.getByRole("switch", { name: "Let the agent work through phone menus" }));
    await waitFor(() => expect(latest?.config.telephony.amd?.ivr_detection).toBe(true));
  });

  it("an empty message saves as null, not an empty string", async () => {
    let latest: AgentEditorForm | undefined;
    render(<VoicemailCardHarness onValues={(v) => (latest = v)} />);
    fireEvent.click(screen.getByRole("switch", { name: "Detect answering machines" }));
    choose(await screen.findByLabelText("When a machine answers"), "Leave a message");
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Call back" } });
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "" } });
    await waitFor(() => expect(latest?.config.telephony.amd?.message).toBeNull());
  });
});

// ------------------------------------- V5-28: SMS contacts (send_sms's destination policy)
function SmsCardHarness({
  contacts = [],
  onValues,
}: {
  contacts?: { label: string; to: string }[];
  onValues: (values: AgentEditorForm) => void;
}) {
  const form = useForm<AgentEditorForm>({
    defaultValues: {
      config: { telephony: { transfer_targets: [], sms_targets: contacts } },
    } as unknown as AgentEditorForm,
  });
  const values = form.watch();
  React.useEffect(() => {
    onValues(values as AgentEditorForm);
  });
  return (
    <FormProvider {...form}>
      <SmsContactsCard />
    </FormProvider>
  );
}

describe("SmsContactsCard (V5-25, V5-28)", () => {
  it("explains the destination policy in plain words and starts empty", () => {
    render(<SmsCardHarness onValues={() => {}} />);
    expect(screen.getByText(/the agent can text the caller automatically/i)).toBeTruthy();
    expect(screen.getByText(/No saved contacts/)).toBeTruthy();
  });

  it("adds a contact", async () => {
    let latest: AgentEditorForm | undefined;
    render(<SmsCardHarness onValues={(v) => (latest = v)} />);

    fireEvent.click(screen.getByRole("button", { name: "Add contact" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Claims desk" } });
    fireEvent.change(screen.getByLabelText("Mobile number"), { target: { value: "+15550002222" } });

    await waitFor(() =>
      expect(latest?.config.telephony.sms_targets).toEqual([{ label: "Claims desk", to: "+15550002222" }]),
    );
  });

  it("removes a contact", async () => {
    let latest: AgentEditorForm | undefined;
    render(
      <SmsCardHarness
        contacts={[{ label: "Claims desk", to: "+15550002222" }, { label: "Sales", to: "+15550001111" }]}
        onValues={(v) => (latest = v)}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Remove contact 1" }));

    await waitFor(() => expect(latest?.config.telephony.sms_targets).toEqual([{ label: "Sales", to: "+15550001111" }]));
  });
});

