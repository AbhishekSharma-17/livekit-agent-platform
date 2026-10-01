import * as React from "react";

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { FormProvider, useForm } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { EditorContextProvider, type EditorContextValue } from "@/components/console/agents/editor/editor-context";
import { buildAgentUpdate, toFormValues } from "@/components/console/agents/editor/form-values";
import { GuardrailsSection } from "@/components/console/agents/editor/sections/guardrails-section";
import type { EditorIssue } from "@/components/console/agents/editor/validation-map";
import { agentEditorFormSchema, type AgentEditorForm } from "@/components/console/lib/schemas";
import { zodResolver } from "@/components/console/lib/zod-resolver";
import type { AgentOut, GuardrailsConfig, ProviderSpec } from "@/contracts/lkap-contracts";
import providersJson from "../../contracts/generated/providers.json";

/**
 * V5-41 (`docs/v5/PLAN-V5.md` V5-41, ask #280): the agent editor's
 * Guardrails card — three rule lists (`config.guardrails.{input,output,
 * tool_output}`), each edited a whole rule at a time through a dialog
 * (never a side drawer); the safe reply, what happens on a trip, the
 * judging model and its time budget. No jargon ever reaches the DOM: a
 * rule's own kind is always spelled "Pattern" / "Instruction" / "Moderation
 * service", never "regex" or "classifier".
 */

const REGISTRY = (providersJson as { providers: ProviderSpec[] }).providers;

const nativeMatches = Element.prototype.matches;
beforeAll(() => {
  // Radix `Dialog`/`RadioGroup` check `:popover-open`/`:modal` in jsdom, which throws without this stub.
  Element.prototype.matches = function matches(this: Element, selector: string) {
    if (selector === ":popover-open" || selector === ":modal") return false;
    return nativeMatches.call(this, selector);
  };
});
afterAll(() => {
  Element.prototype.matches = nativeMatches;
});

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  // `ProviderSlotCard` (the judging model) and `CredentialPicker` (a moderation rule's
  // OpenAI key) both fetch through react-query; the `console-provider-slot.test.tsx`
  // precedent: real provider fixtures for `/providers`, an empty page for `/credentials`.
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.includes("/providers") ? { providers: REGISTRY } : { items: [], total: 0 };
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
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
      pipeline: {
        mode: "cascaded",
        stt: { provider_id: "deepgram-stt" },
        llm: { provider_id: "openai-llm" },
        tts: { provider_id: "cartesia-tts" },
      },
    },
    ...overrides,
  } as AgentOut;
}

let latest: AgentEditorForm | null = null;
let submitted: AgentEditorForm | null = null;

function SectionHarness({ agent: theAgent }: { agent: AgentOut }) {
  const form = useForm<AgentEditorForm>({
    resolver: zodResolver(agentEditorFormSchema),
    defaultValues: toFormValues(theAgent),
    mode: "onChange",
  });
  latest = form.watch();
  return (
    <FormProvider {...form}>
      <form
        onSubmit={form.handleSubmit((values) => {
          submitted = values;
        })}
      >
        <GuardrailsSection />
        <button type="submit">Save</button>
      </form>
    </FormProvider>
  );
}

function renderSection(theAgent: AgentOut = agent()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SectionHarness agent={theAgent} />
    </QueryClientProvider>,
  );
}

/**
 * Same harness, wrapped in a minimal `EditorContextProvider` so
 * `useSectionIssues("guardrails")` (`rule-list.tsx`/`guardrails-section.tsx`)
 * has server issues to read — the `console-provider-slot.test.tsx` precedent
 * for testing a field that reads `issueFor` outside the real editor shell.
 */
function SectionHarnessWithIssues({ agent: theAgent, issues }: { agent: AgentOut; issues: EditorIssue[] }) {
  const form = useForm<AgentEditorForm>({
    resolver: zodResolver(agentEditorFormSchema),
    defaultValues: toFormValues(theAgent),
    mode: "onChange",
  });
  latest = form.watch();
  const contextValue: EditorContextValue = {
    agent: theAgent,
    sections: [],
    activeSection: "guardrails",
    goToSection: () => {},
    issues,
    focusIssue: () => {},
  };
  return (
    <EditorContextProvider value={contextValue}>
      <FormProvider {...form}>
        <GuardrailsSection />
      </FormProvider>
    </EditorContextProvider>
  );
}

function renderSectionWithIssues(theAgent: AgentOut, issues: EditorIssue[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SectionHarnessWithIssues agent={theAgent} issues={issues} />
    </QueryClientProvider>,
  );
}

describe("GuardrailsSection", () => {
  beforeEach(() => {
    latest = null;
    submitted = null;
  });

  it("is empty by default, in every list — off means no rules, not a switch", () => {
    const { container } = renderSection();
    expect(screen.getAllByText("No rules yet.")).toHaveLength(3);
    expect(container.textContent).not.toMatch(/\bregex\b/i);
    expect(container.textContent).not.toMatch(/\bclassifier\b/i);
    expect(container.querySelector('[data-slot="sheet"]')).toBeNull();
  });

  it("adds a Pattern rule through the dialog and posts the exact shape", async () => {
    renderSection();
    fireEvent.click(screen.getAllByRole("button", { name: "Add rule" })[0]);

    const dialog = await screen.findByRole("dialog", { name: "Add a rule" });
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Card numbers" } });
    fireEvent.change(within(dialog).getByLabelText("Pattern"), { target: { value: "\\d{13,19}" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add rule" }));

    await waitFor(() => expect(latest?.config.guardrails?.input).toHaveLength(1));
    expect(latest?.config.guardrails?.input[0]).toEqual({
      kind: "regex",
      name: "Card numbers",
      pattern: "\\d{13,19}",
      ignore_case: true,
    });
    expect(screen.getByText("Card numbers")).toBeTruthy();
    expect(screen.getByText("Pattern")).toBeTruthy();
  });

  it("edits an existing rule in place through the pencil action", async () => {
    renderSection();
    fireEvent.click(screen.getAllByRole("button", { name: "Add rule" })[0]);
    let dialog = await screen.findByRole("dialog", { name: "Add a rule" });
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Cards" } });
    fireEvent.change(within(dialog).getByLabelText("Pattern"), { target: { value: "1234" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add rule" }));
    await waitFor(() => expect(latest?.config.guardrails?.input).toHaveLength(1));

    fireEvent.click(screen.getByRole("button", { name: "Edit Cards" }));
    dialog = await screen.findByRole("dialog", { name: "Edit rule" });
    expect((within(dialog).getByLabelText("Pattern") as HTMLTextAreaElement).value).toBe("1234");
    fireEvent.change(within(dialog).getByLabelText("Pattern"), { target: { value: "\\d{16}" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save rule" }));

    await waitFor(() => expect(latest?.config.guardrails?.input).toHaveLength(1));
    expect(latest?.config.guardrails?.input[0]).toEqual({
      kind: "regex",
      name: "Cards",
      pattern: "\\d{16}",
      ignore_case: true,
    });
  });

  it("adds an Instruction rule to the output list", async () => {
    renderSection();
    fireEvent.click(screen.getAllByRole("button", { name: "Add rule" })[1]);

    const dialog = await screen.findByRole("dialog", { name: "Add a rule" });
    fireEvent.click(within(dialog).getByRole("radio", { name: /Instruction/ }));
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "No medical advice" } });
    fireEvent.change(within(dialog).getByLabelText("Instruction"), {
      target: { value: "Gives medical advice: tells the caller what medicine to take." },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add rule" }));

    await waitFor(() => expect(latest?.config.guardrails?.output).toHaveLength(1));
    expect(latest?.config.guardrails?.output[0]).toEqual({
      kind: "classifier",
      name: "No medical advice",
      prompt: "Gives medical advice: tells the caller what medicine to take.",
    });
  });

  it("adds a Moderation service rule to the tool-result list, categories unchecked means any category", async () => {
    renderSection();
    fireEvent.click(screen.getAllByRole("button", { name: "Add rule" })[2]);

    const dialog = await screen.findByRole("dialog", { name: "Add a rule" });
    fireEvent.click(within(dialog).getByRole("radio", { name: /Moderation service/ }));
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "No hate speech" } });
    fireEvent.click(within(dialog).getByLabelText("Hateful content"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Add rule" }));

    await waitFor(() => expect(latest?.config.guardrails?.tool_output).toHaveLength(1));
    expect(latest?.config.guardrails?.tool_output[0]).toEqual({
      kind: "provider",
      name: "No hate speech",
      provider: "openai_moderation",
      categories: ["hate"],
      credential_id: null,
    });
  });

  it("refuses two rules in the same list sharing a name", async () => {
    renderSection();

    fireEvent.click(screen.getAllByRole("button", { name: "Add rule" })[0]);
    let dialog = await screen.findByRole("dialog", { name: "Add a rule" });
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Cards" } });
    fireEvent.change(within(dialog).getByLabelText("Pattern"), { target: { value: "1234" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add rule" }));
    await waitFor(() => expect(latest?.config.guardrails?.input).toHaveLength(1));

    fireEvent.click(screen.getAllByRole("button", { name: "Add rule" })[0]);
    dialog = await screen.findByRole("dialog", { name: "Add a rule" });
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "cards" } });
    fireEvent.change(within(dialog).getByLabelText("Pattern"), { target: { value: "5678" } });

    expect(within(dialog).getByText("Two rules in this list can't share a name")).toBeTruthy();
    expect((within(dialog).getByRole("button", { name: "Add rule" }) as HTMLButtonElement).disabled).toBe(true);
  });

  describe("test this pattern", () => {
    async function openPatternDialog() {
      renderSection();
      fireEvent.click(screen.getAllByRole("button", { name: "Add rule" })[0]);
      return screen.findByRole("dialog", { name: "Add a rule" });
    }

    it("matches a sample against the pattern", async () => {
      const dialog = await openPatternDialog();
      fireEvent.change(within(dialog).getByLabelText("Pattern"), { target: { value: "\\d{3}" } });
      fireEvent.change(within(dialog).getByLabelText("Test this pattern"), { target: { value: "call 123 now" } });
      fireEvent.click(within(dialog).getByRole("button", { name: "Test" }));
      expect(within(dialog).getByText("Matches. This would trip the rule.")).toBeTruthy();
    });

    it("reports no match", async () => {
      const dialog = await openPatternDialog();
      fireEvent.change(within(dialog).getByLabelText("Pattern"), { target: { value: "\\d{3}" } });
      fireEvent.change(within(dialog).getByLabelText("Test this pattern"), { target: { value: "no digits here" } });
      fireEvent.click(within(dialog).getByRole("button", { name: "Test" }));
      expect(within(dialog).getByText("Doesn't match this sample.")).toBeTruthy();
    });

    it("reads a Python-only pattern as 'can't check', never as invalid", async () => {
      const dialog = await openPatternDialog();
      // `(?P<name>...)` is Python's named-group syntax; it throws as a JS `RegExp`.
      fireEvent.change(within(dialog).getByLabelText("Pattern"), { target: { value: "(?P<x>abc)" } });
      fireEvent.change(within(dialog).getByLabelText("Test this pattern"), { target: { value: "abc" } });
      fireEvent.click(within(dialog).getByRole("button", { name: "Test" }));
      expect(
        within(dialog).getByText("Can't check this pattern in the browser. It's still checked when you save."),
      ).toBeTruthy();
    });
  });

  it("posts the safe reply, what happens on a trip, and the time budget", async () => {
    renderSection();
    // Lets the model picker's provider fetch settle before interacting (avoids an act() warning).
    await screen.findByText("Model for Instruction rules");

    fireEvent.change(screen.getByLabelText("Safe reply"), { target: { value: "I can't help with that." } });
    fireEvent.click(screen.getByRole("radio", { name: /Say the safe reply, then end the call/ }));
    fireEvent.change(screen.getByLabelText("Time budget"), { target: { value: "500" } });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(submitted).not.toBeNull());
    expect(submitted?.config.guardrails?.safe_reply).toBe("I can't help with that.");
    expect(submitted?.config.guardrails?.on_trip).toBe("end_call");
    expect(submitted?.config.guardrails?.budget_ms).toBe(500);
  });

  it("shows the api's message on the row, and again on the field when the rule is reopened", async () => {
    const theAgent = agent({
      config: {
        instructions: "Hi there",
        pipeline: {
          mode: "cascaded",
          stt: { provider_id: "deepgram-stt" },
          llm: { provider_id: "openai-llm" },
          tts: { provider_id: "cartesia-tts" },
        },
        guardrails: {
          input: [{ kind: "regex", name: "Card numbers", pattern: "[", ignore_case: true }],
          output: [],
          tool_output: [],
          on_trip: "interrupt",
          safe_reply: "I'm sorry, I can't help with that. Is there anything else I can help you with?",
          model: null,
          budget_ms: 300,
        },
      },
    });
    const issue: EditorIssue = {
      key: "server-issue-0",
      // As `issuesFromValidation` stores it: bracket indices already normalised to dots.
      path: "guardrails.input.0.pattern",
      message: "the pattern does not compile: unterminated character set",
      severity: "error",
      section: "guardrails",
      source: "server",
    };

    renderSectionWithIssues(theAgent, [issue]);
    expect(screen.getByText("the pattern does not compile: unterminated character set")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Edit Card numbers" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit rule" });
    expect(within(dialog).getByText("the pattern does not compile: unterminated character set")).toBeTruthy();
  });
});

describe("Guardrails ⇄ config round-trip", () => {
  it("carries every stage, the model and the trip settings through toFormValues → buildAgentUpdate unchanged", () => {
    const guardrails: GuardrailsConfig = {
      input: [{ kind: "regex", name: "Cards", pattern: "\\d{13,19}", ignore_case: false }],
      output: [{ kind: "classifier", name: "No medical advice", prompt: "Gives medical advice." }],
      tool_output: [
        { kind: "provider", name: "No hate", provider: "openai_moderation", categories: ["hate"], credential_id: "cred-1" },
      ],
      on_trip: "escalate",
      safe_reply: "Let me get someone to help.",
      model: { provider_id: "openai-llm", credential_id: "cred-2", model: "gpt-4o-mini", fields: {} },
      budget_ms: 500,
    };
    const theAgent = agent({ config: { instructions: "Hi", pipeline: { mode: "cascaded" }, guardrails } });
    const formValues = toFormValues(theAgent);
    expect(formValues.config.guardrails).toEqual(guardrails);

    const update = buildAgentUpdate(theAgent, formValues);
    expect(update.config?.guardrails).toEqual(guardrails);
  });

  it("defaults an agent saved before guardrails existed to empty, off-by-default settings", () => {
    const theAgent = agent();
    const formValues = toFormValues(theAgent);
    expect(formValues.config.guardrails).toEqual({
      input: [],
      output: [],
      tool_output: [],
      on_trip: "interrupt",
      safe_reply: "I'm sorry, I can't help with that. Is there anything else I can help you with?",
      model: null,
      budget_ms: 300,
    });
  });
});
