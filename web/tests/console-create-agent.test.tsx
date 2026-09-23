import * as React from "react";

import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { CreateAgentFlow } from "@/components/console/agents/create/create-agent-flow";
import type { AgentOut, PacksResponse, ProvidersResponse } from "@/contracts/lkap-contracts";

// jsdom has no ResizeObserver; the shadcn `RadioGroupItem` (Radix, via
// @radix-ui/react-use-size) needs one to mount (see
// tests/console-http-tool-editor.test.tsx for the same stub).
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const routerPush = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush, replace: vi.fn() }),
}));

const PROVIDERS: ProvidersResponse = { providers: [] };

const PACKS: PacksResponse = {
  items: [
    {
      manifest: {
        id: "insurance_claim",
        name: "Insurance claim intake",
        description: "Adjuster claim intake with the claim notebook panel.",
        capabilities: { camera: true },
        default_greeting: "Hi",
        default_instructions: "Help.",
        recommended_pipeline: {
          mode: "cascaded",
          stt: { provider_id: "deepgram-stt" },
          llm: { provider_id: "openai-llm" },
          tts: { provider_id: "elevenlabs-tts" },
        },
        state_schema: {},
        tool_names: ["escalate_to_human", "search_knowledge"],
        kb_seeds: [{ kb_name: "Policy handbook", files: ["policy.pdf"] }],
        ui_panel_id: "insurance_notebook",
        version: "1",
      },
    },
    {
      manifest: {
        id: "generic",
        name: "Generic",
        description: "A blank starting point.",
        capabilities: {},
        default_greeting: "Hi",
        default_instructions: "Help.",
        recommended_pipeline: { mode: "cascaded" },
        state_schema: {},
        tool_names: [],
        ui_panel_id: "generic",
        version: "1",
      },
    },
  ],
};

function createdAgent(): AgentOut {
  return {
    id: "agent-new",
    name: "Claims desk",
    slug: "claims-desk",
    description: "",
    pack_id: "insurance_claim",
    ui_panel_id: "insurance_notebook",
    published: false,
    config_version: 1,
    created_at: "2026-09-19T00:00:00Z",
    updated_at: "2026-09-19T00:00:00Z",
    config: { instructions: "Help.", pipeline: { mode: "cascaded" } },
  };
}

function stubFetch() {
  const calls: { url: string; body?: unknown }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.startsWith("/api/console/packs")) {
      return { ok: true, status: 200, json: async () => PACKS } as Response;
    }
    if (url.startsWith("/api/console/providers")) {
      return { ok: true, status: 200, json: async () => PROVIDERS } as Response;
    }
    if (url.startsWith("/api/console/agents") && init?.method === "POST") {
      calls.push({ url, body: init?.body ? JSON.parse(init.body as string) : undefined });
      return { ok: true, status: 201, json: async () => createdAgent() } as Response;
    }
    throw new Error(`Unhandled fetch: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

function renderFlow() {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const calls = stubFetch();
  const view = render(
    <QueryClientProvider client={client}>
      <CreateAgentFlow />
    </QueryClientProvider>,
  );
  return { ...view, calls };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("CreateAgentFlow", () => {
  it("renders a pack card per manifest with facts from the manifest, and pins Blank agent first, pre-selected", async () => {
    renderFlow();

    expect(await screen.findByText("Insurance claim intake")).toBeTruthy();
    // §4.2: the `generic` pack is relabelled "Blank agent" and sits first.
    expect(screen.getByText("Blank agent")).toBeTruthy();
    // Tool + knowledge facts drawn straight from the (non-generic) manifest.
    expect(screen.getByText(/2 code tools/)).toBeTruthy();
    expect(screen.getByText(/seeds 1 knowledge base/)).toBeTruthy();

    // Radix's `RadioGroupItem` renders as a `button[role=radio]`, not a
    // native input — assert via `id`/`aria-checked`, not `.value`/`.checked`.
    const radios = screen.getAllByRole("radio");
    expect(radios[0].id).toBe("pack-generic");
    expect(radios[0].getAttribute("aria-checked")).toBe("true");
  });

  it("requires a name before submitting", async () => {
    renderFlow();
    await screen.findByText("Insurance claim intake");

    fireEvent.click(screen.getByRole("button", { name: "Create agent" }));

    expect(await screen.findByText("Name is required.")).toBeTruthy();
  });

  it("submits the chosen pack and name, then routes to the editor's providers section", async () => {
    const { calls } = renderFlow();
    await screen.findByText("Insurance claim intake");

    fireEvent.click(screen.getByRole("radio", { name: /Insurance claim intake/ }));
    fireEvent.change(screen.getByLabelText(/^Name/), { target: { value: "Claims desk" } });
    fireEvent.click(screen.getByRole("button", { name: "Create agent" }));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body).toEqual({
      name: "Claims desk",
      description: "",
      pack_id: "insurance_claim",
      config: null,
    });
    await waitFor(() => expect(routerPush).toHaveBeenCalledWith("/console/agents/agent-new?section=providers"));
  });

  it("defaults to the Blank agent pack when nothing else is chosen", async () => {
    const { calls } = renderFlow();
    await screen.findByText("Insurance claim intake");

    fireEvent.change(screen.getByLabelText(/^Name/), { target: { value: "Blank bot" } });
    fireEvent.click(screen.getByRole("button", { name: "Create agent" }));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect((calls[0].body as { pack_id: string }).pack_id).toBe("generic");
  });
});
