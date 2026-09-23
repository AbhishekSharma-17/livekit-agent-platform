import * as React from "react";

import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { FormProvider, useForm } from "react-hook-form";

import { toFormValues } from "@/components/console/agents/editor/form-values";
import { FlowCanvas } from "@/components/console/flow/flow-canvas";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import type { AgentOut, FlowSpec, ValidationResult } from "@/contracts/lkap-contracts";

/**
 * V2-16 acceptance: "invalid graph shows issues on nodes". The real canvas
 * (React Flow in jsdom, with the usual observer/matrix stubs) renders the
 * draft from the editor form; structural problems are dotted immediately, and
 * reference problems come back from `POST /v1/agents/{id}/flow/validate`
 * (the network boundary is stubbed) and land on the node the path names.
 */

// Vite's CSS pipeline (Tailwind's PostCSS plugin) doesn't run under vitest; the stylesheet is irrelevant here.
vi.mock("@xyflow/react/dist/style.css", () => ({}));

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const nativeMatches = Element.prototype.matches;
beforeAll(() => {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  vi.stubGlobal(
    "DOMMatrixReadOnly",
    class {
      m22 = 1;
    },
  );
  Element.prototype.matches = function matches(this: Element, selector: string) {
    if (selector === ":popover-open" || selector === ":modal") return false;
    return nativeMatches.call(this, selector);
  };
});
afterAll(() => {
  Element.prototype.matches = nativeMatches;
  vi.unstubAllGlobals();
});
afterEach(() => vi.restoreAllMocks());

function agentWith(flow: FlowSpec): AgentOut {
  return {
    id: "a-1",
    slug: "intake",
    name: "Intake",
    description: "",
    pack_id: "generic",
    ui_panel_id: "composite",
    published: false,
    config_version: 4,
    created_at: "2026-09-23T10:00:00Z",
    updated_at: "2026-09-23T10:00:00Z",
    mode: "flow",
    config: {
      v: 2,
      instructions: "Be helpful.",
      pipeline: { mode: "cascaded" },
      flow,
    },
  } as AgentOut;
}

function stubApi(validation: ValidationResult) {
  const calls: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    calls.push(url);
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
    if (url.includes("/flow/validate")) return json(validation);
    if (url.includes("flows/node-specs")) return json({ v: 1, nodes: [] });
    if (url.includes("/packs")) return json({ items: [] });
    if (url.includes("/providers")) return json({ v: 2, providers: [] });
    return json({ items: [], total: 0 });
  });
  return calls;
}

function Harness({ agent }: { agent: AgentOut }) {
  const form = useForm<AgentEditorForm>({ defaultValues: toFormValues(agent) });
  return (
    <FormProvider {...form}>
      <FlowCanvas agent={agent} />
    </FormProvider>
  );
}

function renderCanvas(agent: AgentOut) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Harness agent={agent} />
    </QueryClientProvider>,
  );
}

const VALID: FlowSpec = {
  nodes: [
    { id: "start", kind: "start", position: [0, 0] },
    { id: "collect", kind: "agent", label: "Collect", instructions: "Ask.", tools: ["nope"], position: [0, 150] },
    { id: "done", kind: "end", label: "Done", position: [0, 300] },
  ],
  edges: [
    { id: "e1", source: "start", target: "collect", condition: "always" },
    { id: "e2", source: "collect", target: "done", condition: "finished" },
  ],
  variables: [],
};

function card(id: string): HTMLElement | null {
  return document.querySelector(`[data-flow-node="${id}"]`);
}

describe("FlowCanvas", () => {
  it("dots a node the draft cannot reach, without a server round trip", async () => {
    const calls = stubApi({ ok: true, issues: [] });
    const flow: FlowSpec = { ...VALID, edges: VALID.edges?.slice(0, 1) };

    renderCanvas(agentWith(flow));

    await waitFor(() => expect(card("done")).not.toBeNull());
    expect(card("done")?.getAttribute("data-issue")).toBe("error");
    // React Flow keeps unmeasured nodes visibility:hidden in jsdom, so query the dot directly.
    expect(card("done")?.querySelector('[role="img"][aria-label="1 error"]')).not.toBeNull();
    expect(card("collect")?.getAttribute("data-issue")).toBeNull();
    expect(screen.getByRole("button", { name: /Flow issues: 1 error/ })).toBeTruthy();
    expect(calls.some((url) => url.includes("/flow/validate"))).toBe(false);
  });

  it("puts the api's reference issues on the node its path names", async () => {
    const calls = stubApi({
      ok: false,
      errors: ["flow.nodes[1].tools[0]: unknown tool 'nope'"],
      issues: [{ path: "flow.nodes[1].tools[0]", message: "unknown tool 'nope'", severity: "error" }],
    });

    renderCanvas(agentWith(VALID));

    await waitFor(() => expect(card("collect")?.getAttribute("data-issue")).toBe("error"), { timeout: 3000 });
    expect(card("done")?.getAttribute("data-issue")).toBeNull();
    const validate = calls.find((url) => url.includes("/flow/validate"));
    expect(validate).toContain("/api/console/agents/a-1/flow/validate");
  });
});
