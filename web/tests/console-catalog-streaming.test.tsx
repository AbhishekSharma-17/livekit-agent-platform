import * as React from "react";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";

import {
  INFERENCE_CREDITS_LINE,
  isOpenRouterSpeech,
  isRecommendedProvider,
  notForLiveCallsChip,
  RECOMMENDED_STACK,
  speechStreams,
  streamingChipCopy,
} from "@/components/console/registry/provider-meta";
import { newProviderRef, ProviderSlotEditor } from "@/components/console/registry/provider-slot-editor";
import type { ProviderRef, ProviderSpec } from "@/contracts/lkap-contracts";
import providersJson from "../../contracts/generated/providers.json";

/**
 * V6-03 (docs/v6/PLAN-V6.md, ask #16): the console's streaming chips, the
 * "not for live calls" note on OpenRouter's two speech entries, the
 * "Recommended" stack per connection type, the credits line's dated
 * constant, and the pre-selection rule for a *new* provider ref (never a
 * stored one — the compatibility rule, U-V6-2).
 *
 * `newProviderRef` (the only caller of `recommendedFieldValues`) is called
 * from exactly two sites in `provider-slot-editor.tsx`: `chooseRun`'s
 * "inference" branch and `chooseVendor` — both fire only when a builder
 * actively picks a run choice or a vendor, never to backfill an existing
 * `ProviderRef`'s missing fields. Verified by `grep -rn "newProviderRef" src`
 * turning up no other caller.
 */

const REGISTRY = (providersJson as { providers: ProviderSpec[] }).providers;

function findSpec(id: string): ProviderSpec {
  const spec = REGISTRY.find((p) => p.id === id);
  if (!spec) throw new Error(`fixture is missing ${id} — did contracts regenerate?`);
  return spec;
}

beforeEach(() => {
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

describe("speechStreams (mirrors lkap_contracts.providers.speech_streams, ask #16)", () => {
  it("reads the registry's recorded value for a speech entry with no streaming field", () => {
    expect(speechStreams(findSpec("livekit-inference-stt"))).toBe(true);
    expect(speechStreams(findSpec("openrouter-stt"))).toBe(false);
    expect(speechStreams(findSpec("openrouter-tts"))).toBe(false);
  });

  it("turns true only when the reference sets the entry's streaming_field", () => {
    const openaiStt = findSpec("openai-stt");
    expect(openaiStt.capabilities?.streaming_field).toBe("use_realtime");
    expect(speechStreams(openaiStt, {})).toBe(false);
    expect(speechStreams(openaiStt, { use_realtime: false })).toBe(false);
    expect(speechStreams(openaiStt, { use_realtime: true })).toBe(true);
  });

  it("is null for a non-speech kind and for an entry the registry hasn't recorded", () => {
    expect(speechStreams(findSpec("livekit-inference-llm"))).toBeNull();
    const unrecorded = findSpec("baseten-tts");
    expect(unrecorded.capabilities?.streaming ?? null).toBeNull();
    expect(speechStreams(unrecorded)).toBeNull();
  });
});

describe("streamingChipCopy / notForLiveCallsChip (D-V6-1, D-V6-2)", () => {
  it("labels a streaming entry 'Streams' with a success tone", () => {
    expect(streamingChipCopy(findSpec("livekit-inference-stt"))).toEqual({ label: "Streams", tone: "success", tip: undefined });
  });

  it("labels a non-streaming entry 'Waits for the whole sentence', carrying the registry's note as the tip", () => {
    const copy = streamingChipCopy(findSpec("openai-stt"), {});
    expect(copy?.label).toBe("Waits for the whole sentence");
    expect(copy?.tone).toBe("neutral");
    expect(copy?.tip).toMatch(/realtime-only models/);
  });

  it("says nothing (null) for an entry the registry hasn't recorded, and for a non-speech kind", () => {
    expect(streamingChipCopy(findSpec("baseten-tts"))).toBeNull();
    expect(streamingChipCopy(findSpec("livekit-inference-llm"))).toBeNull();
  });

  it("flags OpenRouter's two speech entries as 'Not for live calls', and no other provider", () => {
    expect(isOpenRouterSpeech("openrouter-stt")).toBe(true);
    expect(isOpenRouterSpeech("openrouter-tts")).toBe(true);
    expect(notForLiveCallsChip({ id: "openrouter-stt" })).toBe("Not for live calls");
    expect(notForLiveCallsChip({ id: "openrouter-tts" })).toBe("Not for live calls");
    expect(notForLiveCallsChip({ id: "deepgram-stt" })).toBeNull();
  });

  it("never uses jargon ('WS', 'PCM', 'SSE') in any chip label, tone name aside", () => {
    for (const spec of REGISTRY) {
      if (spec.kind !== "stt" && spec.kind !== "tts") continue;
      const copy = streamingChipCopy(spec);
      if (!copy) continue;
      expect(copy.label).not.toMatch(/\bWS\b|\bPCM\b|\bSSE\b/);
    }
  });
});

describe("isRecommendedProvider (D-V6-3: the recommended stack per connection type)", () => {
  it("recommends LiveKit Inference on a Cloud connection, and nothing on self-hosted", () => {
    const inferenceStt = findSpec("livekit-inference-stt");
    expect(isRecommendedProvider(inferenceStt, "cloud")).toBe(true);
    expect(isRecommendedProvider(inferenceStt, "self_hosted")).toBe(false);
    expect(isRecommendedProvider(inferenceStt, null)).toBe(false);
  });

  it("recommends Deepgram + Cartesia on a self-hosted connection, and not on Cloud", () => {
    expect(isRecommendedProvider(findSpec("deepgram-stt"), "self_hosted")).toBe(true);
    expect(isRecommendedProvider(findSpec("cartesia-tts"), "self_hosted")).toBe(true);
    expect(isRecommendedProvider(findSpec("deepgram-stt"), "cloud")).toBe(false);
  });

  it("is dated, so a future refresh is visible in review", () => {
    expect(RECOMMENDED_STACK.asOf).toBe("2026-09-28");
    expect(INFERENCE_CREDITS_LINE.asOf).toBe("2026-09-28");
  });
});

describe("newProviderRef pre-selects FieldSpec.recommended for a *new* ref only (ask #16, U-V6-2)", () => {
  it("turns on openai-stt's use_realtime for a freshly picked provider", () => {
    const ref = newProviderRef(findSpec("openai-stt"));
    expect(ref.fields?.use_realtime).toBe(true);
  });

  it("turns on rime-tts's use_websocket for a freshly picked provider", () => {
    const ref = newProviderRef(findSpec("rime-tts"));
    expect(ref.fields?.use_websocket).toBe(true);
  });

  it("picks elevenlabs-tts's recommended encoding (pcm_24000), and minimax-tts's recommended audio_format (pcm)", () => {
    expect(newProviderRef(findSpec("elevenlabs-tts")).fields?.encoding).toBe("pcm_24000");
    expect(newProviderRef(findSpec("minimax-tts")).fields?.audio_format).toBe("pcm");
  });

  it("falls back to the plain default for a field with no recommended value", () => {
    const ref = newProviderRef(findSpec("deepgram-stt"));
    // deepgram-stt has no `recommended`-carrying fields; the fallback path
    // (recommendedFieldValues === defaultFieldValues for such fields) must
    // not throw and must still produce a fields object.
    expect(ref.fields).toBeDefined();
  });
});

describe("ProviderSlotEditor never rewrites a stored reference's fields (the compatibility rule)", () => {
  it("renders a stored openai-stt ref with fields: {} without ever calling onChange", async () => {
    const stored: ProviderRef = { provider_id: "openai-stt", credential_id: null, model: null, fields: {} };
    const onChange = vi.fn();
    withClient(
      <ProviderSlotEditor kind="stt" value={stored} onChange={onChange} providers={REGISTRY} idPrefix="t-stt" />,
    );
    // Let effects and the providers query settle; a backfill bug would call onChange here.
    await screen.findByRole("radiogroup", { name: "Vendor" });
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("ProviderSlotEditor renders the streaming/not-for-live-calls/Recommended chips (V6-03)", () => {
  it("badges LiveKit Inference's run choice Recommended on a Cloud connection", () => {
    withClient(
      <ProviderSlotEditor
        kind="stt"
        value={null}
        onChange={vi.fn()}
        providers={REGISTRY}
        idPrefix="t-stt-cloud"
        constraints={{ connection: { deployment_type: "cloud" } }}
      />,
    );
    const runGroup = screen.getByText("LiveKit Inference").closest("label") as HTMLElement;
    expect(within(runGroup).getByText("Recommended")).toBeTruthy();
  });

  it("does not badge LiveKit Inference Recommended on a self-hosted connection", () => {
    withClient(
      <ProviderSlotEditor
        kind="stt"
        value={null}
        onChange={vi.fn()}
        providers={REGISTRY}
        idPrefix="t-stt-self"
        constraints={{ connection: { deployment_type: "self_hosted" } }}
      />,
    );
    const runGroup = screen.getByText("LiveKit Inference").closest("label") as HTMLElement;
    expect(within(runGroup).queryByText("Recommended")).toBeNull();
  });

  it("does not badge LiveKit Inference Recommended with no connection in view (the default constraints)", () => {
    withClient(<ProviderSlotEditor kind="stt" value={null} onChange={vi.fn()} providers={REGISTRY} idPrefix="t-stt-none" />);
    const runGroup = screen.getByText("LiveKit Inference").closest("label") as HTMLElement;
    expect(within(runGroup).queryByText("Recommended")).toBeNull();
  });

  it("names ElevenLabs Flash / Deepgram Aura-2 as tts alternatives on a self-hosted connection (D-V6-3)", () => {
    withClient(
      <ProviderSlotEditor
        kind="tts"
        value={null}
        onChange={vi.fn()}
        providers={REGISTRY}
        idPrefix="t-tts-self"
        constraints={{ connection: { deployment_type: "self_hosted" } }}
      />,
    );
    fireEvent.click(screen.getByRole("radio", { name: /your own key/i }));
    expect(screen.getByText(/ElevenLabs Flash or Deepgram Aura-2/)).toBeTruthy();
  });

  it("does not show the tts alternatives line for an stt slot, or on a Cloud connection", () => {
    withClient(
      <ProviderSlotEditor
        kind="stt"
        value={null}
        onChange={vi.fn()}
        providers={REGISTRY}
        idPrefix="t-stt-no-alt"
        constraints={{ connection: { deployment_type: "self_hosted" } }}
      />,
    );
    fireEvent.click(screen.getByRole("radio", { name: /your own key/i }));
    expect(screen.queryByText(/ElevenLabs Flash or Deepgram Aura-2/)).toBeNull();
  });

  it("shows Deepgram as Recommended on a self-hosted connection's stt vendor list", () => {
    withClient(
      <ProviderSlotEditor
        kind="stt"
        value={null}
        onChange={vi.fn()}
        providers={REGISTRY}
        idPrefix="t-stt-vendor"
        constraints={{ connection: { deployment_type: "self_hosted" } }}
      />,
    );
    // The vendor list only shows once "Your own key" is chosen (an empty
    // slot starts with neither run choice picked).
    fireEvent.click(screen.getByRole("radio", { name: /your own key/i }));
    const deepgramCard = document.querySelector('[data-provider-id="deepgram-stt"]') as HTMLElement;
    expect(deepgramCard).toBeTruthy();
    expect(within(deepgramCard).getByText("Recommended")).toBeTruthy();
  });

  it("shows both the streaming chip and 'Not for live calls' on OpenRouter's STT vendor card", () => {
    // V6-32: OpenRouter's STT is unlisted, so only a slot that already uses it shows its card.
    const stored = { provider_id: "openrouter-stt", credential_id: null, model: null, fields: {} };
    withClient(<ProviderSlotEditor kind="stt" value={stored} onChange={vi.fn()} providers={REGISTRY} idPrefix="t-stt-or" />);
    const card = document.querySelector('[data-provider-id="openrouter-stt"]') as HTMLElement;
    expect(card).toBeTruthy();
    expect(within(card).getByText("Waits for the whole sentence")).toBeTruthy();
    expect(within(card).getByText("Not for live calls")).toBeTruthy();
  });

  it("no longer offers OpenRouter's STT to an empty slot (V6-32)", () => {
    withClient(<ProviderSlotEditor kind="stt" value={null} onChange={vi.fn()} providers={REGISTRY} idPrefix="t-stt-or-new" />);
    fireEvent.click(screen.getByRole("radio", { name: /your own key/i }));
    expect(document.querySelector('[data-provider-id="openrouter-stt"]')).toBeNull();
    expect(document.querySelector('[data-provider-id="deepgram-stt"]')).toBeTruthy();
  });

  it("reflects the stored fields on the selected vendor card, not just the entry's default (a streaming_field turned on)", () => {
    // A synthetic mvp entry: the real registry's two streaming_field
    // carriers (openai-stt, rime-tts) are `full`/`deferred`, which the
    // vendor list groups under the collapsed "More providers" section
    // (plain text, no chips) rather than a `VendorCard` — this fixture
    // isolates the fields-aware chip from that unrelated gate.
    const withRealtimeField: ProviderSpec = {
      ...findSpec("openai-stt"),
      id: "test-stt-realtime-field",
      status: "mvp",
      availability: "available",
      worker_image: "slim",
    };
    const registry = [...REGISTRY, withRealtimeField];
    const stored: ProviderRef = { provider_id: withRealtimeField.id, credential_id: null, model: null, fields: { use_realtime: true } };
    withClient(<ProviderSlotEditor kind="stt" value={stored} onChange={vi.fn()} providers={registry} idPrefix="t-stt-selected" />);
    const card = document.querySelector(`[data-provider-id="${withRealtimeField.id}"]`) as HTMLElement;
    expect(card).toBeTruthy();
    expect(within(card).getByText("Streams")).toBeTruthy();
  });

  it("renders the streaming-field option (openai-stt's use_realtime) with no 'WS'/'PCM'/'SSE' jargon on screen (§0.1)", () => {
    const stored = { provider_id: "openai-stt", credential_id: null, model: null, fields: {} };
    const { container } = withClient(
      <ProviderSlotEditor kind="stt" value={stored} onChange={vi.fn()} providers={REGISTRY} idPrefix="t-stt-jargon" />,
    );
    expect(container.textContent).not.toMatch(/\bWS\b|\bPCM\b|\bSSE\b/);
  });
});
