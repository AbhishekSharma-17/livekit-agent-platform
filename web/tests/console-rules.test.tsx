import * as React from "react";

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { FormProvider, useForm } from "react-hook-form";

import { toFormValues } from "@/components/console/agents/editor/form-values";
import { RulesTab } from "@/components/console/agents/tabs/rules-tab";
import { agentEditorFormSchema, type AgentEditorForm } from "@/components/console/lib/schemas";
import { zodResolver } from "@/components/console/lib/zod-resolver";
import type { AgentOut } from "@/contracts/lkap-contracts";

/**
 * V6-15 (`docs/v6/PLAN-V6.md` V6-15, D-V6-25): the agent editor's Rules section — when →
 * then rows, a plain-words condition builder with a raw-text escape hatch validated
 * live against the same grammar the api enforces (`agents/rules/condition.ts`), and
 * action pickers. Acceptance: "an invalid condition shows the api's message inline and
 * the save is blocked."
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
  // Radix `Select` (the action and condition pickers) needs these in jsdom.
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.releasePointerCapture = vi.fn();
});
afterEach(() => vi.unstubAllGlobals());

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
let formState: ReturnType<typeof useForm<AgentEditorForm>> | null = null;

function Harness({ agent: theAgent }: { agent: AgentOut }) {
  const form = useForm<AgentEditorForm>({
    resolver: zodResolver(agentEditorFormSchema),
    defaultValues: toFormValues(theAgent),
    mode: "onChange",
  });
  latest = form.watch();
  formState = form;
  return (
    <FormProvider {...form}>
      <form>
        <RulesTab />
      </form>
    </FormProvider>
  );
}

describe("RulesTab", () => {
  beforeEach(() => {
    latest = null;
    formState = null;
  });

  it("has no rules by default", () => {
    render(<Harness agent={agent()} />);
    expect(screen.getByText("No rules yet.")).toBeTruthy();
  });

  it("adds a rule with a default action and no conditions yet", async () => {
    render(<Harness agent={agent()} />);
    fireEvent.click(screen.getByRole("button", { name: "Add a rule" }));
    await waitFor(() => expect(latest?.config.rules).toHaveLength(1));
    expect(screen.getByText("No conditions yet — this rule never fires.")).toBeTruthy();
    expect(latest?.config.rules?.[0]?.then).toEqual([{ do: "instruct", text: "" }]);
  });

  it("the condition builder writes the safe grammar as the caller fills in a row", async () => {
    render(<Harness agent={agent()} />);
    fireEvent.click(screen.getByRole("button", { name: "Add a rule" }));
    await waitFor(() => expect(latest?.config.rules).toHaveLength(1));

    fireEvent.click(screen.getByRole("button", { name: "Add a condition" }));
    await waitFor(() => expect(screen.getByLabelText("Value name")).toBeTruthy());

    fireEvent.change(screen.getByLabelText("Value name"), { target: { value: "policy_number" } });
    await waitFor(() => expect(latest?.config.rules?.[0]?.when).toBe("var.policy_number is set"));

    fireEvent.click(screen.getByLabelText("Comparison"));
    fireEvent.click(screen.getByRole("option", { name: "equals" }));
    await waitFor(() => expect(screen.getByLabelText("Value to compare with")).toBeTruthy());
    fireEvent.change(screen.getByLabelText("Value to compare with"), { target: { value: "PX-12345" } });
    await waitFor(() => expect(latest?.config.rules?.[0]?.when).toBe('var.policy_number == "PX-12345"'));
  });

  it("an invalid raw condition shows the api's own wording inline and blocks the save", async () => {
    render(<Harness agent={agent()} />);
    fireEvent.click(screen.getByRole("button", { name: "Add a rule" }));
    await waitFor(() => expect(latest?.config.rules).toHaveLength(1));

    fireEvent.click(screen.getByRole("button", { name: "Write it myself" }));
    const textarea = await screen.findByLabelText("Condition");
    fireEvent.change(textarea, { target: { value: "banana is set" } });

    await screen.findByText(
      "the condition does not read: 'banana' is not something a condition understands; name a variable as var.<name> and put text in quotes (at character 1)",
    );

    await waitFor(() => expect(formState?.formState.isValid).toBe(false));
  });

  it("a condition using 'or' stays in text mode — the builder is a one-way escape hatch past that point", async () => {
    render(<Harness agent={agent()} />);
    fireEvent.click(screen.getByRole("button", { name: "Add a rule" }));
    await waitFor(() => expect(latest?.config.rules).toHaveLength(1));

    fireEvent.click(screen.getByRole("button", { name: "Write it myself" }));
    const textarea = await screen.findByLabelText("Condition");
    fireEvent.change(textarea, { target: { value: "var.a is set or var.b is set" } });

    await screen.findByText(/combines.*or.*not.*parentheses/i);
    expect(screen.queryByRole("button", { name: "Use the builder instead" })).toBeNull();
  });

  it("changing an action's kind resets it to that kind's own shape", async () => {
    render(<Harness agent={agent()} />);
    fireEvent.click(screen.getByRole("button", { name: "Add a rule" }));
    await waitFor(() => expect(latest?.config.rules).toHaveLength(1));

    fireEvent.click(screen.getByLabelText("Action"));
    fireEvent.click(screen.getByRole("option", { name: "Change the status" }));
    await waitFor(() => expect(latest?.config.rules?.[0]?.then[0]?.do).toBe("status.set"));

    fireEvent.change(screen.getByLabelText("Status text"), { target: { value: "Escalated" } });
    await waitFor(() =>
      expect(latest?.config.rules?.[0]?.then[0]).toMatchObject({ do: "status.set", label: "Escalated" }),
    );
  });

  it("removing the only condition row leaves the rule with none (it never fires)", async () => {
    render(<Harness agent={agent()} />);
    fireEvent.click(screen.getByRole("button", { name: "Add a rule" }));
    fireEvent.click(screen.getByRole("button", { name: "Add a condition" }));
    await waitFor(() => expect(screen.getByLabelText("Value name")).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: "Remove this condition" }));
    await waitFor(() => expect(latest?.config.rules?.[0]?.when).toBe(""));
    expect(screen.getByText("No conditions yet — this rule never fires.")).toBeTruthy();
  });
});
