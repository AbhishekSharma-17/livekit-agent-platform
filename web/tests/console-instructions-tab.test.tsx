import * as React from "react";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { FormProvider, useForm } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { toFormValues } from "@/components/console/agents/editor/form-values";
import { InstructionsTab } from "@/components/console/agents/tabs/instructions-tab";
import { agentEditorFormSchema, type AgentEditorForm } from "@/components/console/lib/schemas";
import { zodResolver } from "@/components/console/lib/zod-resolver";
import type { AgentOut, PacksResponse } from "@/contracts/lkap-contracts";

/** §7.6 acceptance/tests: "reset to default, counters" for the Instructions & voice section. */

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
beforeEach(() => {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  // Radix `Select` (the Conversation card's "Read tools run"/"Thinking sound"
  // pickers, V4-13) needs these in jsdom; no other test in this file opens one.
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.releasePointerCapture = vi.fn();
});
afterEach(() => vi.unstubAllGlobals());

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

const PACKS: PacksResponse = {
  items: [
    {
      manifest: {
        id: "generic",
        name: "Generic",
        default_instructions: "You are a calm, helpful assistant. Never promise a claim outcome.",
        tool_names: [],
      },
    },
  ],
} as unknown as PacksResponse;

function stubFetch(packs: PacksResponse = PACKS) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/packs")) return jsonResponse(packs);
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
        <form>
          <InstructionsTab agent={theAgent} />
        </form>
      </FormProvider>
    </QueryClientProvider>
  );
}

describe("InstructionsTab", () => {
  it("counts characters and estimates tokens as the instructions change", async () => {
    stubFetch();
    render(<Harness agent={agent()} />);

    expect(screen.getByText("8 characters · ~2 tokens")).toBeTruthy();

    const textarea = screen.getByRole("textbox", { name: "Instructions" }) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "0123456789" } });
    await waitFor(() => expect(screen.getByText("10 characters · ~3 tokens")).toBeTruthy());
  });

  it("resets to the pack default after confirmation", async () => {
    stubFetch();
    render(<Harness agent={agent()} />);

    const resetButton = await screen.findByRole("button", { name: "Reset to pack default" });
    fireEvent.click(resetButton);

    const confirm = await screen.findByRole("button", { name: "Reset" });
    fireEvent.click(confirm);

    await waitFor(() =>
      expect(latest?.config.instructions).toBe("You are a calm, helpful assistant. Never promise a claim outcome."),
    );
  });

  it("shows the mode-specific-instructions note only when the pack manifest has one", async () => {
    stubFetch({
      items: [
        {
          manifest: {
            id: "generic",
            name: "Generic",
            default_instructions: "Default.",
            instructions_by_mode: { realtime: "Realtime variant." },
            tool_names: [],
          },
        },
      ],
    } as unknown as PacksResponse);
    render(<Harness agent={agent()} />);

    expect(
      await screen.findByText(/This pack has mode-specific instructions/),
    ).toBeTruthy();
  });

  it("uses the copy table from §4.6: 'How to greet', 'Say it exactly', 'Let the model paraphrase'", async () => {
    stubFetch();
    render(<Harness agent={agent()} />);

    expect(screen.getByText("How to greet")).toBeTruthy();
    expect(screen.getByLabelText("Say it exactly")).toBeTruthy();
    expect(screen.getByLabelText("Let the model paraphrase")).toBeTruthy();
    expect(screen.queryByText(/Save \(TTS reads it verbatim\)/)).toBeNull();
  });

  it("maps the silence timeout to null when cleared ('End the call when the caller is silent for')", async () => {
    stubFetch();
    render(<Harness agent={agent()} />);

    const timeout = screen.getByLabelText(/End the call when the caller is silent for/) as HTMLInputElement;
    expect(timeout.value).toBe("15");

    fireEvent.change(timeout, { target: { value: "" } });
    await waitFor(() => expect(latest?.config.voice.user_away_timeout_s).toBeNull());
  });

  describe("Timezones (R-V5-10, V5-52)", () => {
    it("labels the existing field 'Business timezone' with the opening-hours hint, never 'IANA'", async () => {
      stubFetch();
      render(<Harness agent={agent()} />);

      expect(screen.getByText("Business timezone")).toBeTruthy();
      expect(screen.getByText("Used for opening hours and bookings.")).toBeTruthy();
      expect(screen.queryByText(/IANA/)).toBeNull();
    });

    it("defaults Caller's time to 'detect' and posts config.locale.caller_timezone on change", async () => {
      stubFetch();
      render(<Harness agent={agent()} />);

      expect(screen.getByText("Caller's time")).toBeTruthy();
      // Radix's `RadioGroupItem` is a `button[role=radio]`, not a native input.
      const detectRadio = screen.getByLabelText(/Use the caller's own timezone \(detected\)/);
      const businessRadio = screen.getByLabelText(/Always use the business timezone/);
      expect(detectRadio.getAttribute("aria-checked")).toBe("true");
      await waitFor(() => expect(latest?.config.locale?.caller_timezone).toBe("detect"));

      fireEvent.click(businessRadio);
      await waitFor(() => expect(latest?.config.locale?.caller_timezone).toBe("business"));
      expect(businessRadio.getAttribute("aria-checked")).toBe("true");
    });

    it("carries a saved 'business' caller_timezone through toFormValues", () => {
      stubFetch();
      const withBusinessLocale = agent({
        config: { instructions: "Hi there", pipeline: { mode: "cascaded" }, locale: { caller_timezone: "business" } },
      });
      render(<Harness agent={withBusinessLocale} />);

      const businessRadio = screen.getByLabelText(/Always use the business timezone/);
      expect(businessRadio.getAttribute("aria-checked")).toBe("true");
    });
  });

  describe("Conversation card (V4-13, BACKGROUND-TOOLS.md §7)", () => {
    /** Radix `Select` also renders a hidden native `<option>` mirror of every item; scope to the open listbox. */
    async function pickOption(text: string) {
      const listbox = await screen.findByRole("listbox");
      fireEvent.click(within(listbox).getByText(text));
    }

    it("posts config.tools.execution_default and config.voice.thinking_sound", async () => {
      stubFetch();
      render(<Harness agent={agent()} />);

      fireEvent.click(screen.getByLabelText("Read tools run"));
      await pickOption("Automatic — background only if slow");
      await waitFor(() => expect(latest?.config.tools.execution_default).toBe("auto"));

      fireEvent.click(screen.getByLabelText("Thinking sound"));
      await pickOption("Keyboard typing");
      await waitFor(() => expect(latest?.config.voice.thinking_sound).toBe("keyboard_typing"));
    });

    it("warns inline (data-issue-path) when a non-blocking default leaves too few tool steps", async () => {
      stubFetch();
      const { container } = render(<Harness agent={agent()} />);

      // Blocking (the default) never spends a tool step on an announcement, so no warning yet.
      expect(container.querySelector('[data-issue-path="tools.max_tool_steps"]')).toBeNull();

      fireEvent.click(screen.getByLabelText("Read tools run"));
      await pickOption("Automatic — background only if slow");

      const warning = await waitFor(() => {
        const node = container.querySelector('[data-issue-path="tools.max_tool_steps"]');
        if (!node) throw new Error("warning not shown yet");
        return node;
      });
      expect(warning.textContent).toContain("use 4 or more");
    });
  });
});
