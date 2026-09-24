import * as React from "react";

import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { FormProvider, useForm } from "react-hook-form";

import { EditorContextProvider, type EditorContextValue } from "@/components/console/agents/editor/editor-context";
import { SummaryRail } from "@/components/console/agents/editor/summary-rail";
import type { EditorSectionDef } from "@/components/console/agents/editor/types";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import type { AgentOut } from "@/contracts/lkap-contracts";

import { templateById } from "./fixtures/templates";

/**
 * The editor's "Next steps" card (docs/v4/TEMPLATES.md §6.4): shown when the
 * editor opens with `?from=<template_id>` after creating from a starter;
 * section items switch the editor section, href items link to their page,
 * and Dismiss removes `from` from the URL.
 */

const routerReplace = vi.fn();
let searchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: routerReplace, push: vi.fn() }),
  usePathname: () => "/console/agents/a-1",
  useSearchParams: () => searchParams,
}));

const AGENT: AgentOut = {
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
  config: { instructions: "Help.", pipeline: { mode: "cascaded" } },
};

const SECTION_IDS = ["providers", "instructions", "flow", "panel", "tools", "knowledge", "recording", "limits"];
const SECTIONS = SECTION_IDS.map((id) => ({ id, label: id.charAt(0).toUpperCase() + id.slice(1) })) as EditorSectionDef[];

function stubFetch(templateStatus: 200 | 404 = 200) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    const respond = (body: unknown, status = 200) => ({ ok: status < 400, status, json: async () => body }) as Response;
    const match = url.match(/^\/api\/console\/templates\/([^?]+)/);
    if (match) {
      if (templateStatus === 404) return respond({ error: { code: "not_found", message: "unknown template" } }, 404);
      return respond(templateById(decodeURIComponent(match[1])));
    }
    if (url.startsWith("/api/console/providers")) return respond({ providers: [] });
    if (url.startsWith("/api/console/packs")) return respond({ items: [] });
    if (url.startsWith("/api/console/tools")) return respond({ items: [], total: 0 });
    throw new Error(`Unhandled fetch: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function Harness({ goToSection }: { goToSection: (id: string) => void }) {
  const form = useForm<AgentEditorForm>({
    defaultValues: {
      name: AGENT.name,
      description: "",
      mode: "prompt",
      ui_panel_id: AGENT.ui_panel_id,
      config: AGENT.config,
    } as unknown as AgentEditorForm,
  });
  const ctx: EditorContextValue = {
    agent: AGENT,
    sections: SECTIONS,
    activeSection: "providers",
    goToSection,
    issues: [],
    focusIssue: () => {},
  };
  return (
    <FormProvider {...form}>
      <EditorContextProvider value={ctx}>
        <SummaryRail agent={AGENT} slots={{ testCallItems: [], headerActions: [] }} />
      </EditorContextProvider>
    </FormProvider>
  );
}

function renderRail() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const goToSection = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <Harness goToSection={goToSection} />
    </QueryClientProvider>,
  );
  return { goToSection };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
  searchParams = new URLSearchParams();
});

describe("SummaryRail — Next steps", () => {
  it("lists the receptionist's four next steps with ?from=receptionist", async () => {
    searchParams = new URLSearchParams("section=tools&from=receptionist");
    stubFetch();
    const { goToSection } = renderRail();

    const card = await screen.findByRole("region", { name: "Next steps" });
    expect(within(card).getByText("Created from Receptionist")).toBeTruthy();
    const steps = templateById("receptionist").template.next_steps ?? [];
    expect(steps).toHaveLength(4);
    const items = within(card).getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual(steps.map((step) => step.label));

    // Every receptionist step is a section of this agent's editor.
    const buttons = items.map((item) => within(item).getByRole("button"));
    expect(buttons.map((button) => button.getAttribute("data-section"))).toEqual(["tools", "knowledge", "flow", "providers"]);
    fireEvent.click(within(card).getByRole("button", { name: "Review the flow" }));
    expect(goToSection).toHaveBeenCalledWith("flow");
  });

  it("links href steps to their page (phone agent → Telephony)", async () => {
    searchParams = new URLSearchParams("from=phone_agent");
    stubFetch();
    renderRail();
    const card = await screen.findByRole("region", { name: "Next steps" });
    const links = within(card).getAllByRole("link");
    expect(links.map((link) => [link.textContent, link.getAttribute("href")])).toEqual([
      ["Attach a number and a dispatch rule", "/console/telephony"],
      ["Add a transfer destination", "/console/telephony"],
    ]);
    expect(within(card).getByRole("button", { name: "Add a storage config to record calls" }).getAttribute("data-section")).toBe(
      "recording",
    );
  });

  it("links the lead-qualification webhook step to Settings → Webhooks", async () => {
    searchParams = new URLSearchParams("from=lead_qualification");
    stubFetch();
    renderRail();
    const card = await screen.findByRole("region", { name: "Next steps" });
    expect(within(card).getByRole("link", { name: /Add a webhook for session.ended/ }).getAttribute("href")).toBe(
      "/console/settings?tab=webhooks",
    );
  });

  it("Dismiss removes `from` from the URL and keeps the rest", async () => {
    searchParams = new URLSearchParams("section=tools&from=receptionist");
    stubFetch();
    renderRail();
    const card = await screen.findByRole("region", { name: "Next steps" });
    fireEvent.click(within(card).getByRole("button", { name: "Dismiss" }));
    expect(routerReplace).toHaveBeenCalledWith("/console/agents/a-1?section=tools", { scroll: false });
  });

  it("renders nothing without ?from=, or when the starter is unknown", async () => {
    const fetchMock = stubFetch();
    renderRail();
    await screen.findByRole("complementary", { name: "Agent summary" });
    expect(screen.queryByRole("region", { name: "Next steps" })).toBeNull();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/templates/"))).toBe(false);
  });

  it("renders nothing when the api doesn't know the starter (404)", async () => {
    searchParams = new URLSearchParams("from=pack:gone");
    const fetchMock = stubFetch(404);
    renderRail();
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/templates/"))).toBe(true));
    await screen.findByRole("complementary", { name: "Agent summary" });
    expect(screen.queryByRole("region", { name: "Next steps" })).toBeNull();
  });
});
