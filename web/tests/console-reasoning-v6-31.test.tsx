import * as React from "react";

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ModelCombobox } from "@/components/console/registry/model-combobox";
import { ProviderSlotEditor } from "@/components/console/registry/provider-slot-editor";
import {
  catalogReasoning,
  effortToSend,
  isFastForVoice,
  offersEffort,
  registryReasoning,
  resolveReasoning,
} from "@/components/console/registry/reasoning";
import type { CatalogItem, ProviderRef } from "@/contracts/lkap-contracts";
import { catalogResponse, CREDENTIAL, REGISTRY, routeFetch, spec } from "./provider-models";

/**
 * V6-31: reasoning models in the console — the "Reasoning" and "Fast for
 * voice" badges in the model picker, the effort selector only for a model
 * that reasons, and Temperature hidden when the model does not take it.
 */

const LUNA_META = {
  supported_parameters: [
    "include_reasoning",
    "max_completion_tokens",
    "max_tokens",
    "reasoning",
    "reasoning_effort",
    "response_format",
    "seed",
    "structured_outputs",
    "tool_choice",
    "tools",
  ],
  reasoning: { mandatory: false, supported_efforts: ["max", "xhigh", "high", "medium", "low", "none"], default_effort: "medium" },
  architecture: { input_modalities: ["text", "image"], output_modalities: ["text"] },
};
const SOL_META = { ...LUNA_META };
const MINI_META = {
  supported_parameters: ["max_tokens", "temperature", "tool_choice", "tools", "top_p"],
  reasoning: null,
  architecture: { input_modalities: ["text", "image"], output_modalities: ["text"] },
};

const CATALOG: CatalogItem[] = [
  { id: "openai/gpt-6-luna", label: "OpenAI: GPT-6 Luna", meta: LUNA_META },
  { id: "openai/gpt-6-sol", label: "OpenAI: GPT-6 Sol", meta: SOL_META },
  { id: "openai/gpt-4.1-mini", label: "OpenAI: GPT-4.1 mini", meta: MINI_META },
];

const nativeMatches = Element.prototype.matches;
const nativeScrollIntoView = Element.prototype.scrollIntoView;
beforeAll(() => {
  Element.prototype.matches = function matches(this: Element, selector: string) {
    if (selector === ":popover-open" || selector === ":modal") return false;
    return nativeMatches.call(this, selector);
  };
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
  const { fetch } = routeFetch({
    handlers: [(req) => (req.url.includes("/catalog") && !req.url.includes("search_vendor") ? { body: catalogResponse(CATALOG) } : undefined)],
  });
  vi.stubGlobal("fetch", fetch);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

function withClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("the reasoning view", () => {
  it("reads OpenRouter's record the way the api does", () => {
    expect(catalogReasoning(LUNA_META)).toEqual({
      reasoning: true,
      efforts: ["none", "low", "medium", "high", "xhigh", "max"],
      parameters: LUNA_META.supported_parameters,
    });
    expect(catalogReasoning(MINI_META)).toMatchObject({ reasoning: false, efforts: [] });
    expect(catalogReasoning({ capabilities: { vision: true } })).toEqual({ reasoning: null, efforts: null, parameters: null });
  });

  it("picks the effort the worker sends", () => {
    const luna = catalogReasoning(LUNA_META);
    expect(effortToSend(luna, null)).toBe("none");
    expect(effortToSend(luna, "minimal")).toBe("low");
    expect(effortToSend(luna, "high")).toBe("high");
    expect(effortToSend(catalogReasoning(MINI_META), "low")).toBeNull();
    expect(effortToSend({ reasoning: null, efforts: null, parameters: null }, "high")).toBe("high");
  });

  it("marks small models that do not reason or can skip thinking as fast for voice", () => {
    expect(isFastForVoice(catalogReasoning(LUNA_META), "openai/gpt-6-luna")).toBe(true);
    expect(isFastForVoice(catalogReasoning(SOL_META), "openai/gpt-6-sol")).toBe(false);
    expect(isFastForVoice(catalogReasoning(MINI_META), "openai/gpt-4.1-mini")).toBe(true);
    expect(isFastForVoice({ reasoning: null, efforts: null, parameters: null }, "vendor/tiny-mini")).toBe(false);
  });

  it("uses the registry for LiveKit Inference's GPT-5 family and the catalog first elsewhere", () => {
    const inference = spec("livekit-inference-llm");
    expect(registryReasoning(inference, "openai/gpt-5.5")).toMatchObject({ reasoning: true, efforts: ["none", "low", "medium", "high", "xhigh"] });
    expect(offersEffort(registryReasoning(inference, "google/gemma-4-31b-it"))).toBe(false);
    const openrouter = spec("openrouter-llm");
    const item: CatalogItem = { id: "openai/gpt-4.1-mini", label: "x", meta: LUNA_META };
    expect(resolveReasoning({ spec: openrouter, modelId: "openai/gpt-4.1-mini", catalogItem: item }).reasoning).toBe(true);
    expect(resolveReasoning({ spec: openrouter, modelId: "openai/gpt-4.1-mini" }).reasoning).toBe(false);
  });
});

describe("model picker badges", () => {
  function Harness() {
    const provider = spec("openrouter-llm");
    const [value, setValue] = React.useState("");
    return (
      <ModelCombobox
        id="m"
        models={provider.models ?? []}
        value={value}
        defaultModel={provider.default_model}
        onChange={setValue}
        provider={provider}
        credentialId={CREDENTIAL.id}
      />
    );
  }

  function row(text: string): HTMLElement {
    const list = screen.getByRole("listbox");
    const node = within(list).getByText(text).closest("[cmdk-item]");
    if (!node) throw new Error(`no row ${text}`);
    return node as HTMLElement;
  }

  it("shows Reasoning on reasoning models and Fast for voice on small quick ones", async () => {
    withClient(<Harness />);
    fireEvent.click(screen.getByRole("combobox"));
    await screen.findByText("OpenAI: GPT-6 Luna");

    const luna = row("OpenAI: GPT-6 Luna");
    expect(within(luna).getByText("Reasoning")).toBeTruthy();
    expect(within(luna).getByText("Fast for voice")).toBeTruthy();
    const sol = row("OpenAI: GPT-6 Sol");
    expect(within(sol).getByText("Reasoning")).toBeTruthy();
    expect(within(sol).queryByText("Fast for voice")).toBeNull();
    const mini = row("GPT-4.1 mini");
    expect(within(mini).queryByText("Reasoning")).toBeNull();
    expect(within(mini).getByText("Fast for voice")).toBeTruthy();
  });
});

describe("LLM options", () => {
  function SlotHarness({ initial, onChange }: { initial: ProviderRef; onChange?: (next: ProviderRef | null) => void }) {
    const [value, setValue] = React.useState<ProviderRef | null>(initial);
    return (
      <ProviderSlotEditor
        kind="llm"
        value={value}
        onChange={(next) => {
          setValue(next);
          onChange?.(next);
        }}
        providers={REGISTRY}
        idPrefix="t-llm"
      />
    );
  }

  const luna: ProviderRef = { provider_id: "openrouter-llm", credential_id: CREDENTIAL.id, model: "openai/gpt-6-luna", fields: {} };
  const mini: ProviderRef = { provider_id: "openrouter-llm", credential_id: CREDENTIAL.id, model: "openai/gpt-4.1-mini", fields: {} };

  it("a reasoning model gets the effort selector and loses Temperature", async () => {
    withClient(<SlotHarness initial={luna} />);

    const effort = await screen.findByRole("combobox", { name: "Reasoning effort" });
    expect(effort.textContent).toContain("Automatic (lowest: None (no thinking))");
    expect(screen.queryByLabelText("Temperature")).toBeNull();
    expect(screen.getByText(/This model sets its own temperature/)).toBeTruthy();
  });

  it("a non-reasoning model keeps Temperature and has no effort selector", async () => {
    withClient(<SlotHarness initial={mini} />);

    await waitFor(() => expect(screen.getByLabelText("Temperature")).toBeTruthy());
    expect(screen.queryByRole("combobox", { name: "Reasoning effort" })).toBeNull();
  });

  it("a slow effort says what it costs on a live call, and Automatic clears the field", async () => {
    const changes: (ProviderRef | null)[] = [];
    withClient(<SlotHarness initial={{ ...luna, fields: { reasoning_effort: "high" } }} onChange={(next) => changes.push(next)} />);

    const effort = await screen.findByRole("combobox", { name: "Reasoning effort" });
    expect(screen.getByText(/adds several seconds to every reply/)).toBeTruthy();
    fireEvent.click(effort);
    fireEvent.click(await screen.findByRole("option", { name: /Automatic/ }));

    await waitFor(() => expect(changes.at(-1)?.fields).toEqual({}));
  });
});
