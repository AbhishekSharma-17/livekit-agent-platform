import * as React from "react";

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { FormProvider, useForm } from "react-hook-form";

import { DEFAULT_AVATAR_OPTIONS } from "@/components/console/agents/defaults";
import { ProvidersTab } from "@/components/console/agents/tabs/providers-tab";
import { EditorContextProvider, type EditorContextValue } from "@/components/console/agents/editor/editor-context";
import { ModelCombobox } from "@/components/console/registry/model-combobox";
import {
  connectionDisabledReason,
  isKnownTextOnlyLlm,
  isMvp,
  isSelectableForCredentials,
  slotAvailability,
  unavailableCopy,
} from "@/components/console/registry/provider-meta";
import { ProviderSlotCard } from "@/components/console/registry/provider-slot-card";
import { ProviderSlotEditor, type SlotConstraints } from "@/components/console/registry/provider-slot-editor";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import type { AgentOut, ProviderRef, ProviderSpec } from "@/contracts/lkap-contracts";
import providersJson from "../../contracts/generated/providers.json";

/**
 * WP-4 provider slot (docs/UI_UX_SPEC.md §4.4, §7.5 items 1–3; v2
 * amendments: composable `kind`/`value`/`onChange`/`constraints`; R-V2-1:
 * `status` gates, `verification` is informational only).
 */

const REGISTRY = (providersJson as { providers: ProviderSpec[] }).providers;

const nativeMatches = Element.prototype.matches;
const nativeScrollIntoView = Element.prototype.scrollIntoView;
beforeAll(() => {
  // See console-editor-shell.test.tsx: jsdom is very slow on these two
  // pseudo-classes, which floating-ui asks about on every Radix open.
  Element.prototype.matches = function matches(this: Element, selector: string) {
    if (selector === ":popover-open" || selector === ":modal") return false;
    return nativeMatches.call(this, selector);
  };
  // cmdk scrolls the active item into view and observes its list size.
  Element.prototype.scrollIntoView = function scrollIntoView() {};
});
afterAll(() => {
  Element.prototype.matches = nativeMatches;
  Element.prototype.scrollIntoView = nativeScrollIntoView;
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
      const body = url.includes("/providers") ? { providers: REGISTRY } : { items: [], total: 0 };
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
});

function withClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const inferenceStt: ProviderRef = {
  provider_id: "livekit-inference-stt",
  credential_id: null,
  model: "deepgram/nova-3",
  fields: {},
};

function Harness({
  kind,
  initial,
  constraints,
  onChange,
  providers = REGISTRY,
}: {
  kind: ProviderSpec["kind"];
  initial: ProviderRef | null;
  constraints?: SlotConstraints;
  onChange?: (next: ProviderRef | null) => void;
  providers?: ProviderSpec[];
}) {
  const [value, setValue] = React.useState<ProviderRef | null>(initial);
  return (
    <ProviderSlotEditor
      kind={kind}
      value={value}
      onChange={(next) => {
        setValue(next);
        onChange?.(next);
      }}
      constraints={constraints}
      providers={providers}
      idPrefix={`t-${kind}`}
    />
  );
}

describe("registry gate (R-V2-1 fallback, no ProviderOut in view)", () => {
  it("offers exactly the status=mvp entries when the fixture carries no `installed_on` (bare ProviderSpec)", () => {
    for (const spec of REGISTRY) {
      expect(slotAvailability(spec) === "selectable").toBe(spec.status === "mvp");
    }
    const base = REGISTRY.find((p) => p.id === "deepgram-stt")!;
    const noStatus = { ...base, status: undefined };
    expect(isMvp({ ...noStatus, availability: "available", worker_image: "slim" })).toBe(true);
    expect(isMvp({ ...noStatus, availability: "available", worker_image: "full" })).toBe(false);
    expect(isMvp({ ...noStatus, availability: "deferred", worker_image: "slim" })).toBe(false);
  });

  it("never gates on verification", () => {
    const base = REGISTRY.find((p) => p.id === "deepgram-stt")!;
    expect(slotAvailability({ ...base, verification: "unverified" })).toBe("selectable");
    expect(slotAvailability({ ...base, verification: "verified" })).toBe("selectable");
  });

  it("gives every non-selectable entry a chip and a sentence", () => {
    const deferred = { ...REGISTRY[0], status: "deferred" as const, availability: "deferred" as const };
    expect(unavailableCopy(deferred).chip).toBe("Coming soon");
    const full = { ...REGISTRY[0], status: "deferred" as const, availability: "available" as const, worker_image: "full" as const };
    expect(unavailableCopy(full).chip).toBe("Not installed");
    const incompatible = { ...REGISTRY[0], status: "deferred" as const, availability: "incompatible" as const, notes: "Pinned SDK conflict." };
    expect(unavailableCopy(incompatible)).toEqual({ chip: "Not available", reason: "Pinned SDK conflict." });
  });
});

describe("registry gate (R-V2-2: availability + installed_on + enabled)", () => {
  const base = REGISTRY.find((p) => p.id === "deepgram-stt")!;
  const connA = { id: "conn-a", name: "cloud-a", deployment_type: "cloud" as const };
  const connB = { id: "conn-b", name: "self-a", deployment_type: "self_hosted" as const };

  it("is selectable on a connection only when installed_on includes it, regardless of worker_image/status", () => {
    const installedOnA = { ...base, status: undefined, worker_image: "full" as const, installed_on: ["conn-a"] };
    expect(slotAvailability(installedOnA, { connection: connA })).toBe("selectable");
    expect(slotAvailability(installedOnA, { connection: connB })).toBe("not-installed");
    expect(unavailableCopy(installedOnA, { connection: connB }).reason).toContain("self-a");
  });

  it("a disabled provider is never selectable, even when installed", () => {
    const disabled = { ...base, installed_on: ["conn-a"], enabled: false };
    expect(slotAvailability(disabled, { connection: connA })).toBe("disabled");
  });

  it("cloud_only providers are unavailable on a self-hosted connection", () => {
    const cloudOnly = {
      ...base,
      installed_on: ["conn-a", "conn-b"],
      capabilities: { ...base.capabilities, cloud_only: true },
    };
    expect(slotAvailability(cloudOnly, { connection: connA })).toBe("selectable");
    expect(slotAvailability(cloudOnly, { connection: connB })).toBe("cloud-only");
    expect(unavailableCopy(cloudOnly, { connection: connB }).reason).toMatch(/LiveKit Cloud/);
  });

  it("with no connection in view, installed_on is ignored — availability + enabled decide (the static registry export always reports installed_on: [])", () => {
    const knownEmpty = { ...base, installed_on: [] };
    expect(slotAvailability(knownEmpty)).toBe("selectable");
    const noEnrichment = { ...base, installed_on: undefined };
    expect(slotAvailability(noEnrichment)).toBe("selectable");
    const disabledEverywhere = { ...base, installed_on: [], enabled: false };
    expect(slotAvailability(disabledEverywhere)).toBe("disabled");
  });

  it("connectionDisabledReason mirrors unavailableCopy's reason, and is null once installed and enabled", () => {
    const installed = { ...base, installed_on: ["conn-a"] };
    expect(connectionDisabledReason(installed, connA)).toBeNull();
    expect(connectionDisabledReason(installed, connB)).toContain("self-a");
    expect(connectionDisabledReason(installed, null)).toBeNull();
  });

  it("isSelectableForCredentials ignores installed_on (a key can be added ahead of any connection)", () => {
    const nowhereInstalled = { ...base, installed_on: [] };
    expect(isSelectableForCredentials(nowhereInstalled)).toBe(true);
    const disabledForCredentials = { ...base, enabled: false };
    expect(isSelectableForCredentials(disabledForCredentials)).toBe(false);
    const deferred = { ...base, availability: "deferred" as const };
    expect(isSelectableForCredentials(deferred)).toBe(false);
  });
});

describe("ProviderSlotEditor", () => {
  it("switches from LiveKit Inference to your own key and back without losing the Inference config", () => {
    const onChange = vi.fn();
    withClient(<Harness kind="stt" initial={inferenceStt} onChange={onChange} />);

    const inference = screen.getByRole("radio", { name: /LiveKit Inference/ }) as HTMLInputElement;
    const own = screen.getByRole("radio", { name: /Your own key/ }) as HTMLInputElement;
    expect(inference.checked).toBe(true);
    // Inference: no vendor list, no key picker.
    expect(screen.queryByRole("radiogroup", { name: "Vendor" })).toBeNull();
    expect(screen.queryByLabelText(/^Key/)).toBeNull();

    fireEvent.click(own);
    expect(onChange).toHaveBeenLastCalledWith(null);
    const vendors = screen.getByRole("radiogroup", { name: "Vendor" });
    fireEvent.click(within(vendors).getByRole("radio", { name: /Deepgram/ }));
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ provider_id: "deepgram-stt", credential_id: null, model: expect.any(String) }),
    );
    // Own key → the key picker appears.
    expect(screen.getByRole("button", { name: /Add key/ })).toBeTruthy();

    fireEvent.click(screen.getByRole("radio", { name: /LiveKit Inference/ }));
    expect(onChange).toHaveBeenLastCalledWith(inferenceStt);
  });

  it("fills an empty slot when LiveKit Inference is picked", () => {
    const onChange = vi.fn();
    withClient(<Harness kind="llm" initial={null} onChange={onChange} />);
    const inference = screen.getByRole("radio", { name: /LiveKit Inference/ }) as HTMLInputElement;
    expect(inference.checked).toBe(false);
    fireEvent.click(inference);
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ provider_id: "livekit-inference-llm" }));
  });

  it("shows non-mvp providers disabled under More providers, with a reason chip, and never as radios", () => {
    withClient(<Harness kind="stt" initial={{ provider_id: "deepgram-stt", credential_id: null, model: null, fields: {} }} />);
    const vendors = screen.getByRole("radiogroup", { name: "Vendor" });
    const radios = within(vendors).getAllByRole("radio");
    const selectableStt = REGISTRY.filter((p) => p.kind === "stt" && p.status === "mvp" && !p.id.startsWith("livekit-inference"));
    expect(radios).toHaveLength(selectableStt.length);

    const unavailable = REGISTRY.filter((p) => p.kind === "stt" && p.status !== "mvp" && p.availability !== "removed");
    const toggle = screen.getByRole("button", { name: `More providers (${unavailable.length})` });
    fireEvent.click(toggle);
    const sample = unavailable[0];
    const item = document.querySelector(`[data-provider-id="${sample.id}"][data-unavailable]`);
    expect(item).not.toBeNull();
    expect(item!.querySelector("input")).toBeNull();
    expect(screen.getAllByText(unavailableCopy(sample).chip).length).toBeGreaterThan(0);
  });

  it("shows a small Verified chip for verified providers (informational only)", () => {
    const verified = REGISTRY.find((p) => p.id === "deepgram-stt")!;
    const providers = REGISTRY.map((p) => (p.id === verified.id ? { ...p, verification: "verified" as const } : p));
    withClient(<Harness kind="stt" initial={{ provider_id: verified.id, credential_id: null, model: null, fields: {} }} providers={providers} />);
    const card = document.querySelector(`[data-provider-id="${verified.id}"]`)!;
    expect(within(card as HTMLElement).getByText("Verified")).toBeTruthy();
    expect(within(card as HTMLElement).getByRole("radio")).toBeTruthy();
  });

  it("composes constraints: disabledReason moves a provider out of the list; inference off hides the run choice", () => {
    withClient(
      <Harness
        kind="stt"
        initial={null}
        constraints={{
          inference: "off",
          disabledReason: (spec) => (spec.id === "deepgram-stt" ? "Not installed on self-a — add it to the worker image." : null),
        }}
      />,
    );
    expect(screen.queryByRole("radio", { name: /LiveKit Inference/ })).toBeNull();
    const vendors = screen.getByRole("radiogroup", { name: "Vendor" });
    expect(within(vendors).queryByRole("radio", { name: /Deepgram/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /More providers/ }));
    expect(screen.getByText("Not installed on self-a — add it to the worker image.")).toBeTruthy();
  });

  it("renders the TTS voice option as a select from capabilities.voices", () => {
    withClient(
      <Harness
        kind="tts"
        initial={{ provider_id: "livekit-inference-tts", credential_id: null, model: null, fields: { voice: "", language: "en" } }}
      />,
    );
    expect(document.querySelector("#t-tts-field-voice")?.getAttribute("role")).toBe("combobox");
  });

  it("shows a provider's notes when selected (V4-04: the OpenRouter STT latency caveat)", () => {
    const openrouterStt = REGISTRY.find((p) => p.id === "openrouter-stt")!;
    expect(openrouterStt.notes).toBeTruthy();
    withClient(
      <Harness
        kind="stt"
        initial={{ provider_id: openrouterStt.id, credential_id: null, model: null, fields: {} }}
      />,
    );
    const notes = document.querySelector('[data-slot="provider-notes"]');
    expect(notes?.textContent).toBe(openrouterStt.notes);
  });

  it("shows nothing when the selected provider has no notes", () => {
    const deepgram = REGISTRY.find((p) => p.id === "deepgram-stt")!;
    expect(deepgram.notes ?? null).toBeNull();
    withClient(<Harness kind="stt" initial={{ provider_id: deepgram.id, credential_id: null, model: null, fields: {} }} />);
    expect(document.querySelector('[data-slot="provider-notes"]')).toBeNull();
  });
});

describe("ModelCombobox", () => {
  const models = [
    { id: "google/gemini-3.5-flash", label: "Gemini 3.5 Flash", supports_video: true },
    { id: "openai/gpt-5-mini", label: "GPT-5 mini" },
  ];

  it("shows the label first and the id in mono, marking the default", () => {
    withClient(<ModelCombobox id="m" models={models} value="" defaultModel="google/gemini-3.5-flash" onChange={() => {}} />);
    const trigger = screen.getByRole("combobox");
    expect(trigger.textContent).toMatch(/^Gemini 3\.5 Flash/);
    expect(trigger.textContent).toContain("Default");
    expect(trigger.querySelector(".font-mono")?.textContent).toBe("google/gemini-3.5-flash");
  });

  it("accepts a custom id typed into the search", async () => {
    const onChange = vi.fn();
    withClient(<ModelCombobox id="m" models={models} value="" defaultModel={null} onChange={onChange} />);
    fireEvent.click(screen.getByRole("combobox"));
    fireEvent.change(await screen.findByPlaceholderText(/Search models/), { target: { value: "acme/voice-9" } });
    fireEvent.click(await screen.findByText(/Use custom model:/));
    expect(onChange).toHaveBeenCalledWith("acme/voice-9");
  });

  it("filters to vision models when asked", async () => {
    withClient(<ModelCombobox id="m" models={models} value="" defaultModel={null} onChange={() => {}} open visionOnly />);
    expect(await screen.findByText("Gemini 3.5 Flash")).toBeTruthy();
    expect(screen.queryByText("GPT-5 mini")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Vision only" }));
    expect(await screen.findByText("GPT-5 mini")).toBeTruthy();
  });
});

describe("ProviderSlotCard", () => {
  it("summarises provider, model label, badges and a missing key; Edit expands the editor", () => {
    function CardHarness() {
      const [open, setOpen] = React.useState(false);
      return (
        <ProviderSlotCard
          title="Language model"
          kind="llm"
          value={{ provider_id: "openai-llm", credential_id: null, model: null, fields: {} }}
          onChange={() => {}}
          providers={REGISTRY}
          expanded={open}
          onExpandedChange={setOpen}
          idPrefix="card-llm"
        />
      );
    }
    withClient(<CardHarness />);
    const card = screen.getByRole("region", { name: "Language model" });
    expect(within(card).getByText("OpenAI")).toBeTruthy();
    expect(within(card).getByText("Key required")).toBeTruthy();
    const edit = within(card).getByRole("button", { name: /Edit/ });
    expect(edit.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(edit);
    expect(within(card).getByRole("button", { name: /Done/ }).getAttribute("aria-expanded")).toBe("true");
    expect(card.querySelector('[data-slot="provider-slot-editor"]')).not.toBeNull();
  });
});

function TabHarness({ values }: { values: Partial<AgentEditorForm["config"]> }) {
  const form = useForm<AgentEditorForm>({
    defaultValues: {
      config: {
        pipeline: {
          mode: "cascaded",
          stt: inferenceStt,
          llm: { provider_id: "livekit-inference-llm", credential_id: null, model: "google/gemini-3.5-flash", fields: {} },
          tts: { provider_id: "livekit-inference-tts", credential_id: null, model: null, fields: {} },
          realtime: null,
          avatar: null,
          image_gen: null,
          workflow_llm: null,
        },
        capabilities: { camera: false, screen_share: false, chat_input: true, vision_inject_per_turn: false },
        ...values,
      } as AgentEditorForm["config"],
    },
  });
  return (
    <FormProvider {...form}>
      <ProvidersTab />
      <output data-testid="mode">{form.watch("config.pipeline.mode")}</output>
      <output data-testid="dirty">{String(form.formState.isDirty)}</output>
    </FormProvider>
  );
}

describe("ProvidersTab", () => {
  it("shows the cascaded pipeline in order and switches to realtime from the mode cards", async () => {
    withClient(<TabHarness values={{}} />);
    const slots = await screen.findByRole("list", { name: "Pipeline slots" });
    const titles = within(slots)
      .getAllByRole("heading", { level: 3 })
      .map((h) => h.textContent);
    expect(titles).toEqual(["Speech-to-text", "Language model", "Text-to-speech"]);

    fireEvent.click(screen.getByRole("radio", { name: /Realtime/ }));
    expect(screen.getByTestId("mode").textContent).toBe("realtime");
    await waitFor(() =>
      expect(
        within(screen.getByRole("list", { name: "Pipeline slots" }))
          .getAllByRole("heading", { level: 3 })
          .map((h) => h.textContent),
      ).toEqual(["Realtime model"]),
    );
  });

  it("keeps one slot open at a time", async () => {
    withClient(<TabHarness values={{}} />);
    await screen.findByRole("list", { name: "Pipeline slots" });
    fireEvent.click(screen.getByRole("button", { name: /Edit speech-to-text/i }));
    fireEvent.click(screen.getByRole("button", { name: /Edit language model/i }));
    expect(screen.getAllByRole("button", { name: /Done/ })).toHaveLength(1);
  });

  it("puts optional slots behind Add buttons and removes them again", async () => {
    withClient(<TabHarness values={{}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Add avatar" }));
    const avatar = screen.getByRole("region", { name: "Avatar" });
    // Avatars have no Inference option: the vendor list shows directly.
    expect(within(avatar).queryByRole("radio", { name: /LiveKit Inference/ })).toBeNull();
    fireEvent.click(within(avatar).getByRole("radio", { name: /Beyond Presence/ }));
    expect(screen.getByTestId("dirty").textContent).toBe("true");
    fireEvent.click(within(avatar).getByRole("button", { name: "Remove" }));
    expect(screen.queryByRole("region", { name: "Avatar" })).toBeNull();
    expect(screen.getByRole("button", { name: "Add avatar" })).toBeTruthy();

    // An added slot left empty can be dismissed too.
    fireEvent.click(screen.getByRole("button", { name: "Add image generation" }));
    fireEvent.click(within(screen.getByRole("region", { name: "Image generation" })).getByRole("button", { name: "Remove" }));
    expect(screen.queryByRole("region", { name: "Image generation" })).toBeNull();
  });

  it("warns on the language model when the camera is on and the model is text-only", async () => {
    const textOnly = REGISTRY.find((p) => p.id === "livekit-inference-llm")!.models!.find((m) => !m.supports_video);
    expect(textOnly).toBeTruthy();
    expect(isKnownTextOnlyLlm(REGISTRY, { provider_id: "livekit-inference-llm", model: textOnly!.id })).toBe(true);

    withClient(
      <TabHarness
        values={{
          pipeline: {
            mode: "cascaded",
            stt: inferenceStt,
            llm: { provider_id: "livekit-inference-llm", credential_id: null, model: textOnly!.id, fields: {} },
            tts: { provider_id: "livekit-inference-tts", credential_id: null, model: null, fields: {} },
            avatar_options: DEFAULT_AVATAR_OPTIONS,
          },
          capabilities: { camera: true, screen_share: false, chat_input: true, vision_inject_per_turn: true },
        }}
      />,
    );
    expect(await screen.findByText(/This model can't see images/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Show vision models" }));
    // The LLM card opens with the model list filtered to vision models.
    expect(await screen.findByRole("button", { name: "Vision only", pressed: true })).toBeTruthy();
  });

  it("shows the half-cascade slots and a note when an agent already uses it", async () => {
    withClient(
      <TabHarness
        values={{
          pipeline: {
            mode: "half_cascade",
            realtime: { provider_id: "google-realtime", credential_id: null, model: null, fields: {} },
            tts: { provider_id: "livekit-inference-tts", credential_id: null, model: null, fields: {} },
            avatar_options: DEFAULT_AVATAR_OPTIONS,
          },
        }}
      />,
    );
    expect(await screen.findByText(/uses half-cascade/)).toBeTruthy();
    const titles = within(screen.getByRole("list", { name: "Pipeline slots" }))
      .getAllByRole("heading", { level: 3 })
      .map((h) => h.textContent);
    expect(titles).toEqual(["Realtime model", "Text-to-speech"]);
  });
});

function makeAgentOut(overrides: Partial<AgentOut> = {}): AgentOut {
  return {
    id: "agent-1",
    slug: "agent-1",
    name: "Test agent",
    description: "",
    pack_id: "generic",
    ui_panel_id: "generic",
    published: false,
    config_version: 1,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    mode: "prompt",
    connection_id: null,
    limits: {},
    allowed_origins: [],
    config: {
      v: 2,
      instructions: "Be helpful.",
      pipeline: { mode: "cascaded", stt: inferenceStt, llm: null, tts: null },
      voice: {},
      capabilities: { camera: false, screen_share: false, chat_input: true, vision_inject_per_turn: false },
      tools: { builtin_disabled: [], http_request_enabled: false, tool_ids: [], max_tool_steps: 3 },
      knowledge: { kb_ids: [], auto_inject: true, top_k: 4 },
      panel: { panel_id: "generic", layout: "wide", blocks: [] },
      recording: { enabled: false, audio_only: true, storage_config_id: null, retention_days: null },
      pack_settings: {},
      timezone: "UTC",
    },
    ...overrides,
  } as AgentOut;
}

const EDITOR_CONTEXT_STUB: EditorContextValue = {
  agent: makeAgentOut(),
  sections: [],
  activeSection: "providers",
  goToSection: () => {},
  issues: [],
  focusIssue: () => {},
};

/** `ProvidersTab` inside a minimal `EditorContextProvider`, so `useDraftCostEstimate` has an agent to price. */
function TabWithEstimate({ values }: { values: Partial<AgentEditorForm["config"]> }) {
  return (
    <EditorContextProvider value={EDITOR_CONTEXT_STUB}>
      <TabHarness values={values} />
    </EditorContextProvider>
  );
}

describe("ProvidersTab — cost estimate (docs/v4/COSTS.md §5 item 2)", () => {
  const COST_ESTIMATE = {
    per_minute_usd: { low: "0.03", mid: "0.04", high: "0.05" },
    session_minutes: 5,
    channel: "web",
    lines: [
      {
        slot: "stt",
        label: "Caller's speech → text",
        provider_id: "deepgram-stt",
        model: "nova-3",
        unit: "audio_s_in",
        quantity_per_min: "60",
        usd_per_min: "0.0048",
        quote: { provider_id: "deepgram-stt", unit: "audio_s_in", usd_per_unit: "0.00008", source: "table", as_of: "2026-09-23" },
      },
      {
        slot: "llm",
        label: "Agent's thinking",
        provider_id: "acme-llm",
        model: null,
        unit: "tokens_in",
        note: "no price",
      },
    ],
    assumptions: [],
    unpriced: ["Agent's thinking — acme-llm"],
    priced_share: 0.5,
    price_version: "2026-09-23",
    as_of: "2026-09-23",
    sources: ["table"],
    caveats: [],
  };

  function stubFetch() {
    return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes("/cost-estimates/assumptions")) {
        return { ok: true, status: 200, json: async () => ({ assumptions: [], sessions_sampled: 0 }) } as Response;
      }
      if (url.includes("/cost-estimates") && method === "POST") {
        return { ok: true, status: 200, json: async () => COST_ESTIMATE } as Response;
      }
      if (url.includes("/providers")) return { ok: true, status: 200, json: async () => ({ providers: REGISTRY }) } as Response;
      if (url.includes("auth/me")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ user: { id: "u1", email: "a@b.test" }, workspaces: [{ id: "w1", name: "W", slug: "w", role: "admin" }] }),
        } as Response;
      }
      return { ok: true, status: 200, json: async () => ({ items: [], total: 0 }) } as Response;
    });
  }

  beforeEach(() => {
    vi.stubGlobal("fetch", stubFetch());
  });

  it("shows the same '≈ $/min · estimate' figure in the Pipeline header as the rail would", async () => {
    withClient(<TabWithEstimate values={{}} />);
    await waitFor(() => expect(screen.getByText(/≈ \$0\.0400\/min · estimate/)).toBeTruthy(), { timeout: 3000 });
  });

  it("shows a priced slot's own chip and 'no price' with Set a price for an unpriced, admin-only slot", async () => {
    withClient(<TabWithEstimate values={{}} />);
    const sttCard = await screen.findByRole("region", { name: "Speech-to-text" });
    await waitFor(() => expect(within(sttCard).getByText(/≈ \$0\.0048\/min · estimate/)).toBeTruthy(), { timeout: 3000 });

    const llmCard = screen.getByRole("region", { name: "Language model" });
    await waitFor(() => expect(within(llmCard).getByText("no price")).toBeTruthy(), { timeout: 3000 });
    expect(within(llmCard).getByRole("button", { name: "Set a price" })).toBeTruthy();
  });
});
