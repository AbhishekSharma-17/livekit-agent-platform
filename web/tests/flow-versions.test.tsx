import * as React from "react";

import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { create } from "jsondiffpatch";

import { VersionHistory } from "@/components/console/flow/version-history";
import { diffRows, objectHash } from "@/components/console/flow/version-diff";
import type { AgentConfig, AgentOut } from "@/contracts/lkap-contracts";

/**
 * V2-16: the version history sheet — list, diff (jsondiffpatch) and
 * "restore creates a new version" (the api's `POST …/versions/{n}/restore`
 * answers with the agent at `config_version + 1`; the network is stubbed).
 */

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

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
afterEach(() => vi.restoreAllMocks());

const PROMPT: AgentConfig = { v: 2, instructions: "Be brief.", pipeline: { mode: "cascaded" }, flow: null };
const FLOW: AgentConfig = {
  ...PROMPT,
  instructions: "Be kind.",
  flow: {
    nodes: [
      { id: "start", kind: "start" },
      { id: "ask", kind: "agent", instructions: "Ask." },
    ],
    edges: [{ id: "e1", source: "start", target: "ask", condition: "always" }],
    variables: [],
  },
};

function agent(version: number, config: AgentConfig): AgentOut {
  return {
    id: "a-1",
    slug: "a",
    name: "A",
    description: "",
    pack_id: "generic",
    ui_panel_id: "composite",
    published: false,
    config_version: version,
    created_at: "2026-09-23T10:00:00Z",
    updated_at: "2026-09-23T10:00:00Z",
    mode: config.flow ? "flow" : "prompt",
    config,
  } as AgentOut;
}

describe("diffRows", () => {
  it("flattens a jsondiffpatch delta into readable rows, matching nodes by id", () => {
    const differ = create({ objectHash, arrays: { detectMove: true } });
    const rows = diffRows(differ.diff(PROMPT, FLOW));
    expect(rows).toEqual(
      expect.arrayContaining([
        { path: "instructions", kind: "changed", before: "Be brief.", after: "Be kind." },
        expect.objectContaining({ path: "flow", kind: "changed", before: null }),
      ]),
    );

    const edited = structuredClone(FLOW);
    (edited.flow?.nodes?.[1] as { instructions: string }).instructions = "Ask twice.";
    expect(diffRows(differ.diff(FLOW, edited))).toEqual([
      { path: "flow.nodes[1].instructions", kind: "changed", before: "Ask.", after: "Ask twice." },
    ]);
  });
});

describe("VersionHistory", () => {
  it("diffs a version against the current one and restores it as a new version", async () => {
    const posted: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
      if (init?.method === "POST") {
        posted.push(url);
        return json(agent(3, PROMPT));
      }
      if (url.includes("/versions/1")) {
        return json({ config_version: 1, created_at: "2026-09-22T10:00:00Z", note: "created", config: PROMPT });
      }
      return json({
        items: [
          { config_version: 2, created_at: "2026-09-23T10:00:00Z", note: null },
          { config_version: 1, created_at: "2026-09-22T10:00:00Z", note: "created" },
        ],
        total: 2,
      });
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <VersionHistory agent={agent(2, FLOW)} />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "History" }));
    const list = await screen.findByRole("list", { name: "Versions" });
    expect(within(list).getByRole("button", { name: /Version 2/ })).toHaveProperty("disabled", true);
    fireEvent.click(within(list).getByRole("button", { name: /Version 1/ }));

    const changes = await screen.findByRole("list", { name: "Changes" });
    expect(within(changes).getByText("instructions")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    const dialog = await screen.findByRole("dialog", { name: /Restore version 1/ });
    expect(within(dialog).getByText(/saved as version 3/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Restore" }));

    await waitFor(() => expect(screen.queryByRole("list", { name: "Versions" })).toBeNull());
    expect(posted).toEqual(["/api/console/agents/a-1/versions/1/restore"]);
    expect(client.getQueryData<AgentOut>(["agents", "a-1"])?.config_version).toBe(3);
  });
});
