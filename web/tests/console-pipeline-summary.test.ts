import { describe, expect, it } from "vitest";

import {
  avatarConnectionNote,
  connectionNotes,
  livekitDetectorPlace,
  noiseConnectionNote,
  pipelineSummary,
  reasoningClause,
  sttEndsTurns,
  turnEnderSentence,
  whoEndsTurn,
  type ConnectionFacts,
  type PipelineLike,
} from "@/components/console/agents/providers-section/pipeline-summary";
import { resolveReasoning } from "@/components/console/registry/reasoning";

import { REGISTRY, spec } from "./provider-models";

/**
 * V6-33: the one-line pipeline summary, and who ends the caller's turn.
 * The precedence mirrors `SessionBuilder.build` / `stt_decides_turns` in the worker:
 * an explicit turn-detection part, else a transcriber that ends turns itself
 * (`capabilities.end_of_turn`), else LiveKit's detector (hosted on a connection
 * with hosted turn detection, else inside the worker); a realtime model decides.
 */

const CLOUD: ConnectionFacts = {
  name: "Acme Cloud",
  deployment_type: "cloud",
  capabilities: {
    inference_available: true,
    turn_detector_mode: "hosted",
    noise_cancellation_tier: "krisp",
    cloud_hosting: true,
    sip_enabled: true,
  },
};

const DGX: ConnectionFacts = {
  name: "DGX-LivekitServer",
  deployment_type: "self_hosted",
  capabilities: {
    inference_available: false,
    turn_detector_mode: "local",
    noise_cancellation_tier: "none",
    cloud_hosting: false,
    sip_enabled: false,
  },
};

const FLUX = { provider_id: "deepgram-flux-stt", model: null, fields: {} };
const NOVA = { provider_id: "deepgram-stt", model: null, fields: {} };
const AURA = { provider_id: "deepgram-tts", model: null, fields: {} };
const LUNA = { provider_id: "openrouter-llm", model: "openai/gpt-6-luna", fields: {} };

/** What the model picker's catalog read tells the summary about a model the provider list does not name. */
const luna = {
  reasoning: resolveReasoning({ spec: spec("openrouter-llm"), modelId: "openai/gpt-6-luna" }),
  label: "GPT-6 Luna",
};
const LUNA_REASONS = { reasoning: true as const, efforts: ["none" as const, "low" as const, "medium" as const], parameters: null };

function summarize(pipeline: PipelineLike, connection: ConnectionFacts | null) {
  return pipelineSummary({
    pipeline,
    providers: REGISTRY,
    connection,
    llmReasoning: LUNA_REASONS,
    llmModelLabel: luna.label,
  });
}

describe("pipelineSummary: who ends the turn", () => {
  it("lets Deepgram Flux end turns itself, on Cloud", () => {
    const summary = summarize({ mode: "cascaded", stt: FLUX, llm: LUNA, tts: AURA }, CLOUD);
    expect(summary.turnEnder).toEqual({ kind: "listener", name: "Deepgram Flux" });
    expect(summary.text).toBe(
      "Deepgram Flux listens and decides when you've finished · GPT-6 Luna thinks (reasoning: automatic, lowest) · Deepgram Aura speaks · runs on Acme Cloud (LiveKit Cloud)",
    );
  });

  it("names the same pipeline on the DGX, where Flux still ends the turns", () => {
    const summary = summarize({ mode: "cascaded", stt: FLUX, llm: LUNA, tts: AURA }, DGX);
    expect(summary.text).toBe(
      "Deepgram Flux listens and decides when you've finished · GPT-6 Luna thinks (reasoning: automatic, lowest) · Deepgram Aura speaks · runs on DGX-LivekitServer (self-hosted)",
    );
  });

  it("gives a transcriber that does not end turns (Nova-3) LiveKit's detector, hosted by LiveKit, on Cloud", () => {
    const summary = summarize({ mode: "cascaded", stt: NOVA, llm: LUNA, tts: AURA }, CLOUD);
    expect(summary.turnEnder).toEqual({ kind: "livekit", place: "hosted" });
    expect(summary.text).toContain("Deepgram listens · LiveKit's turn detector (hosted by LiveKit) decides when you've finished");
    expect(summary.text).not.toContain("listens and decides");
  });

  it("runs the same transcriber's detector inside the agent's worker on self-hosted", () => {
    const summary = summarize({ mode: "cascaded", stt: NOVA, llm: LUNA, tts: AURA }, DGX);
    expect(summary.turnEnder).toEqual({ kind: "livekit", place: "worker" });
    expect(summary.text).toContain("LiveKit's turn detector (runs inside the agent's worker) decides when you've finished");
    expect(summary.text).toContain("runs on DGX-LivekitServer (self-hosted)");
  });

  it("lets an explicit turn-detection part win over a transcriber that ends turns", () => {
    const pipeline: PipelineLike = {
      mode: "cascaded",
      stt: FLUX,
      llm: LUNA,
      tts: AURA,
      turn_detection: { provider_id: "turn-detector-plugin", model: null, fields: {} },
    };
    const summary = summarize(pipeline, CLOUD);
    expect(summary.turnEnder).toEqual({ kind: "part", name: "LiveKit's turn detector", place: "worker" });
    expect(summary.text).toContain("Deepgram Flux listens · LiveKit's turn detector (runs inside the agent's worker) decides when you've finished");
    expect(summary.text).not.toContain("listens and decides");
  });

  it("places LiveKit's hosted detector part on LiveKit's side", () => {
    const ender = whoEndsTurn({
      pipeline: { mode: "cascaded", stt: NOVA, turn_detection: { provider_id: "inference-turn-detector" } },
      providers: REGISTRY,
      connection: CLOUD,
    });
    expect(ender).toEqual({ kind: "part", name: "LiveKit's turn detector", place: "hosted" });
  });

  it("says the model decides for a realtime model, whatever else is stored", () => {
    const summary = summarize(
      {
        mode: "realtime",
        realtime: { provider_id: "openai-realtime", model: null, fields: {} },
        stt: FLUX,
        turn_detection: { provider_id: "turn-detector-plugin" },
      },
      CLOUD,
    );
    expect(summary.turnEnder).toEqual({ kind: "model", name: "GPT Realtime" });
    expect(summary.text).toBe("GPT Realtime listens, thinks and speaks, and decides when you've finished · runs on Acme Cloud (LiveKit Cloud)");
  });

  it("leaves the transcriber out of a half-cascade agent, which has none", () => {
    const summary = summarize(
      {
        mode: "half_cascade",
        realtime: { provider_id: "google-realtime", model: null, fields: {} },
        stt: FLUX,
        tts: AURA,
      },
      DGX,
    );
    expect(summary.turnEnder).toEqual({ kind: "livekit", place: "worker" });
    expect(summary.text).toBe(
      "LiveKit's turn detector (runs inside the agent's worker) decides when you've finished · Gemini 3.8 Live listens and thinks · Deepgram Aura speaks · runs on DGX-LivekitServer (self-hosted)",
    );
  });

  it("keeps the detector in the worker when the agent asks for a local one, even on Cloud", () => {
    expect(livekitDetectorPlace(CLOUD, { mode: "local" })).toBe("worker");
    expect(livekitDetectorPlace(CLOUD, { mode: "hosted" })).toBe("hosted");
  });

  it("does not guess where the detector runs when no connection is in view", () => {
    const summary = summarize({ mode: "cascaded", stt: NOVA, llm: LUNA, tts: AURA }, null);
    expect(summary.turnEnder).toEqual({ kind: "livekit", place: "unknown" });
    expect(summary.text).toContain("LiveKit's turn detector decides when you've finished");
    expect(summary.text).not.toContain("hosted by LiveKit");
    expect(summary.text).not.toContain("runs on");
  });

  it("reads inference_available when the connection does not report a detector mode", () => {
    expect(livekitDetectorPlace({ name: "a", capabilities: { inference_available: true } })).toBe("hosted");
    expect(livekitDetectorPlace({ name: "b", capabilities: { inference_available: false } })).toBe("worker");
  });

  it("does not treat a LiveKit Inference Flux model as ending turns (the provider's flag decides, as in the worker)", () => {
    const inferenceStt = { provider_id: "livekit-inference-stt", model: "deepgram/flux-general-en", fields: {} };
    expect(sttEndsTurns(inferenceStt, REGISTRY)).toBe(false);
    expect(sttEndsTurns(FLUX, REGISTRY)).toBe(true);
    expect(sttEndsTurns(null, REGISTRY)).toBe(false);
  });

  it("names a gateway's part by the model and a vendor's own part by the entry", () => {
    const summary = summarize(
      {
        mode: "cascaded",
        stt: { provider_id: "livekit-inference-stt", model: "deepgram/nova-3", fields: {} },
        llm: { provider_id: "livekit-inference-llm", model: "google/gemma-4-31b-it", fields: {} },
        tts: { provider_id: "livekit-inference-tts", model: "inworld/inworld-tts-2", fields: {} },
      },
      CLOUD,
    );
    expect(summary.text).toBe(
      "Deepgram Nova 3 listens · LiveKit's turn detector (hosted by LiveKit) decides when you've finished · Gemma 4 31B Instruct thinks (reasoning: automatic, lowest) · Inworld TTS 2 speaks · runs on Acme Cloud (LiveKit Cloud)",
    );
  });

  it("names a model the provider list does not know by its id without the vendor prefix", () => {
    const summary = pipelineSummary({ pipeline: { mode: "cascaded", llm: LUNA }, providers: REGISTRY });
    expect(summary.text).toBe("LiveKit's turn detector decides when you've finished · gpt-6-luna thinks");
  });

  it("leaves out parts that are not chosen, and says nothing when nothing is", () => {
    expect(pipelineSummary({ pipeline: { mode: "cascaded" }, providers: REGISTRY, connection: null }).text).toBe(
      "LiveKit's turn detector decides when you've finished",
    );
    expect(pipelineSummary({ pipeline: null, providers: REGISTRY }).text).toBe("");
    expect(pipelineSummary({ pipeline: { mode: "realtime" }, providers: REGISTRY }).text).toBe("");
  });

  it("adds the avatar when there is one", () => {
    const summary = summarize(
      { mode: "cascaded", stt: FLUX, llm: LUNA, tts: AURA, avatar: { provider_id: "lemonslice-avatar" } },
      CLOUD,
    );
    expect(summary.segments).toContain("LemonSlice gives it a face");
  });
});

describe("reasoningClause", () => {
  it("says automatic, lowest when no effort is stored on a model that reasons", () => {
    expect(reasoningClause(LUNA_REASONS, LUNA)).toBe("reasoning: automatic, lowest");
  });

  it("names a stored effort, moved up to one the model offers", () => {
    expect(reasoningClause(LUNA_REASONS, { ...LUNA, fields: { reasoning_effort: "low" } })).toBe("reasoning: low");
    expect(reasoningClause(LUNA_REASONS, { ...LUNA, fields: { reasoning_effort: "high" } })).toBe("reasoning: medium");
  });

  it("says nothing for a model that does not reason or one not known yet", () => {
    expect(reasoningClause({ reasoning: false, efforts: null, parameters: null }, LUNA)).toBeNull();
    expect(reasoningClause({ reasoning: null, efforts: null, parameters: null }, LUNA)).toBeNull();
    expect(reasoningClause(null, LUNA)).toBeNull();
  });
});

describe("turnEnderSentence", () => {
  it("reads as a sentence for each kind", () => {
    expect(turnEnderSentence({ kind: "model", name: "GPT Realtime" })).toBe(
      "GPT Realtime decides when the caller has finished, on its own.",
    );
    expect(turnEnderSentence({ kind: "listener", name: "Deepgram Flux" })).toBe(
      "Deepgram Flux decides when the caller has finished, as part of listening.",
    );
    expect(turnEnderSentence({ kind: "livekit", place: "hosted" })).toBe(
      "LiveKit's turn detector decides when the caller has finished (hosted by LiveKit).",
    );
    expect(turnEnderSentence({ kind: "livekit", place: "unknown" })).toBe(
      "LiveKit's turn detector decides when the caller has finished.",
    );
  });
});

describe("connection notes", () => {
  it("marks nothing as missing on a full Cloud connection", () => {
    const notes = connectionNotes(CLOUD);
    expect(notes.map((note) => note.id)).toEqual(["inference", "turn_detector", "noise", "phone"]);
    expect(notes.every((note) => !note.limited)).toBe(true);
  });

  it("says what a self-hosted connection lacks, in plain words", () => {
    const byId = Object.fromEntries(connectionNotes(DGX).map((note) => [note.id, note]));
    expect(byId.inference.text).toBe("Cloud only. Here each part needs its own provider key.");
    expect(byId.turn_detector.text).toBe("LiveKit hosts it on Cloud only; here it runs inside the agent's worker.");
    expect(byId.noise.text).toBe("LiveKit's own noise filter is Cloud only.");
    expect(byId.phone.text).toBe("Phone calls use LiveKit Cloud telephony, which this connection doesn't have.");
    expect(Object.values(byId).every((note) => note.limited)).toBe(true);
  });

  it("gives no notes without a connection or its capabilities", () => {
    expect(connectionNotes(null)).toEqual([]);
    expect(connectionNotes({ name: "x" })).toEqual([]);
  });

  it("puts the avatar and noise-filter notes only where they apply", () => {
    expect(avatarConnectionNote(DGX)).toMatch(/reachable from the internet/);
    expect(avatarConnectionNote(CLOUD)).toBeNull();
    expect(noiseConnectionNote(DGX)).toMatch(/LiveKit Cloud only/);
    expect(noiseConnectionNote(CLOUD)).toBeNull();
    expect(noiseConnectionNote(undefined)).toBeNull();
  });

  it("uses no internal words in any note", () => {
    const all = [...connectionNotes(DGX), ...connectionNotes(CLOUD)].map((note) => `${note.label} ${note.text}`).join(" ");
    expect(all).not.toMatch(/slot|registry|R-V6/i);
  });
});
