import * as React from "react";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { FormProvider, useForm } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { toFormValues } from "@/components/console/agents/editor/form-values";
import { ConversationSection } from "@/components/console/agents/editor/sections/conversation-section";
import { agentEditorFormSchema, type AgentEditorForm } from "@/components/console/lib/schemas";
import { zodResolver } from "@/components/console/lib/zod-resolver";
import type { AgentOut, ProvidersResponse } from "@/contracts/lkap-contracts";

/**
 * V5-11 acceptance (`docs/v5/PLAN-V5.md`): choosing a preset posts
 * `conversation_preset` and leaves `turn_handling` untouched; editing a field
 * flips the preset to Custom and posts the typed dict; the Advanced JSON
 * round-trips unknown keys; the noise-cancellation price line renders from
 * the fixture; the raw JSON no longer appears outside Advanced.
 */

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
beforeEach(() => {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  // Radix `Select` ("Read tools run", "Thinking sound", "Background sound") needs these in jsdom.
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.releasePointerCapture = vi.fn();
  // `submitted` is module-scoped so `Harness`'s `onSubmit` closure can reach it;
  // reset per test so a later test's `waitFor(() => expect(submitted).not.toBeNull())`
  // can't pass on a previous test's leftover submission.
  submitted = null;
  latest = null;
});
afterEach(() => vi.unstubAllGlobals());

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

const PROVIDERS: ProvidersResponse = {
  providers: [
    {
      id: "legacy-noise-cancellation",
      kind: "noise_cancellation",
      label: "Noise Cancellation (LiveKit Cloud)",
      vendor: "LiveKit",
      package: "livekit-plugins-noise-cancellation",
      python_class: "livekit.plugins.noise_cancellation.BVC",
      telephony_variant: "livekit.plugins.noise_cancellation.BVCTelephony",
      price_note: "Free for the first 1,000 minutes a month, then metered.",
    },
  ],
};

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/providers")) return jsonResponse(PROVIDERS);
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
let submitted: AgentEditorForm | null = null;

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
        <form onSubmit={form.handleSubmit((values) => { submitted = values; })}>
          <ConversationSection />
          <button type="submit">Save</button>
        </form>
      </FormProvider>
    </QueryClientProvider>
  );
}

describe("ConversationSection", () => {
  it("choosing a preset posts conversation_preset and leaves turn_handling untouched", async () => {
    stubFetch();
    const withCustomTiming = agent({
      config: {
        instructions: "Hi there",
        pipeline: {
          mode: "cascaded",
          conversation_preset: "custom",
          turn_handling: { endpointing: { min_delay: 0.7 } },
        },
      },
    });
    const { container } = render(<Harness agent={withCustomTiming} />);

    fireEvent.click(container.querySelector("#preset-snappy")!);

    await waitFor(() => expect(latest?.config.pipeline.conversation_preset).toBe("snappy"));
    // `toMatchObject`, not `toEqual`: this only pins that the value the admin
    // actually set is untouched; the next test below pins the exact submitted
    // shape (no stray empty sibling groups), which is `pipelineConfigSchema`'s
    // job (`lib/schemas.ts`'s `.transform()`), not this component's.
    expect(latest?.config.pipeline.turn_handling).toMatchObject({ endpointing: { min_delay: 0.7 } });
    expect(latest?.config.pipeline.turn_handling?.endpointing?.max_delay).toBeUndefined();
  });

  it("choosing a preset doesn't add empty sibling groups to the saved turn_handling / turn_detector", async () => {
    // Mounting the Controllers for every turn-taking field (including ones the
    // stored config never set) makes RHF materialize their paths with `undefined`
    // leaves; `pipelineConfigSchema`'s `.transform()` (`lib/schemas.ts`) has to
    // prune those back out, or every save would pad `turn_handling` with inert
    // `interruption: {}` / `preemptive_generation: {}` objects and turn
    // `turn_detector: null` into `turn_detector: {}` — a real (if inert) change
    // to a config the admin never touched. This exercises the actual submitted
    // payload (`handleSubmit`'s `result.data`), not the live-watched form value,
    // since only the former goes through the schema's transform.
    stubFetch();
    const validPipeline = agent({
      config: {
        instructions: "Hi there",
        pipeline: {
          mode: "cascaded",
          stt: { provider_id: "deepgram-stt" },
          llm: { provider_id: "openai-llm" },
          tts: { provider_id: "cartesia-tts" },
          conversation_preset: "custom",
          turn_handling: { endpointing: { min_delay: 0.7 } },
          turn_detector: null,
        },
      },
    });
    const { container } = render(<Harness agent={validPipeline} />);

    fireEvent.click(container.querySelector("#preset-snappy")!);
    await waitFor(() => expect(latest?.config.pipeline.conversation_preset).toBe("snappy"));

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(submitted).not.toBeNull());
    expect(submitted?.config.pipeline.turn_handling).toEqual({ endpointing: { min_delay: 0.7 } });
    expect(submitted?.config.pipeline.turn_detector).toBeNull();
  });

  it("editing a turn-taking field flips the preset to Custom and posts the typed dict", async () => {
    stubFetch();
    const balanced = agent({
      config: { instructions: "Hi there", pipeline: { mode: "cascaded", conversation_preset: "balanced" } },
    });
    render(<Harness agent={balanced} />);

    const waitAtLeast = screen.getByLabelText("Wait at least") as HTMLInputElement;
    fireEvent.change(waitAtLeast, { target: { value: "1.2" } });

    await waitFor(() => expect(latest?.config.pipeline.conversation_preset).toBe("custom"));
    expect(latest?.config.pipeline.turn_handling?.endpointing?.min_delay).toBe(1.2);
  });

  it("the Advanced JSON round-trips unknown keys through save", async () => {
    stubFetch();
    // A complete cascaded pipeline so the form actually validates and `Save` calls through.
    const validPipeline = agent({
      config: {
        instructions: "Hi there",
        pipeline: {
          mode: "cascaded",
          stt: { provider_id: "deepgram-stt" },
          llm: { provider_id: "openai-llm" },
          tts: { provider_id: "cartesia-tts" },
        },
      },
    });
    render(<Harness agent={validPipeline} />);

    fireEvent.click(screen.getByRole("button", { name: "Advanced" }));
    const jsonField = await screen.findByLabelText("Turn handling (JSON)");
    fireEvent.change(jsonField, {
      target: { value: JSON.stringify({ endpointing: { min_delay: 0.4 }, some_future_key: true }) },
    });
    fireEvent.blur(jsonField);

    await waitFor(() => expect(latest?.config.pipeline.turn_handling?.some_future_key).toBe(true));

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(submitted).not.toBeNull());
    expect(submitted?.config.pipeline.turn_handling).toMatchObject({
      endpointing: { min_delay: 0.4 },
      some_future_key: true,
    });
  });

  it("shows an error and doesn't commit or erase invalid JSON in the Advanced field", async () => {
    stubFetch();
    render(<Harness agent={agent()} />);

    fireEvent.click(screen.getByRole("button", { name: "Advanced" }));
    const jsonField = (await screen.findByLabelText("Turn handling (JSON)")) as HTMLTextAreaElement;
    fireEvent.change(jsonField, { target: { value: "{not json" } });
    fireEvent.blur(jsonField);

    expect(await screen.findByText("Must be valid JSON")).toBeTruthy();
    // The invalid text must stay visible under the error, not be silently
    // replaced by the last-good value (the field re-syncs from the stored
    // value only once the error clears — see the effect's `!error` guard).
    expect(jsonField.value).toBe("{not json");
  });

  it("renders the noise-cancellation price line from the fixture", async () => {
    stubFetch();
    const withNc = agent({
      config: {
        instructions: "Hi there",
        pipeline: { mode: "cascaded", noise_cancellation: { provider_id: "legacy-noise-cancellation" } },
      },
    });
    render(<Harness agent={withNc} />);

    expect(await screen.findByText("Noise Cancellation (LiveKit Cloud)")).toBeTruthy();
    expect(await screen.findByText("Free for the first 1,000 minutes a month, then metered.")).toBeTruthy();
  });

  it("shows 'Off' with no price note when no noise-cancellation provider is set", async () => {
    stubFetch();
    render(<Harness agent={agent()} />);

    expect(await screen.findByText("Off")).toBeTruthy();
  });

  it("keeps the raw turn_handling JSON out of view until Advanced is opened", async () => {
    stubFetch();
    render(<Harness agent={agent()} />);

    expect(screen.queryByLabelText("Turn handling (JSON)")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Advanced" }));
    expect(await screen.findByLabelText("Turn handling (JSON)")).toBeTruthy();
  });

  it("uses plain wording for the preset and turn-taking fields, no internal key names", () => {
    stubFetch();
    render(<Harness agent={agent()} />);

    expect(screen.getByText("Balanced")).toBeTruthy();
    expect(screen.getByText("Phone call")).toBeTruthy();
    expect(screen.getByLabelText("Wait at least")).toBeTruthy();
    expect(screen.getByLabelText("Let the caller interrupt after")).toBeTruthy();
    expect(screen.getByLabelText("Reply while the caller is still finishing")).toBeTruthy();
    // The raw key names only ever appear inside the closed Advanced JSON value, never as visible copy.
    expect(screen.queryByText(/endpointing/i)).toBeNull();
    expect(screen.queryByText(/preemptive_generation/i)).toBeNull();
    expect(screen.queryByText(/turn_handling/i)).toBeNull();
  });

  it("posts config.tools.execution_default (moved from the Instructions tab, V4-13)", async () => {
    stubFetch();
    render(<Harness agent={agent()} />);

    fireEvent.click(screen.getByLabelText("Read tools run"));
    const listbox = await screen.findByRole("listbox");
    fireEvent.click(within(listbox).getByText("Automatic — background only if slow"));

    await waitFor(() => expect(latest?.config.tools.execution_default).toBe("auto"));
  });
});
