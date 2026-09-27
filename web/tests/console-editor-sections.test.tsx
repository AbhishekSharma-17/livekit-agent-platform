import * as React from "react";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { FormProvider, useForm } from "react-hook-form";

import { toFormValues } from "@/components/console/agents/editor/form-values";
import { FlowSection } from "@/components/console/agents/editor/sections/flow-section";
import { LimitsSection } from "@/components/console/agents/editor/sections/limits-section";
import { RecordingSection } from "@/components/console/agents/editor/sections/recording-section";
import { agentEditorFormSchema, type AgentEditorForm } from "@/components/console/lib/schemas";
import { zodResolver } from "@/components/console/lib/zod-resolver";
import type { AgentOut } from "@/contracts/lkap-contracts";

/** The two sections WP-3 ships content for (recording, limits) and the flow placeholder. */

const AGENT = {
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
  config: { instructions: "Hi", pipeline: { mode: "cascaded" } },
} as AgentOut;

let latest: AgentEditorForm | null = null;

// Radix Switch measures its thumb with ResizeObserver, which jsdom lacks.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
beforeEach(() => vi.stubGlobal("ResizeObserver", ResizeObserverStub));
afterEach(() => vi.unstubAllGlobals());

function Harness({ children, agent = AGENT }: { children: React.ReactNode; agent?: AgentOut }) {
  const form = useForm<AgentEditorForm>({
    resolver: zodResolver(agentEditorFormSchema),
    defaultValues: toFormValues(agent),
    mode: "onChange",
  });
  latest = form.watch();
  return (
    <FormProvider {...form}>
      <form>{children}</form>
    </FormProvider>
  );
}

describe("RecordingSection", () => {
  it("toggles recording and only enables retention while recording is on", async () => {
    render(
      <Harness>
        <RecordingSection />
      </Harness>,
    );
    const retention = screen.getByLabelText("Keep recordings for") as HTMLInputElement;
    expect(retention.disabled).toBe(true);
    expect((screen.getByRole("switch", { name: "Audio only" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByLabelText("Storage") as HTMLInputElement).value).toBe("Default storage");

    fireEvent.click(screen.getByRole("switch", { name: "Record calls" }));
    await waitFor(() => expect(latest?.config.recording.enabled).toBe(true));
    expect(retention.disabled).toBe(false);

    fireEvent.change(retention, { target: { value: "30" } });
    await waitFor(() => expect(latest?.config.recording.retention_days).toBe(30));
    fireEvent.change(retention, { target: { value: "" } });
    await waitFor(() => expect(latest?.config.recording.retention_days).toBeNull());
  });

  it("gates 'ask for consent' and its question on recording being on (V5-15, V5-17)", async () => {
    render(
      <Harness>
        <RecordingSection />
      </Harness>,
    );
    const requireConsent = screen.getByRole("switch", { name: "Ask for consent before recording" }) as HTMLButtonElement;
    const consentText = screen.getByLabelText("Consent question") as HTMLTextAreaElement;
    expect(requireConsent.disabled).toBe(true);
    expect(consentText.disabled).toBe(true);

    fireEvent.click(screen.getByRole("switch", { name: "Record calls" }));
    await waitFor(() => expect(requireConsent.disabled).toBe(false));
    expect(consentText.disabled).toBe(true); // still off until require_consent is on

    fireEvent.click(requireConsent);
    await waitFor(() => expect(latest?.config.recording.require_consent).toBe(true));
    expect(consentText.disabled).toBe(false);

    fireEvent.change(consentText, { target: { value: "Is it okay if we record this?" } });
    await waitFor(() => expect(latest?.config.recording.consent_text).toBe("Is it okay if we record this?"));
    fireEvent.change(consentText, { target: { value: "" } });
    await waitFor(() => expect(latest?.config.recording.consent_text).toBeNull());
  });
});

describe("LimitsSection", () => {
  it("edits the limits and shows field errors", async () => {
    render(
      <Harness>
        <LimitsSection />
      </Harness>,
    );
    const concurrent = screen.getByLabelText("Calls at the same time") as HTMLInputElement;
    expect(concurrent.value).toBe("5");
    expect(screen.getByText(/The call ends when it reaches this length\. \(30m/)).toBeTruthy();

    fireEvent.change(concurrent, { target: { value: "0" } });
    expect(await screen.findByText("At least 1")).toBeTruthy();
    expect(concurrent.getAttribute("aria-invalid")).toBe("true");

    fireEvent.change(concurrent, { target: { value: "3" } });
    await waitFor(() => expect(latest?.limits.max_concurrent_sessions).toBe(3));
    await waitFor(() => expect(screen.queryByText("At least 1")).toBeNull());
  });

  it("adds, normalises, rejects and removes allowed websites", async () => {
    render(
      <Harness>
        <LimitsSection />
      </Harness>,
    );
    expect(screen.getByText("No other websites can start calls.")).toBeTruthy();
    const input = screen.getByLabelText("Add a website");

    fireEvent.change(input, { target: { value: "https://Example.com/" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(latest?.allowed_origins).toEqual(["https://example.com"]));

    fireEvent.change(input, { target: { value: "example.com/path" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByText(/Use an origin like https:\/\/example.com \(no path\)/)).toBeTruthy();
    expect(latest?.allowed_origins).toEqual(["https://example.com"]);

    fireEvent.change(input, { target: { value: "*" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    const list = await screen.findByRole("list", { name: "Allowed websites" });
    expect(within(list).getByText("* (any site)")).toBeTruthy();

    fireEvent.click(within(list).getByRole("button", { name: "Remove https://example.com" }));
    await waitFor(() => expect(latest?.allowed_origins).toEqual(["*"]));
  });
});

describe("FlowSection", () => {
  it("renders the placeholder until the flow builder replaces it", () => {
    render(<FlowSection />);
    expect(screen.getByText("This agent runs a flow")).toBeTruthy();
  });
});

describe("EDITOR_SECTIONS (V5-33)", () => {
  it("registers the Tests section after the built-ins, with the tests/publish_gate issue paths", async () => {
    const { EDITOR_SECTIONS } = await import("@/components/console/agents/editor/sections");
    const tests = EDITOR_SECTIONS.find((section) => section.id === "tests");
    expect(tests?.label).toBe("Tests");
    expect(tests?.issuePaths).toEqual(["tests", "publish_gate"]);
    expect(EDITOR_SECTIONS.map((s) => s.id)).toContain("limits");
    expect(EDITOR_SECTIONS[EDITOR_SECTIONS.length - 1]?.id).toBe("tests");
  });
});
