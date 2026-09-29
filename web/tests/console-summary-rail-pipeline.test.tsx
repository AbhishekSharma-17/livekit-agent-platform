import * as React from "react";

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { FormProvider, useForm } from "react-hook-form";

import { EditorContextProvider, type EditorContextValue } from "@/components/console/agents/editor/editor-context";
import { SummaryRail } from "@/components/console/agents/editor/summary-rail";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import type { AgentOut, ConnectionOut, ProviderOut } from "@/contracts/lkap-contracts";

import { REGISTRY } from "./provider-models";

/**
 * V6-33: the agent overview (the summary rail) says who does what in one line and labels each
 * part by its job, with the same turn-ending precedence as the providers section.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/console/agents/a-1",
  useSearchParams: () => new URLSearchParams(),
}));

const DGX: ConnectionOut = {
  id: "conn-dgx",
  slug: "dgx",
  name: "DGX-LivekitServer",
  url: "ws://dgx.internal:7880",
  deployment_type: "self_hosted",
  deployment_mode: "external",
  is_default: true,
  status: "ok",
  capabilities: { inference_available: false, turn_detector_mode: "local", noise_cancellation_tier: "none" },
};

function agentWith(stt: string): AgentOut {
  return {
    id: "a-1",
    name: "Front desk",
    slug: "front-desk",
    description: "",
    pack_id: "generic",
    ui_panel_id: "generic",
    published: false,
    config_version: 1,
    created_at: "2026-09-24T00:00:00Z",
    updated_at: "2026-09-24T00:00:00Z",
    connection_id: "conn-dgx",
    config: {
      instructions: "Help.",
      pipeline: {
        mode: "cascaded",
        stt: { provider_id: stt, credential_id: null, model: null, fields: {} },
        llm: { provider_id: "openrouter-llm", credential_id: null, model: "openai/gpt-4.1-mini", fields: {} },
        tts: { provider_id: "deepgram-tts", credential_id: null, model: null, fields: {} },
      },
    },
  } as unknown as AgentOut;
}

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      let body: unknown = { items: [], total: 0 };
      if (url.startsWith("/api/console/providers")) {
        body = { v: 2, providers: REGISTRY.map((p) => ({ ...p, v: 2, enabled: true, installed_on: ["conn-dgx"] }) as unknown as ProviderOut[]) };
      } else if (url.startsWith("/api/console/connections/conn-dgx")) body = DGX;
      else if (url.startsWith("/api/console/connections")) body = { items: [DGX], total: 1 };
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
}

function Harness({ agent }: { agent: AgentOut }) {
  const form = useForm<AgentEditorForm>({
    defaultValues: {
      name: agent.name,
      description: "",
      mode: "prompt",
      ui_panel_id: agent.ui_panel_id,
      connection_id: agent.connection_id ?? null,
      config: agent.config,
    } as unknown as AgentEditorForm,
  });
  const ctx: EditorContextValue = {
    agent,
    sections: [],
    activeSection: "providers",
    goToSection: () => {},
    issues: [],
    focusIssue: () => {},
  };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <FormProvider {...form}>
        <EditorContextProvider value={ctx}>
          <SummaryRail agent={agent} slots={{ testCallItems: [], headerActions: [] }} />
        </EditorContextProvider>
      </FormProvider>
    </QueryClientProvider>
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SummaryRail: the pipeline in one line (V6-33)", () => {
  it("says a Flux agent's listener ends the turns and where it runs", async () => {
    stubFetch();
    render(<Harness agent={agentWith("deepgram-flux-stt")} />);
    const line = await screen.findByText(/Deepgram Flux listens and decides when you've finished/);
    expect(line.textContent).toBe(
      "Deepgram Flux listens and decides when you've finished · GPT-4.1 mini thinks · Deepgram Aura speaks · runs on DGX-LivekitServer (self-hosted)",
    );
  });

  it("says a Nova-3 agent on the DGX uses LiveKit's detector inside the worker", async () => {
    stubFetch();
    render(<Harness agent={agentWith("deepgram-stt")} />);
    const line = await screen.findByText(/Deepgram listens/);
    expect(line.textContent).toContain("LiveKit's turn detector (runs inside the agent's worker) decides when you've finished");
  });

  it("labels each part in the mini-flow by its job", async () => {
    stubFetch();
    render(<Harness agent={agentWith("deepgram-flux-stt")} />);
    await screen.findByText(/Deepgram Flux listens/);
    const items = within(screen.getByRole("list", { name: "Pipeline" })).getAllByRole("listitem");
    expect(items[0].textContent).toMatch(/Listens.*Deepgram Flux/);
    expect(items[1].textContent).toMatch(/Thinks.*OpenRouter/);
    expect(items[2].textContent).toMatch(/Speaks.*Deepgram Aura/);
  });
});
