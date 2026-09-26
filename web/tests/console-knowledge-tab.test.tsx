import * as React from "react";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { FormProvider, useForm } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { toFormValues } from "@/components/console/agents/editor/form-values";
import { KnowledgeTab } from "@/components/console/agents/tabs/knowledge-tab";
import { agentEditorFormSchema, type AgentEditorForm } from "@/components/console/lib/schemas";
import { zodResolver } from "@/components/console/lib/zod-resolver";
import type { AgentOut, KbPage } from "@/contracts/lkap-contracts";

/**
 * V5-10 acceptance (`docs/v5/PLAN-V5.md`): the Knowledge tab posts the seven
 * retrieval fields with the contract's defaults, plain wording throughout
 * (jargon only inside "How this works"), no `sheet` import.
 */

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
beforeEach(() => {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  // Radix `Select` ("Re-rank results") needs these in jsdom.
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.releasePointerCapture = vi.fn();
});
afterEach(() => vi.unstubAllGlobals());

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

const KBS: KbPage = {
  items: [
    {
      id: "kb-1",
      name: "Policy handbook",
      description: "",
      embedder_id: "fastembed-embedding",
      chunk_count: 12,
      document_count: 2,
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
    },
  ],
  total: 1,
} as unknown as KbPage;

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/knowledge-bases")) return jsonResponse(KBS);
      return jsonResponse({});
    }),
  );
}

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
    config: { instructions: "Hi there", pipeline: { mode: "cascaded" } },
    ...overrides,
  } as AgentOut;
}

let latest: AgentEditorForm | null = null;

function Harness({ agent: theAgent }: { agent: AgentOut }) {
  const client = React.useMemo(() => new QueryClient({ defaultOptions: { queries: { retry: false } } }), []);
  const form = useForm<AgentEditorForm>({
    resolver: zodResolver(agentEditorFormSchema),
    defaultValues: toFormValues(theAgent),
    mode: "onChange",
  });
  latest = form.watch();
  return (
    <QueryClientProvider client={client}>
      <FormProvider {...form}>
        <KnowledgeTab />
      </FormProvider>
    </QueryClientProvider>
  );
}

describe("KnowledgeTab retrieval settings (V5-10)", () => {
  it("carries the contract's defaults for a new agent's config.knowledge", async () => {
    stubFetch();
    render(<Harness agent={agent()} />);
    await screen.findByText("Policy handbook");

    expect(latest?.config.knowledge).toMatchObject({
      auto_inject: true,
      top_k: 4,
      min_score: null,
      prefetch: true,
      rerank: "none",
      mode: "hybrid",
      max_inject_tokens: 1200,
      skip_short_turns: true,
      query_mode: "conversation",
    });
  });

  it("choosing Vector only sets config.knowledge.mode", async () => {
    stubFetch();
    render(<Harness agent={agent()} />);
    await screen.findByText("Policy handbook");

    fireEvent.click(screen.getByLabelText(/Vector only/));

    await waitFor(() => expect(latest?.config.knowledge.mode).toBe("vector"));
  });

  it("turning on 'Only use strong matches' reveals the slider and sets a floor; turning it off clears it", async () => {
    stubFetch();
    render(<Harness agent={agent()} />);
    await screen.findByText("Policy handbook");

    expect(screen.queryByLabelText("Minimum match strength")).toBeNull();

    fireEvent.click(screen.getByLabelText("Only use strong matches"));
    await waitFor(() => expect(latest?.config.knowledge.min_score).toBe(0.5));
    expect(screen.getByLabelText("Minimum match strength")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Only use strong matches"));
    await waitFor(() => expect(latest?.config.knowledge.min_score).toBeNull());
    expect(screen.queryByLabelText("Minimum match strength")).toBeNull();
  });

  it("turning on re-ranking sets config.knowledge.rerank to local", async () => {
    stubFetch();
    render(<Harness agent={agent()} />);
    await screen.findByText("Policy handbook");

    fireEvent.click(screen.getByLabelText("Re-rank results"));
    const listbox = await screen.findByRole("listbox");
    fireEvent.click(within(listbox).getByText("On this server"));

    await waitFor(() => expect(latest?.config.knowledge.rerank).toBe("local"));
  });

  it("choosing 'Just this message' sets query_mode to last_turn", async () => {
    stubFetch();
    render(<Harness agent={agent()} />);
    await screen.findByText("Policy handbook");

    fireEvent.click(screen.getByLabelText(/Just this message/));

    await waitFor(() => expect(latest?.config.knowledge.query_mode).toBe("last_turn"));
  });

  it("turning off 'Skip short replies' sets skip_short_turns to false", async () => {
    stubFetch();
    render(<Harness agent={agent()} />);
    await screen.findByText("Policy handbook");

    fireEvent.click(screen.getByLabelText("Skip short replies"));

    await waitFor(() => expect(latest?.config.knowledge.skip_short_turns).toBe(false));
  });

  it("editing 'Most text to include' sets max_inject_tokens", async () => {
    stubFetch();
    render(<Harness agent={agent()} />);
    await screen.findByText("Policy handbook");

    fireEvent.change(screen.getByLabelText("Most text to include"), { target: { value: "2000" } });

    await waitFor(() => expect(latest?.config.knowledge.max_inject_tokens).toBe(2000));
  });

  it("uses plain wording — no jargon outside the How this works disclosure", async () => {
    stubFetch();
    const { container } = render(<Harness agent={agent()} />);
    await screen.findByText("Policy handbook");

    expect(screen.queryByText(/RRF|tsvector|FTS|rerank_score/i)).toBeNull();
    expect(container.querySelector("details")).toBeTruthy();
    expect(screen.getByText("How this works")).toBeTruthy();
  });
});
