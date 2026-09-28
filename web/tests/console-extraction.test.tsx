import * as React from "react";

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { FormProvider, useForm } from "react-hook-form";

import { ExtractionTab } from "@/components/console/agents/tabs/extraction-tab";
import * as testChatStore from "@/components/console/agents/test-chat/store";
import { toFormValues } from "@/components/console/agents/editor/form-values";
import { agentEditorFormSchema, type AgentEditorForm } from "@/components/console/lib/schemas";
import { zodResolver } from "@/components/console/lib/zod-resolver";
import type { AgentOut } from "@/contracts/lkap-contracts";

/**
 * V6-15 (`docs/v6/PLAN-V6.md` V6-15, D-V6-24): the agent editor's Extraction section —
 * off by default, a fields table, a trigger picker in plain words, the "still needed"
 * checklist toggle and a privacy note. No jargon in the DOM: "extraction", "var.",
 * "regex" never appear in a visible string.
 */

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

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
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  // Radix `Select` (the field type and "where it shows" pickers) needs these in jsdom.
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.releasePointerCapture = vi.fn();
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
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
      pipeline: { mode: "cascaded" },
      panel: { panel_id: "generic", layout: "side", blocks: [] },
    },
    ...overrides,
  } as AgentOut;
}

let latest: AgentEditorForm | null = null;

function Harness({ agent: theAgent }: { agent: AgentOut }) {
  const form = useForm<AgentEditorForm>({
    resolver: zodResolver(agentEditorFormSchema),
    defaultValues: toFormValues(theAgent),
    mode: "onChange",
  });
  latest = form.watch();
  return (
    <FormProvider {...form}>
      <form>
        <ExtractionTab agent={theAgent} />
      </form>
    </FormProvider>
  );
}

describe("ExtractionTab", () => {
  beforeEach(() => {
    latest = null;
  });

  it("is off by default, with no fields", () => {
    render(<Harness agent={agent()} />);
    expect(screen.getByRole("switch", { name: "Capture details from the conversation" }).getAttribute("aria-checked")).toBe(
      "false",
    );
    expect(screen.getByText("No fields yet.")).toBeTruthy();
  });

  it("turns extraction on and adds a field, posting it in config.extraction", async () => {
    render(<Harness agent={agent()} />);
    fireEvent.click(screen.getByRole("switch", { name: "Capture details from the conversation" }));
    await waitFor(() => expect(latest?.config.extraction?.enabled).toBe(true));

    fireEvent.click(screen.getByRole("button", { name: "Add a field" }));
    await waitFor(() => expect(latest?.config.extraction?.fields).toHaveLength(1));

    const nameInput = screen.getByLabelText("Name") as HTMLInputElement;
    fireEvent.change(nameInput, { target: { value: "policy_number" } });
    await waitFor(() => expect(latest?.config.extraction?.fields[0]?.name).toBe("policy_number"));

    fireEvent.click(screen.getByRole("checkbox", { name: "Required" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Never store this" }));
    await waitFor(() => {
      expect(latest?.config.extraction?.fields[0]).toMatchObject({
        name: "policy_number",
        required: true,
        sensitive: true,
      });
    });
  });

  it("hides the tool trigger when the agent has no tools", () => {
    render(<Harness agent={agent()} />);
    expect(screen.queryByText("When one of these tools finishes")).toBeNull();
  });

  it("shows and toggles the tool trigger when the agent has tools", async () => {
    render(
      <Harness
        agent={agent({
          config: { instructions: "Hi", pipeline: { mode: "cascaded" }, tools: { tool_ids: ["lookup_policy"] } },
        })}
      />,
    );
    expect(screen.getByText("When one of these tools finishes")).toBeTruthy();

    fireEvent.click(screen.getByRole("checkbox", { name: "When one of these tools finishes" }));
    await waitFor(() => expect(latest?.config.extraction?.triggers.some((t) => t.kind === "tool")).toBe(true));
  });

  it("the still-needed toggle warns when the panel has no checklist block", async () => {
    render(<Harness agent={agent()} />);
    fireEvent.click(screen.getByRole("switch", { name: "List required details not yet captured on the checklist" }));
    await waitFor(() => expect(latest?.config.extraction?.still_needed).toBe("checklist"));
    expect(screen.getByText("Add a checklist block to the panel for this to show.")).toBeTruthy();
  });

  it("opens the test chat for the current agent, rather than faking a tool call", () => {
    const spy = vi.spyOn(testChatStore, "setTestChatOpenAgent");
    render(<Harness agent={agent({ id: "a-42" })} />);
    fireEvent.click(screen.getByRole("button", { name: "Try it in the test chat" }));
    expect(spy).toHaveBeenCalledWith("a-42");
  });

  it("names the agent's own storage tier in the privacy note, with no jargon", () => {
    const { container } = render(
      <Harness agent={agent({ config: { instructions: "Hi", pipeline: { mode: "cascaded" }, privacy: { storage_tier: "basic" } } })} />,
    );
    expect(screen.getByText(/only briefly, since this agent keeps very little/)).toBeTruthy();
    expect(container.textContent).not.toMatch(/\bvar\.|regex|extraction model|show_in/i);
  });
});
