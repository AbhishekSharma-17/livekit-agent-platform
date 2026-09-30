import * as React from "react";

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { modelBlurb } from "@/components/console/registry/model-blurb";
import { ModelCombobox } from "@/components/console/registry/model-combobox";
import type { CatalogItem } from "@/contracts/lkap-contracts";

import { spec } from "./provider-models";

/**
 * V6-33: the one line under a model in a picker that says what it does, from the provider's
 * capability flags, the model's own record and the vendor's live list. Plain words, no ids.
 */

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
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ items: [], total: 0 }) }) as Response),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
});

function Picker({ providerId, fields }: { providerId: string; fields?: Record<string, unknown> }) {
  const provider = spec(providerId);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <ModelCombobox
        id="m"
        models={provider.models ?? []}
        value=""
        defaultModel={provider.default_model}
        onChange={() => {}}
        provider={provider}
        fields={fields}
      />
    </QueryClientProvider>
  );
}

function openPicker() {
  fireEvent.click(screen.getByRole("combobox"));
}

function rowOf(label: string): HTMLElement {
  const row = screen
    .getAllByText(label)
    .map((node) => node.closest("[cmdk-item]"))
    .find((node) => node !== null);
  if (!row) throw new Error(`no row ${label}`);
  return row as HTMLElement;
}

/** The list row for a model, once the list has opened (the trigger may show the same label). */
async function rowWhenListed(label: string): Promise<HTMLElement> {
  await screen.findByRole("listbox");
  return rowOf(label);
}

function blurbOf(row: HTMLElement): string | undefined {
  return row.querySelector('[data-slot="model-blurb"]')?.textContent ?? undefined;
}

describe("model picker one-liners", () => {
  it("says Deepgram Flux streams and decides the end of the turn itself", async () => {
    render(<Picker providerId="deepgram-flux-stt" />);
    openPicker();
    expect(blurbOf(await rowWhenListed("Flux (general, en)"))).toBe("Streaming · decides end of turn itself");
    expect(blurbOf(rowOf("Flux (multilingual)"))).toBe("Streaming · decides end of turn itself");
  });

  it("says an ordinary streaming transcriber streams, and nothing about turns", async () => {
    render(<Picker providerId="deepgram-stt" />);
    openPicker();
    expect(blurbOf(await rowWhenListed("Nova 3"))).toBe("Streaming");
  });

  it("says a batch entry is not streaming and waits for the whole sentence", async () => {
    render(<Picker providerId="openrouter-stt" />);
    openPicker();
    expect(blurbOf(await rowWhenListed("GPT-4o mini Transcribe"))).toBe("Not streaming: waits for the whole sentence");
  });

  it("says the same of a voice that waits for the whole sentence, and that a streaming voice streams", async () => {
    const view = render(<Picker providerId="openrouter-tts" />);
    openPicker();
    const first = spec("openrouter-tts").models?.[0]?.label ?? "";
    expect(blurbOf(await rowWhenListed(first))).toBe("Not streaming: waits for the whole sentence");
    view.unmount();

    render(<Picker providerId="deepgram-tts" />);
    openPicker();
    expect(blurbOf(await rowWhenListed("Aura 2 Andromeda (en)"))).toBe("Streaming");
  });

  it("says a reasoning model reasons and which efforts it takes, without ids", async () => {
    render(<Picker providerId="livekit-inference-llm" />);
    openPicker();
    const blurb = blurbOf(await rowWhenListed("GPT-5.6 Luna"));
    expect(blurb).toBe("Reasoning · effort none to max · Fast for voice");
    // The mono id line is not part of the note.
    expect(blurb).not.toMatch(/openai\//);
  });

  it("says a model that takes images can see them", async () => {
    render(<Picker providerId="openrouter-llm" />);
    openPicker();
    const row = await rowWhenListed("GPT-4.1 mini");
    expect(within(row).getByText(/Can see images/)).toBeTruthy();
  });

  it("says nothing for a text-only model with nothing else to say", async () => {
    render(<Picker providerId="livekit-inference-llm" />);
    openPicker();
    expect(blurbOf(await rowWhenListed("Grok 4.7"))).toBeUndefined();
  });

  it("marks Gemma 4 31B, which LiveKit serves tuned for voice, fast for voice (V6-34)", async () => {
    render(<Picker providerId="livekit-inference-llm" />);
    openPicker();
    expect(blurbOf(await rowWhenListed("Gemma 4 31B Instruct"))).toBe("Fast for voice");
  });
});

describe("modelBlurb", () => {
  const openrouter = spec("openrouter-llm");

  it("reads a switch that turns streaming on from the slot's stored options", () => {
    const stt = spec("openai-stt");
    const field = stt.capabilities?.streaming_field;
    // OpenAI transcription waits for the whole utterance unless its streaming switch is on.
    expect(field).toBeTruthy();
    expect(modelBlurb({ provider: stt, models: stt.models ?? [], modelId: "x", fields: {} })).toBe(
      "Not streaming: waits for the whole sentence",
    );
    expect(modelBlurb({ provider: stt, models: stt.models ?? [], modelId: "x", fields: { [field as string]: true } })).toBe("Streaming");
  });

  it("takes a vendor's live record for a model the provider list does not name", () => {
    const item: CatalogItem = {
      id: "acme/vision-1",
      label: "Vision 1",
      meta: { architecture: { input_modalities: ["text", "image"] } },
    };
    expect(modelBlurb({ provider: openrouter, models: [], modelId: item.id, catalogItem: item })).toBe("Can see images");
    expect(modelBlurb({ provider: openrouter, models: [], modelId: "acme/plain-1" })).toBeNull();
  });

  it("joins reasoning, speed and sight in that order", () => {
    const item: CatalogItem = {
      id: "acme/luna-mini",
      label: "Luna mini",
      meta: {
        supported_parameters: ["reasoning", "reasoning_effort", "tools"],
        reasoning: { supported_efforts: ["none", "low", "high"] },
        architecture: { input_modalities: ["text", "image"] },
      },
    };
    expect(modelBlurb({ provider: openrouter, models: [], modelId: item.id, catalogItem: item })).toBe(
      "Reasoning · effort none to high · Fast for voice · Can see images",
    );
  });

  it("says a realtime model with camera input can see video", () => {
    const live = spec("google-realtime");
    expect(modelBlurb({ provider: live, models: live.models ?? [], modelId: "gemini-3.8-live" })).toBe("Can see video");
  });

  it("stays silent without a provider, and for kinds with no model to describe", () => {
    expect(modelBlurb({ provider: undefined, models: [], modelId: "x" })).toBeNull();
    const vad = spec("silero-vad");
    expect(modelBlurb({ provider: vad, models: vad.models ?? [], modelId: "silero" })).toBeNull();
  });

  it("uses no internal words", () => {
    const lines = [
      modelBlurb({ provider: spec("deepgram-flux-stt"), models: [], modelId: "x" }),
      modelBlurb({ provider: spec("openrouter-stt"), models: [], modelId: "x" }),
    ].join(" ");
    expect(lines).not.toMatch(/slot|registry|websocket|WS|SSE|PCM/);
  });
});
