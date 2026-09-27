import * as React from "react";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { FormProvider, useForm } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { toFormValues } from "@/components/console/agents/editor/form-values";
import { EditorContextProvider, type EditorContextValue } from "@/components/console/agents/editor/editor-context";
import { TestsSection } from "@/components/console/agents/editor/sections/tests-section";
import { agentEditorFormSchema, type AgentEditorForm } from "@/components/console/lib/schemas";
import { zodResolver } from "@/components/console/lib/zod-resolver";
import type { AgentOut, AgentTestRun, AgentTestRunPage } from "@/contracts/lkap-contracts";

/**
 * V5-33 acceptance (`docs/v5/PLAN-V5.md`): the case dialog posts the
 * `tests[]` shape; Run enqueues and the table polls; a failing verdict
 * renders its reason; the gate switch posts `publish_gate`.
 */

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
beforeEach(() => {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.releasePointerCapture = vi.fn();
  latest = null;
});
afterEach(() => vi.unstubAllGlobals());

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

const RUN: AgentTestRun = {
  id: "run-1",
  agent_id: "a-1",
  config_version: 1,
  status: "failed",
  created_at: "2026-09-27T00:00:00Z",
  case_ids: ["booking"],
  passed: 0,
  failed: 1,
  inconclusive: 0,
  errored: 0,
  pass_ratio: 0,
  verdicts: [
    {
      case_id: "booking",
      case_name: "Books an appointment",
      status: "failed",
      scores: [{ judge: "task_completion", verdict: "fail", reason: "Never confirmed a time." }],
      turns: 3,
      transcript: [{ role: "user", text: "I need an appointment" }],
      tool_calls: [],
    },
  ],
};

const RUN_PAGE: AgentTestRunPage = { items: [{ ...RUN, verdicts: [] }] };

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/tests/runs/run-1")) return jsonResponse(RUN);
      if (url.includes("/tests/runs")) return jsonResponse(RUN_PAGE);
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

function Harness({ agent: theAgent, ctx }: { agent: AgentOut; ctx?: Partial<EditorContextValue> }) {
  const client = React.useMemo(() => new QueryClient({ defaultOptions: { queries: { retry: false } } }), []);
  const form = useForm<AgentEditorForm>({
    resolver: zodResolver(agentEditorFormSchema),
    defaultValues: toFormValues(theAgent),
    mode: "onChange",
  });
  latest = form.watch();
  const value: EditorContextValue = {
    agent: theAgent,
    sections: [],
    activeSection: "tests",
    goToSection: vi.fn(),
    issues: [],
    focusIssue: vi.fn(),
    ...ctx,
  };
  return (
    <QueryClientProvider client={client}>
      <EditorContextProvider value={value}>
        <FormProvider {...form}>
          <form>
            <TestsSection />
          </form>
        </FormProvider>
      </EditorContextProvider>
    </QueryClientProvider>
  );
}

describe("TestsSection", () => {
  it("adding a case through the dialog posts the tests[] shape", async () => {
    stubFetch();
    render(<Harness agent={agent()} />);

    fireEvent.click(screen.getByRole("button", { name: "Add case" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Books an appointment" } });
    fireEvent.change(within(dialog).getByLabelText("Who is calling"), {
      target: { value: "A polite caller wanting a Tuesday slot." },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add case" }));

    await waitFor(() => expect(latest?.config.tests).toHaveLength(1));
    expect(latest?.config.tests?.[0]).toMatchObject({
      id: "books_an_appointment",
      name: "Books an appointment",
      persona_instructions: "A polite caller wanting a Tuesday slot.",
    });
  });

  it("two cases can't share an id", async () => {
    stubFetch();
    const withCase = agent({
      config: {
        instructions: "Hi there",
        pipeline: { mode: "cascaded" },
        tests: [{ id: "booking", name: "Booking", persona_instructions: "A caller." }],
      },
    });
    render(<Harness agent={withCase} />);

    fireEvent.click(screen.getByRole("button", { name: "Add case" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Case id"), { target: { value: "booking" } });
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Another booking" } });
    fireEvent.change(within(dialog).getByLabelText("Who is calling"), { target: { value: "A caller." } });

    expect(await within(dialog).findByText("Two cases can't share an id")).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Add case" }));
    // The dialog stays open (the save was refused) — the config still has one case.
    await waitFor(() => expect(latest?.config.tests).toHaveLength(1));
  });

  it("toggling the publish gate posts publish_gate", async () => {
    stubFetch();
    render(<Harness agent={agent()} />);
    fireEvent.click(screen.getByRole("switch", { name: "Require passing tests before publish" }));
    await waitFor(() => expect(latest?.config.publish_gate?.require_tests).toBe(true));
  });

  it("Run is disabled while the form is dirty", async () => {
    stubFetch();
    const withCase = agent({
      config: {
        instructions: "Hi there",
        pipeline: { mode: "cascaded" },
        tests: [{ id: "booking", name: "Booking", persona_instructions: "A caller." }],
      },
    });
    render(<Harness agent={withCase} />);
    expect((screen.getByRole("button", { name: /Run/ }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole("switch", { name: "Require passing tests before publish" }));
    await waitFor(() => expect((screen.getByRole("button", { name: /Run/ }) as HTMLButtonElement).disabled).toBe(true));
    expect(screen.getByText(/Save your changes first/)).toBeTruthy();
  });

  it("selecting a run shows its verdicts, and a failing verdict opens with its reason", async () => {
    stubFetch();
    render(<Harness agent={agent()} />);

    fireEvent.click(await screen.findByText(/1 of 1 case passed|0 of 1 case passed/));
    fireEvent.click(await screen.findByText(/Books an appointment: failed/));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Never confirmed a time.")).toBeTruthy();
    expect(within(dialog).getByText("I need an appointment")).toBeTruthy();
  });
});
