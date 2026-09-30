import * as React from "react";

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { FormProvider, useForm } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { toast } from "sonner";

import { toFormValues } from "@/components/console/agents/editor/form-values";
import { MemorySection } from "@/components/console/agents/editor/sections/memory-section";
import { MemoryTab } from "@/components/console/sessions-v2/memory-tab";
import { DangerTab } from "@/components/console/settings/danger-tab";
import { agentEditorFormSchema, type AgentEditorForm } from "@/components/console/lib/schemas";
import { zodResolver } from "@/components/console/lib/zod-resolver";
import type { AgentOut, SessionDetailOut } from "@/contracts/lkap-contracts";

/**
 * V5-42 (`docs/v5/PLAN-V5.md` V5-42, ask #263): the agent editor's Memory
 * card (`config.memory`), the session detail's Memory tab (what a session
 * recalled/stored, "Forget this caller"), and Settings → Danger zone's
 * "Delete all caller memories". No jargon ("Mem0", "subject id", "HMAC")
 * ever reaches the DOM; a `memory_unavailable` (503) reads as a plain
 * notice, never a raw error.
 */

// No `<Toaster/>` is mounted in these component tests (the `console-app-accounts.test.tsx` pattern).
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const nativeMatches = Element.prototype.matches;
beforeAll(() => {
  // Radix `Dialog` checks `:popover-open`/`:modal` in jsdom, which throws without this stub.
  Element.prototype.matches = function matches(this: Element, selector: string) {
    if (selector === ":popover-open" || selector === ":modal") return false;
    return nativeMatches.call(this, selector);
  };
});
afterAll(() => {
  Element.prototype.matches = nativeMatches;
});

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

function agent(overrides: Partial<AgentOut> = {}): AgentOut {
  return {
    id: "a-1",
    slug: "claims",
    name: "Claims",
    description: "",
    pack_id: "generic",
    ui_panel_id: "generic",
    published: false,
    config_version: 1,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    config: {
      instructions: "Hi there",
      pipeline: {
        mode: "cascaded",
        stt: { provider_id: "deepgram-stt" },
        llm: { provider_id: "openai-llm" },
        tts: { provider_id: "cartesia-tts" },
      },
    },
    ...overrides,
  } as AgentOut;
}

let latest: AgentEditorForm | null = null;
let submitted: AgentEditorForm | null = null;

function SectionHarness({ agent: theAgent }: { agent: AgentOut }) {
  const form = useForm<AgentEditorForm>({
    resolver: zodResolver(agentEditorFormSchema),
    defaultValues: toFormValues(theAgent),
    mode: "onChange",
  });
  latest = form.watch();
  return (
    <FormProvider {...form}>
      <form onSubmit={form.handleSubmit((values) => { submitted = values; })}>
        <MemorySection />
        <button type="submit">Save</button>
      </form>
    </FormProvider>
  );
}

describe("MemorySection", () => {
  beforeEach(() => {
    latest = null;
    submitted = null;
  });

  it("is off by default, with every other field disabled", () => {
    render(<SectionHarness agent={agent()} />);
    expect(screen.getByRole("switch", { name: "Remember returning callers" }).getAttribute("aria-checked")).toBe(
      "false",
    );
    expect((screen.getByLabelText("Forget after") as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText("What the agent says about memory") as HTMLTextAreaElement).disabled).toBe(true);
    expect((screen.getByLabelText("How much to recall") as HTMLInputElement).disabled).toBe(true);
    expect(
      (screen.getByRole("switch", { name: /Store what the caller said as it was/ }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("explains pseudonymous ids, anonymous visitors and phone numbers in plain words, with no jargon", () => {
    const { container } = render(<SectionHarness agent={agent()} />);
    expect(screen.getByText(/pseudonymous id/)).toBeTruthy();
    expect(screen.getByText(/never their name or phone number/)).toBeTruthy();
    expect(screen.getByText(/anonymous web visitor/)).toBeTruthy();
    expect(container.textContent).not.toMatch(/Mem0|HMAC|subject.id/i);
  });

  it("turning it on enables the rest, and posts config.memory on save", async () => {
    render(<SectionHarness agent={agent()} />);

    fireEvent.click(screen.getByRole("switch", { name: "Remember returning callers" }));
    await waitFor(() => expect(latest?.config.memory?.enabled).toBe(true));
    expect((screen.getByLabelText("Forget after") as HTMLInputElement).disabled).toBe(false);

    fireEvent.click(screen.getByRole("radio", { name: /All agents in this workspace/ }));
    fireEvent.change(screen.getByLabelText("Forget after"), { target: { value: "30" } });
    fireEvent.change(screen.getByLabelText("What the agent says about memory"), {
      target: { value: "We remember our calls to help you next time." },
    });
    fireEvent.change(screen.getByLabelText("How much to recall"), { target: { value: "250" } });
    fireEvent.click(screen.getByRole("switch", { name: /Store what the caller said as it was/ }));

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(submitted).not.toBeNull());
    expect(submitted?.config.memory).toEqual({
      enabled: true,
      scope: "workspace",
      retention_days: 30,
      consent_line: "We remember our calls to help you next time.",
      max_recall_tokens: 250,
      verbatim: true,
    });
  });

  it("loads a stored config.memory unchanged", () => {
    render(
      <SectionHarness
        agent={agent({
          config: {
            instructions: "Hi there",
            pipeline: { mode: "cascaded" },
            memory: {
              enabled: true,
              scope: "agent",
              retention_days: 45,
              consent_line: "We remember our calls.",
              max_recall_tokens: 250,
              verbatim: false,
            },
          },
        })}
      />,
    );
    expect(screen.getByRole("switch", { name: "Remember returning callers" }).getAttribute("aria-checked")).toBe(
      "true",
    );
    expect(screen.getByRole("radio", { name: /Only this agent/ }).getAttribute("aria-checked")).toBe("true");
    expect((screen.getByLabelText("Forget after") as HTMLInputElement).value).toBe("45");
    expect((screen.getByLabelText("What the agent says about memory") as HTMLTextAreaElement).value).toBe(
      "We remember our calls.",
    );
    expect((screen.getByLabelText("How much to recall") as HTMLInputElement).value).toBe("250");
  });

  // The `limits-section.tsx` pattern: clearing a numeric field holds an empty
  // string (backed by `NaN` in form state) rather than snapping back to the
  // default, so a caller backspacing "90" to retype a value isn't fought by
  // the input on every keystroke; zod's `.min()` then reports the empty case.
  it("lets 'Forget after' be cleared without snapping back to the default", async () => {
    render(<SectionHarness agent={agent()} />);
    fireEvent.click(screen.getByRole("switch", { name: "Remember returning callers" }));
    const retention = (await screen.findByLabelText("Forget after")) as HTMLInputElement;
    await waitFor(() => expect(retention.disabled).toBe(false));

    fireEvent.change(retention, { target: { value: "" } });
    expect(retention.value).toBe("");
    await waitFor(() => expect(Number.isNaN(latest?.config.memory?.retention_days)).toBe(true));

    fireEvent.change(retention, { target: { value: "45" } });
    await waitFor(() => expect(latest?.config.memory?.retention_days).toBe(45));
  });
});

function sessionDetail(overrides: Partial<SessionDetailOut> = {}): SessionDetailOut {
  return {
    id: "s-1",
    agent_id: "a-1",
    agent_name: "Claims desk",
    config_version: 1,
    room_name: "room-1",
    status: "ended",
    pipeline_mode: "cascaded",
    created_at: "2026-09-27T00:00:00Z",
    ...overrides,
  } as SessionDetailOut;
}

interface Call {
  url: string;
  method: string;
  body: Record<string, unknown> | undefined;
}

function stubApi(role: "admin" | "builder" | "viewer", handler: (call: Call) => { status: number; body: unknown } | undefined) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined;
      const call: Call = { url: String(input), method: init?.method ?? "GET", body };
      calls.push(call);
      let status = 200;
      let responseBody: unknown;
      const override = handler(call);
      if (override) {
        ({ status, body: responseBody } = override);
      } else if (call.url.includes("/auth/me")) {
        responseBody = { user: { id: "u1" }, workspaces: [{ id: "ws1", name: "Acme claims", slug: "acme", role }] };
      } else {
        responseBody = {};
      }
      return { ok: status < 400, status, json: async () => responseBody } as Response;
    }),
  );
  return calls;
}

function renderTab(session: SessionDetailOut) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryTab session={session} />
    </QueryClientProvider>,
  );
}

describe("MemoryTab", () => {
  it("shows an off notice when the agent's memory is off", async () => {
    stubApi("admin", (call) => {
      if (call.url.includes("/sessions/s-1/memory")) return { status: 200, body: { enabled: false } };
      return undefined;
    });
    renderTab(sessionDetail());
    expect(await screen.findByText("Memory is off for this agent")).toBeTruthy();
  });

  it("shows what was recalled and stored as plain text, with the status lines", async () => {
    stubApi("admin", (call) => {
      if (call.url.includes("/sessions/s-1/memory")) {
        return {
          status: 200,
          body: {
            enabled: true,
            subject_id: "ab12cd34",
            recall_status: "recalled",
            recalled: ["Prefers to be called Ada", "Mornings suit them best"],
            store_status: "stored",
            stored: ["Likes to be called Ada"],
          },
        };
      }
      return undefined;
    });
    renderTab(sessionDetail());

    expect(await screen.findByText("Prefers to be called Ada")).toBeTruthy();
    expect(screen.getByText("Mornings suit them best")).toBeTruthy();
    expect(screen.getByText("Likes to be called Ada")).toBeTruthy();
    expect(screen.getByText("Recalled from an earlier call.")).toBeTruthy();
    expect(screen.getByText("Stored after the call.")).toBeTruthy();
    expect(screen.getByText("Pseudonymous caller id known")).toBeTruthy();
  });

  it("shows a plain notice, not a raw error, when memory isn't installed", async () => {
    stubApi("admin", (call) => {
      if (call.url.includes("/sessions/s-1/memory")) {
        return { status: 503, body: { error: { code: "memory_unavailable", message: "memory is unavailable" } } };
      }
      return undefined;
    });
    renderTab(sessionDetail());
    expect(await screen.findByText("Memory is not installed on this server.")).toBeTruthy();
  });

  it("disables 'Forget this caller' for a non-admin, with a reason — never hidden", async () => {
    stubApi("builder", (call) => {
      if (call.url.includes("/sessions/s-1/memory")) {
        return { status: 200, body: { enabled: true, subject_id: "ab12cd34", recall_status: "recalled", recalled: ["x"] } };
      }
      return undefined;
    });
    renderTab(sessionDetail());

    const button = (await screen.findByRole("button", { name: "Forget this caller" })) as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(true));
    expect(button.getAttribute("title")).toMatch(/admin/i);
  });

  it("an admin confirms and forgets the caller, calling DELETE /v1/memory/subjects/{id}", async () => {
    const calls = stubApi("admin", (call) => {
      if (call.url.includes("/sessions/s-1/memory")) {
        return { status: 200, body: { enabled: true, subject_id: "ab12cd34", recall_status: "recalled", recalled: ["x"] } };
      }
      if (call.url.includes("/memory/subjects/ab12cd34") && call.method === "DELETE") {
        return { status: 200, body: { subject_id: "ab12cd34", forgotten: true, sessions_updated: 2 } };
      }
      return undefined;
    });
    renderTab(sessionDetail());

    const button = (await screen.findByRole("button", { name: "Forget this caller" })) as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);

    const dialog = await screen.findByRole("alertdialog", { name: "Forget this caller?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Forget caller" }));

    await waitFor(() =>
      expect(calls.some((c) => c.method === "DELETE" && c.url.includes("/memory/subjects/ab12cd34"))).toBe(true),
    );
    expect(toast.success).toHaveBeenCalled();
  });
});

function renderDanger() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DangerTab />
    </QueryClientProvider>,
  );
}

describe("Settings → Danger zone — Delete all caller memories", () => {
  it("disables the purge button for a non-admin, with a reason", async () => {
    stubApi("builder", () => undefined);
    renderDanger();
    const button = (await screen.findByRole("button", { name: "Delete all caller memories" })) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.getAttribute("title")).toMatch(/admin/i);
  });

  it("an admin must type the workspace name before the confirm button enables, then it posts /v1/memory/purge", async () => {
    const calls = stubApi("admin", (call) => {
      if (call.url.includes("/memory/purge") && call.method === "POST") {
        return { status: 200, body: { status: "queued", subjects: 3, job_id: "job-1" } };
      }
      return undefined;
    });
    renderDanger();

    const trigger = (await screen.findByRole("button", { name: "Delete all caller memories" })) as HTMLButtonElement;
    await waitFor(() => expect(trigger.disabled).toBe(false));
    fireEvent.click(trigger);

    const dialog = await screen.findByRole("dialog", { name: "Delete all caller memories?" });
    const confirmButton = within(dialog).getByRole("button", { name: "Delete all caller memories" }) as HTMLButtonElement;
    expect(confirmButton.disabled).toBe(true);

    const input = within(dialog).getByLabelText("Workspace name");
    fireEvent.change(input, { target: { value: "wrong name" } });
    expect(confirmButton.disabled).toBe(true);

    fireEvent.change(input, { target: { value: "Acme claims" } });
    expect(confirmButton.disabled).toBe(false);
    fireEvent.click(confirmButton);

    await waitFor(() =>
      expect(calls.some((c) => c.method === "POST" && c.url.includes("/memory/purge"))).toBe(true),
    );
    const purgeCall = calls.find((c) => c.method === "POST" && c.url.includes("/memory/purge"))!;
    expect(purgeCall.body).toEqual({ confirm: true });
    expect(toast.success).toHaveBeenCalled();
  });
});
