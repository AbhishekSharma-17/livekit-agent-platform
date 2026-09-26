import * as React from "react";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ToolTemplateDialog } from "@/components/console/tools/tool-template-dialog";
import type { ProviderSpec, ToolOut, ToolTemplate } from "@/contracts/lkap-contracts";

/**
 * "Add a tool" → "From a template" (V5-25's `GET /v1/tool-templates`, D-V5-36;
 * V5-28's console side, docs/v5/_asks.md #154). The dialog lists a group's
 * templates with checkboxes, a key picker and any fixed arguments
 * (`event_type_id`), and posts `POST /v1/tool-templates/{id}/instantiate`.
 */

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
beforeEach(() => {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  // Radix `Select` (the key picker) needs these in jsdom.
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.releasePointerCapture = vi.fn();
});
afterEach(() => vi.unstubAllGlobals());

const SECRET_BAG: ProviderSpec = {
  id: "http-tool-secret",
  kind: "secret_bag",
  label: "Tool secrets",
  vendor: "LKAP",
  package: "",
  python_class: "",
};

const CHECK_AVAILABILITY: ToolTemplate = {
  id: "cal_com.booking_check_availability",
  group: "cal_com",
  group_label: "Cal.com bookings",
  label: "Check availability",
  summary: "Finds free times for the booking type.",
  risk: "read",
  secret_names: ["CAL_API_KEY"],
  defaults: [{ name: "event_type_id", label: "Event type id", help: "The event type's numeric id.", required: true }],
  definition: {
    kind: "http",
    name: "booking_check_availability",
    description: "List free times.",
    parameters: { type: "object", properties: { event_type_id: { type: "integer" } } },
    method: "GET",
    url: "https://api.cal.com/v2/slots",
    allowed_hosts: ["api.cal.com"],
  },
} as unknown as ToolTemplate;

const BOOKING_CREATE: ToolTemplate = {
  ...CHECK_AVAILABILITY,
  id: "cal_com.booking_create",
  label: "Book a time",
  summary: "Books the chosen time for the caller.",
  risk: "write",
  definition: { ...CHECK_AVAILABILITY.definition, name: "booking_create" },
} as unknown as ToolTemplate;

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

function stubFetch({
  tools = [] as ToolOut[],
  onInstantiate,
}: {
  tools?: ToolOut[];
  onInstantiate?: (url: string, body: unknown) => void;
} = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("/instantiate")) {
        const body: unknown = init?.body ? JSON.parse(init.body as string) : undefined;
        onInstantiate?.(url, body);
        return jsonResponse({ tool_ids: ["t1"], names: ["booking_check_availability"], template_ids: [CHECK_AVAILABILITY.id] });
      }
      if (url.includes("/tool-templates")) return jsonResponse({ items: [CHECK_AVAILABILITY, BOOKING_CREATE] });
      if (url.includes("/credentials")) return jsonResponse({ items: [{ id: "cred-1", provider_id: "http-tool-secret", label: "Cal key", fingerprint: "ab12" }] });
      if (url.includes("/tools")) return jsonResponse({ items: tools, total: tools.length });
      // `useWriteAccess("admin")` (instantiating binds a key, docs/v5/_asks.md #154(d)) reads this on every mount.
      if (url.includes("/auth/me")) {
        return jsonResponse({ user: { id: "u1" }, workspaces: [{ id: "ws1", name: "WS", slug: "ws", role: "admin" }] });
      }
      return jsonResponse({});
    }),
  );
}

function renderDialog(props: Partial<React.ComponentProps<typeof ToolTemplateDialog>> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onInstantiated = props.onInstantiated ?? vi.fn();
  render(
    <QueryClientProvider client={client}>
      <ToolTemplateDialog
        agentId="agent-1"
        secretBagSpec={SECRET_BAG}
        onInstantiated={onInstantiated}
        trigger={<button type="button">From a template</button>}
        {...props}
      />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByText("From a template"));
  return { onInstantiated };
}

describe("ToolTemplateDialog", () => {
  it("lists the group's templates, pre-checked, with a risk chip each", async () => {
    stubFetch();
    renderDialog();

    expect(await screen.findByText("Cal.com bookings")).toBeTruthy();
    expect(screen.getByText("Check availability")).toBeTruthy();
    expect(screen.getByText("Book a time")).toBeTruthy();
    expect(screen.getByText("Read only")).toBeTruthy();
    expect(screen.getByText("Changes things")).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: /Check availability/ }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByRole("checkbox", { name: /Book a time/ }).getAttribute("aria-checked")).toBe("true");
  });

  it("marks an already-created tool as 'Already added' and leaves it unchecked", async () => {
    stubFetch({
      tools: [
        {
          id: "existing-1",
          name: "booking_create",
          kind: "http",
          agent_id: "agent-1",
          enabled: true,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
          definition: BOOKING_CREATE.definition,
        } as unknown as ToolOut,
      ],
    });
    renderDialog();

    await screen.findByText("Book a time");
    expect(screen.getByText("Already added")).toBeTruthy();
    const createCheckbox = screen.getByRole("checkbox", { name: /Book a time/ }) as HTMLButtonElement;
    expect(createCheckbox.getAttribute("aria-checked")).toBe("true");
    expect(createCheckbox.disabled).toBe(true);
    // The other template in the group is still offered normally.
    expect((screen.getByRole("checkbox", { name: /Check availability/ }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("requires a required default and a key before it will submit", async () => {
    let posted: unknown;
    stubFetch({ onInstantiate: (_url, body) => (posted = body) });
    renderDialog();

    await screen.findByText("Cal.com bookings");
    fireEvent.click(screen.getByText(/Add \d+ tools?/));
    expect(await screen.findByText(/Choose a key first/)).toBeTruthy();
    expect(posted).toBeUndefined();
  });

  it("instantiates the chosen subset with the picked key and fixed event type id", async () => {
    let posted: { credential_id?: string; names?: string[]; defaults?: Record<string, string> } | undefined;
    let postedUrl: string | undefined;
    stubFetch({
      onInstantiate: (url, body) => {
        postedUrl = url;
        posted = body as typeof posted;
      },
    });
    const onInstantiated = vi.fn();
    renderDialog({ onInstantiated });

    await screen.findByText("Cal.com bookings");
    // Leave "Check availability" unchecked — only "Book a time" is submitted.
    fireEvent.click(screen.getByRole("checkbox", { name: /Check availability/ }));
    fireEvent.change(screen.getByLabelText("Event type id"), { target: { value: "123456" } });

    fireEvent.click(screen.getByLabelText("Key"));
    // Radix `Select` mirrors every item in a hidden native `<option>` too; scope to the open listbox.
    const listbox = await screen.findByRole("listbox");
    fireEvent.click(within(listbox).getByText(/Cal key/));

    fireEvent.click(screen.getByText(/Add \d+ tools?/));

    await waitFor(() => expect(onInstantiated).toHaveBeenCalled());
    expect(postedUrl).toContain("cal_com");
    expect(postedUrl).toContain("/instantiate");
    expect(posted?.credential_id).toBe("cred-1");
    expect(posted?.names).toEqual(["booking_create"]);
    expect(posted?.defaults).toEqual({ event_type_id: "123456" });
  });

  it("shows the business-timezone hint when given one (R-V5-10, ask #150)", async () => {
    stubFetch();
    renderDialog({ businessTimezone: "Europe/London" });

    expect(await screen.findByText(/Europe\/London/)).toBeTruthy();
  });
});
