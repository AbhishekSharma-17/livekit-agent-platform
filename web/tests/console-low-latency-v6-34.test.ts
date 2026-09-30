import { describe, expect, it } from "vitest";

import {
  sttEndOfTurn,
  sttEndsTurns,
  sttTurnsOptedIn,
  turnEnderSentence,
  whoEndsTurn,
  type PipelineLike,
} from "@/components/console/agents/providers-section/pipeline-summary";
import { CONVERSATION_PRESET_VALUES, turnDetectorSettingsSchema } from "@/components/console/lib/schemas";
import { modelBlurb } from "@/components/console/registry/model-blurb";
import { isFastForVoice } from "@/components/console/registry/reasoning";

import { REGISTRY, spec } from "./provider-models";

/**
 * V6-34 in the console: who ends the caller's turn mirrors `transcriber_ends_turns`
 * (LiveKit Inference Flux only when the agent opts in with the Fast preset or the
 * turn detector set to speech-to-text), the Fast preset and the `stt` mode parse,
 * the model line says a Flux model can end turns, and Gemma 4 31B is fast for voice.
 */

const INFERENCE_FLUX = { provider_id: "livekit-inference-stt", model: "deepgram/flux-general-en", fields: {} };
const INFERENCE_NOVA = { provider_id: "livekit-inference-stt", model: "deepgram/nova-3", fields: {} };
const DIRECT_FLUX = { provider_id: "deepgram-flux-stt", model: null, fields: {} };

function cascaded(extra: Partial<PipelineLike>): PipelineLike {
  return { mode: "cascaded", llm: { provider_id: "livekit-inference-llm" }, tts: { provider_id: "livekit-inference-tts" }, ...extra };
}

describe("who ends the turn (V6-34)", () => {
  it("records which transcribers can end turns, by entry or by model", () => {
    expect(sttEndOfTurn(DIRECT_FLUX, REGISTRY)).toBe("entry");
    expect(sttEndOfTurn(INFERENCE_FLUX, REGISTRY)).toBe("model");
    expect(sttEndOfTurn({ ...INFERENCE_FLUX, model: "deepgram/flux-general-multi" }, REGISTRY)).toBe("model");
    expect(sttEndOfTurn(INFERENCE_NOVA, REGISTRY)).toBeNull();
    expect(sttEndOfTurn(null, REGISTRY)).toBeNull();
  });

  it("lets Inference Flux end turns only when the agent opts in", () => {
    expect(sttEndsTurns(INFERENCE_FLUX, REGISTRY)).toBe(false);
    expect(sttEndsTurns(INFERENCE_FLUX, REGISTRY, true)).toBe(true);
    expect(sttEndsTurns(INFERENCE_NOVA, REGISTRY, true)).toBe(false);
    expect(sttEndsTurns(DIRECT_FLUX, REGISTRY)).toBe(true);
  });

  it("counts the Fast preset and the speech-to-text mode as opting in", () => {
    expect(sttTurnsOptedIn({ conversation_preset: "fast" })).toBe(true);
    expect(sttTurnsOptedIn({ turn_detector: { mode: "stt" } })).toBe(true);
    expect(sttTurnsOptedIn({ conversation_preset: "snappy", turn_detector: { mode: "hosted" } })).toBe(false);
  });

  it("names the listener as the turn ender for Inference Flux on the Fast preset, the detector otherwise", () => {
    const fast = whoEndsTurn({ pipeline: cascaded({ stt: INFERENCE_FLUX, conversation_preset: "fast" }), providers: REGISTRY });
    const balanced = whoEndsTurn({
      pipeline: cascaded({ stt: INFERENCE_FLUX, conversation_preset: "balanced" }),
      providers: REGISTRY,
    });

    expect(fast?.kind).toBe("listener");
    expect(turnEnderSentence(fast!)).toContain("as part of listening");
    expect(balanced?.kind).toBe("livekit");
  });
});

describe("the Fast preset and the speech-to-text mode parse (V6-34)", () => {
  it("offers fast next to the other presets", () => {
    expect(CONVERSATION_PRESET_VALUES).toContain("fast");
    expect(CONVERSATION_PRESET_VALUES).toContain("balanced");
  });

  it("accepts the stt turn-detector mode", () => {
    expect(turnDetectorSettingsSchema.parse({ mode: "stt" }).mode).toBe("stt");
    expect(() => turnDetectorSettingsSchema.parse({ mode: "elsewhere" })).toThrow();
  });
});

describe("model lines (V6-34)", () => {
  it("says an Inference Flux model can decide the end of turn with the Fast preset", () => {
    const stt = spec("livekit-inference-stt");
    const models = stt.models ?? [];

    expect(modelBlurb({ provider: stt, models, modelId: "deepgram/flux-general-en", fields: {} })).toContain(
      "can decide end of turn itself (Fast preset)",
    );
    expect(modelBlurb({ provider: stt, models, modelId: "deepgram/nova-3", fields: {} })).not.toContain("end of turn");
  });

  it("marks Gemma 4 31B and the small GPT-4.1 models fast for voice", () => {
    const unknown = { reasoning: null, efforts: null, parameters: null };
    const notReasoning = { reasoning: false, efforts: [], parameters: null };

    expect(isFastForVoice(unknown, "google/gemma-4-31b-it")).toBe(true);
    expect(isFastForVoice({ reasoning: true, efforts: ["high"], parameters: null }, "google/gemma-4-31b-it")).toBe(false);
    expect(isFastForVoice(notReasoning, "openai/gpt-4.1-mini")).toBe(true);
    expect(isFastForVoice(notReasoning, "openai/gpt-4.1-nano")).toBe(true);
  });

  it("lists Inworld TTS 2 Flash on LiveKit Inference", () => {
    expect((spec("livekit-inference-tts").models ?? []).map((m) => m.id)).toContain("inworld/inworld-tts-2-flash");
  });
});
