import * as React from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { FormProvider, useForm } from "react-hook-form";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConnectionChip } from "@/components/console/agents/providers-section/connection-chip";
import { ConnectionCreateForm } from "@/components/console/connections/connection-create-form";
import {
  AGENT_NAME_HINT,
  NO_WORKER_TITLE,
  OTHER_CONNECTION_WORKER_NOTE,
  WORKER_START_COMMAND,
  workerStatusLabel,
} from "@/components/console/connections/connection-model";
import { ConnectionOverview } from "@/components/console/connections/connection-overview";
import { WorkerStatusNotice } from "@/components/console/connections/worker-status-notice";
import { ConnectError, describeConnectError } from "@/lib/livekit";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import type { AgentOut, ConnectionOut, FleetStatus } from "@/contracts/lkap-contracts";

/**
 * V6-27: agent-name clashes shown inline under the Agent name field, the
 * "each connection needs its own worker" notes, the no-worker empty state, the
 * worker status in the agent editor's connection picker, and the call page's
 * `no_worker_running` message.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/console/connections",
  useSearchParams: () => new URLSearchParams(),
}));

const CLASH_MESSAGE =
  "Another connection already uses the agent name 'lkap-agent' on this LiveKit server. Calls would be split between them. Pick a different agent name.";

type Reply = { status: number; body: unknown };

/**
 * An admin's `/auth/me`: editing a connection and starting its workers are
 * admin writes server-side (`auth/roles.py::ROUTE_POLICY`), so the console
 * only offers them to admins (docs/ui/AUDIT.md D12).
 */
const ADMIN_ME: Reply = {
  status: 200,
  body: {
    user: { id: "u1", email: "admin@example.test" },
    workspaces: [{ id: "ws1", name: "Test workspace", slug: "test", role: "admin" }],
  },
};

function stubFetch(route: (url: string, init?: RequestInit) => Reply | undefined) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const reply = route(url, init) ?? { status: 200, body: {} };
    return {
      ok: reply.status < 400,
      status: reply.status,
      statusText: "",
      json: async () => reply.body,
      text: async () => (typeof reply.body === "string" ? reply.body : JSON.stringify(reply.body)),
    } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

class StubResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", StubResizeObserver);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const CONNECTION: ConnectionOut = {
  id: "conn-1",
  slug: "staging",
  name: "Staging",
  url: "wss://example.livekit.cloud",
  agent_name: "lkap-agent",
  deployment_type: "cloud",
  deployment_mode: "external",
  is_default: true,
  status: "ok",
  replicas: 1,
};

function fleet(overrides: Partial<FleetStatus> = {}): FleetStatus {
  return { desired_replicas: 0, instances: [], ready_workers: 0, shared_agent_name_workers: 0, ...overrides };
}

function fillCreateForm() {
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Staging" } });
  fireEvent.change(screen.getByLabelText("URL"), { target: { value: "wss://example.livekit.cloud" } });
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "key-placeholder" } });
  fireEvent.change(screen.getByLabelText("API secret"), { target: { value: "secret-placeholder" } });
}

describe("ConnectionCreateForm — agent name clash (V6-27)", () => {
  it("shows the 409 from Test connection inline under the Agent name field and keeps Save locked", async () => {
    stubFetch((url, init) =>
      url.endsWith("/connections/test") && init?.method === "POST"
        ? { status: 409, body: { error: { code: "agent_name_in_use", message: CLASH_MESSAGE, details: { field: "agent_name" } } } }
        : undefined,
    );
    renderWithClient(<ConnectionCreateForm />);
    fillCreateForm();

    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    const inline = await screen.findByText(CLASH_MESSAGE);
    expect(inline.id).toBe("conn-agent-name-error");
    const input = screen.getByLabelText("Agent name");
    expect(input.getAttribute("aria-invalid")).toBe("true");
    expect(input.getAttribute("aria-describedby")).toContain("conn-agent-name-error");
    expect(screen.queryByText("Connection failed")).toBeNull();
    expect((screen.getByRole("button", { name: "Create connection" }) as HTMLButtonElement).disabled).toBe(true);

    // Editing the name clears the message until the next test.
    fireEvent.change(input, { target: { value: "lkap-agent-staging" } });
    expect(screen.queryByText(CLASH_MESSAGE)).toBeNull();
  });

  it("explains the agent name and that each connection needs its own worker, per mode", () => {
    stubFetch(() => undefined);
    renderWithClient(<ConnectionCreateForm />);

    expect(screen.getByText(AGENT_NAME_HINT)).toBeTruthy();
    const note = document.querySelector('[data-slot="worker-note"]');
    expect(note?.textContent).toContain("Each connection needs its own worker.");
    expect(note?.textContent).toContain("External: you start it");

    fireEvent.click(screen.getByText("Supervised"));
    expect(note?.textContent).toContain("LKAP starts it once you press Start");

    fireEvent.click(screen.getByText("Cloud-hosted"));
    expect(note?.textContent).toContain("deploy it to LiveKit Cloud with the bundle");
  });
});

describe("ConnectionOverview edit — agent name clash (V6-27)", () => {
  it("shows the 409 from Save under the Agent name field", async () => {
    stubFetch((url, init) => {
      if (url.includes("/auth/me")) return ADMIN_ME;
      if (url.includes("/connections/conn-1/fleet")) return { status: 200, body: fleet({ ready_workers: 1 }) };
      if (url.endsWith("/connections/conn-1") && init?.method === "PUT") {
        return { status: 409, body: { error: { code: "agent_name_in_use", message: CLASH_MESSAGE, details: { field: "agent_name" } } } };
      }
      return undefined;
    });
    renderWithClient(<ConnectionOverview connection={CONNECTION} />);

    // Edit appears once the role has loaded (admins only).
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Agent name"), { target: { value: "taken-name" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    const inline = await screen.findByText(CLASH_MESSAGE);
    expect(inline.id).toBe("edit-conn-agent-name-error");
  });
});

describe("WorkerStatusNotice (V6-27)", () => {
  it("shows the empty state with worker settings and the start command for an external connection", async () => {
    stubFetch((url) => (url.includes("/fleet") ? { status: 200, body: fleet() } : undefined));
    renderWithClient(<WorkerStatusNotice connection={CONNECTION} />);

    expect(await screen.findByText(NO_WORKER_TITLE)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Copy worker settings" })).toBeTruthy();
    expect(screen.getByText(WORKER_START_COMMAND)).toBeTruthy();
    expect(screen.getByText(OTHER_CONNECTION_WORKER_NOTE)).toBeTruthy();
  });

  it("offers Start for a supervised connection", async () => {
    const fetchMock = stubFetch((url, init) =>
      url.includes("/auth/me")
        ? ADMIN_ME
        : url.includes("/fleet")
          ? { status: 200, body: init?.method === "POST" ? fleet({ desired_replicas: 1 }) : fleet() }
          : undefined,
    );
    renderWithClient(<WorkerStatusNotice connection={{ ...CONNECTION, deployment_mode: "supervised", replicas: 2 }} />);

    fireEvent.click(await screen.findByRole("button", { name: "Start" }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([input, init]) => String(input).includes("/fleet") && init?.method === "POST" && String(init.body).includes('"start"'),
        ),
      ).toBe(true),
    );
  });

  it("shows the ready count and warns when another connection's workers share the agent name", async () => {
    stubFetch((url) =>
      url.includes("/fleet") ? { status: 200, body: fleet({ ready_workers: 2, shared_agent_name_workers: 1 }) } : undefined,
    );
    renderWithClient(<WorkerStatusNotice connection={CONNECTION} />);

    expect(await screen.findByText("2 workers ready")).toBeTruthy();
    expect(screen.queryByText(NO_WORKER_TITLE)).toBeNull();
    expect(screen.getByText(/1 worker started for another connection is running under the agent name “lkap-agent”/)).toBeTruthy();
  });

  it("renders nothing while the counts are unknown", () => {
    stubFetch(() => undefined);
    const { container } = renderWithClient(<WorkerStatusNotice connection={CONNECTION} />);
    expect(container.textContent).toBe("");
  });
});

function ChipHarness({ connectionId }: { connectionId: string | null }) {
  const methods = useForm<AgentEditorForm>({
    defaultValues: { connection_id: connectionId, config: { pipeline: {} } } as unknown as AgentEditorForm,
  });
  return (
    <FormProvider {...methods}>
      <ConnectionChip agent={{ id: "a1" } as AgentOut} />
    </FormProvider>
  );
}

describe("ConnectionChip worker status (V6-27)", () => {
  const busy: ConnectionOut = { ...CONNECTION, id: "conn-busy", name: "Busy", is_default: true, ready_workers: 2 };
  const idle: ConnectionOut = { ...CONNECTION, id: "conn-idle", name: "Idle", is_default: false, ready_workers: 0 };

  function stubConnections() {
    stubFetch((url) => {
      if (url.includes("/connections")) return { status: 200, body: { items: [busy, idle], total: 2 } };
      if (url.includes("/providers")) return { status: 200, body: { providers: [] } };
      return undefined;
    });
  }

  it("lists each connection's worker status and warns, with a link, when the bound one has none", async () => {
    stubConnections();
    renderWithClient(<ChipHarness connectionId="conn-idle" />);

    const warning = await screen.findByText(/No worker is running for “Idle”/);
    expect(warning.closest("a")?.getAttribute("href")).toBe("/console/connections/conn-idle");

    fireEvent.click(screen.getByRole("button", { name: /Connection:/ }));
    const list = within(await screen.findByRole("listbox", { name: "Connections" }));
    expect(list.getAllByText("2 workers ready").length).toBeGreaterThan(0);
    expect(list.getByText("No workers running")).toBeTruthy();
  });

  it("shows no warning when the bound connection has ready workers", async () => {
    stubConnections();
    renderWithClient(<ChipHarness connectionId="conn-busy" />);

    await screen.findByText(/Busy/);
    expect(screen.queryByText(/No worker is running/)).toBeNull();
  });
});

describe("worker status helpers and the call page message (V6-27)", () => {
  it.each([
    [undefined, null],
    [null, null],
    [0, "No workers running"],
    [1, "1 worker ready"],
    [3, "3 workers ready"],
  ])("workerStatusLabel(%s) is %s", (count, label) => {
    expect(workerStatusLabel(count)).toBe(label);
  });

  it("shows the api's no_worker_running message on the call page", () => {
    const message = "No worker is running for connection 'Staging'. Start one from Connections → Staging.";
    expect(describeConnectError(new ConnectError(409, "no_worker_running", message))).toBe(message);
  });
});
