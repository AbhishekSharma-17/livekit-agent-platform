import * as React from "react";

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { FormProvider, useForm } from "react-hook-form";

import { BUILTIN_SECTIONS } from "@/components/console/agents/editor/builtin-sections";
import { EDITOR_EXTENSIONS } from "@/components/console/agents/editor/extensions";
import { EDITOR_SECTIONS, EDITOR_SLOTS } from "@/components/console/agents/editor/sections";
import { ConnectionChip } from "@/components/console/agents/providers-section/connection-chip";
import { ProvidersSection } from "@/components/console/agents/providers-section/providers-section";
import { providersSectionExtension } from "@/components/console/agents/providers-section/extension";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import type { AgentOut } from "@/contracts/lkap-contracts";

/**
 * V6-42 (R-V6-6, ask #325): the editor has one Providers section, and it is the
 * connection-aware one. The older `tabs/providers-tab.tsx` is deleted and the
 * `providers` extension no longer replaces a section, it only fills the
 * connection chip slot.
 */

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
beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.includes("/providers") ? { v: 2, providers: [] } : { items: [], total: 0 };
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

function Harness({ Component }: { Component: React.ComponentType<{ agent: AgentOut }> }) {
  const form = useForm<AgentEditorForm>({
    defaultValues: {
      connection_id: null,
      config: {
        pipeline: { mode: "cascaded", stt: null, llm: null, tts: null, realtime: null },
        capabilities: { camera: false, screen_share: false, chat_input: true, vision_inject_per_turn: false },
      } as unknown as AgentEditorForm["config"],
    },
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <FormProvider {...form}>
        <Component agent={{} as AgentOut} />
      </FormProvider>
    </QueryClientProvider>
  );
}

describe("the Providers section of the agent editor (V6-42)", () => {
  it("registers exactly one Providers section, and it is the connection-aware ProvidersSection", () => {
    const providers = EDITOR_SECTIONS.filter((section) => section.id === "providers");
    expect(providers).toHaveLength(1);
    expect(providers[0]?.Component).toBe(ProvidersSection);
    expect(EDITOR_SECTIONS.filter((section) => section.label === "Providers")).toHaveLength(1);
    expect(BUILTIN_SECTIONS.find((section) => section.id === "providers")?.Component).toBe(ProvidersSection);
  });

  it("keeps the longer issue keyword list, with vad, turn and noise", () => {
    const providers = EDITOR_SECTIONS.find((section) => section.id === "providers");
    for (const word of ["stt", "llm", "tts", "realtime", "provider", "credential", "model", "avatar", "image", "vad", "turn", "noise"]) {
      expect(providers?.issueKeywords?.test(`the ${word} setting is wrong`), word).toBe(true);
    }
    expect(providers?.issuePaths).toEqual(["pipeline", "connection_id"]);
  });

  it("no longer replaces a section from the extension, which only fills the connection chip slot", () => {
    expect(EDITOR_EXTENSIONS).toContain(providersSectionExtension);
    expect(providersSectionExtension.sections).toBeUndefined();
    expect(providersSectionExtension.sectionPatches).toBeUndefined();
    expect(EDITOR_SLOTS.connectionChip).toBe(ConnectionChip);
  });

  it("renders the connection-aware controls (the half-cascade mode card) from the registered component", async () => {
    const Registered = EDITOR_SECTIONS.find((section) => section.id === "providers")!.Component;
    render(<Harness Component={Registered} />);
    expect(await screen.findByText("Half-cascade")).toBeTruthy();
    expect(screen.getByRole("radiogroup", { name: "Pipeline mode" })).toBeTruthy();
  });
});
