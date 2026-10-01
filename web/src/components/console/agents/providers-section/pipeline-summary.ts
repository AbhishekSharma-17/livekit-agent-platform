import { findModel, isInferenceProvider } from "@/components/console/registry/provider-meta";
import { slotModelId } from "@/components/console/registry/provider-slot-editor";
import { effortToSend, REASONING_EFFORT_FIELD, type ReasoningView } from "@/components/console/registry/reasoning";
import type { ConnectionCapabilities, ProviderRef, ProviderSpec } from "@/contracts/lkap-contracts";

/**
 * The one-line "who does what" summary of an agent's pipeline, and the
 * plain-words notes on how Cloud and self-hosted connections differ (V6-33).
 *
 * Everything is derived on the client from the agent's configuration, the
 * provider list and the connection's capabilities, with the same precedence the
 * worker uses to pick who ends the caller's turn
 * (`agent/src/lkap_agent/session_builder.py`, `SessionBuilder.build` and
 * `stt_decides_turns`):
 *
 *   1. a realtime-only agent: the model decides (there is no client-side turn detector);
 *   2. an explicit turn-detection part, if one is configured;
 *   3. else the transcriber, when its provider declares `capabilities.end_of_turn`,
 *      or its model does (`ModelSpec.end_of_turn`, LiveKit Inference Flux) and the
 *      agent opted in with the Fast preset or the turn detector set to
 *      speech-to-text (V6-34, `transcriber_ends_turns`) — a cascaded agent only:
 *      a half-cascade agent has no transcriber;
 *   4. else the LiveKit turn detector: hosted by LiveKit when the connection's
 *      `turn_detector_mode` is `hosted`, else inside the agent's worker.
 *
 * Half-cascade caveat: the worker also drops the detector for a realtime model
 * that keeps its own server-side turns (a runtime property of the model object,
 * not something the provider list records). The summary states step 2 or 4 there.
 */

/** A provider reference as the form and the saved config both carry it. */
export interface PartRef {
  provider_id?: string | null;
  model?: string | null;
  fields?: Record<string, unknown> | null;
}

/** The parts of `PipelineConfig` the summary reads. */
export interface PipelineLike {
  mode?: "realtime" | "cascaded" | "half_cascade" | null;
  stt?: PartRef | null;
  llm?: PartRef | null;
  tts?: PartRef | null;
  realtime?: PartRef | null;
  avatar?: PartRef | null;
  turn_detection?: PartRef | null;
  turn_detector?: { mode?: string | null } | null;
  conversation_preset?: string | null;
}

/** What the summary needs to know about the agent's connection. */
export interface ConnectionFacts {
  name: string;
  deployment_type?: "cloud" | "self_hosted";
  capabilities?: ConnectionCapabilities;
}

/** Where a turn detector runs: on LiveKit's side, inside the worker, or not known (no connection in view). */
export type TurnPlace = "hosted" | "worker" | "unknown";

/** Who decides that the caller has finished speaking. */
export type TurnEnder =
  | { kind: "model"; name: string }
  | { kind: "listener"; name: string }
  | { kind: "part"; name: string; place: TurnPlace }
  | { kind: "livekit"; place: TurnPlace };

export interface PipelineSummaryInput {
  pipeline: PipelineLike | null | undefined;
  providers: readonly ProviderSpec[];
  connection?: ConnectionFacts | null;
  /** What is known about the language model's reasoning (the catalog query decides); omitted when unknown. */
  llmReasoning?: ReasoningView | null;
  /** The language model's name from the vendor's live list, when the registry has none. */
  llmModelLabel?: string | null;
}

export interface PipelineSummary {
  /** The parts of the sentence, in the order a turn flows. */
  segments: string[];
  /** The segments joined for display: "A listens · B thinks · C speaks". */
  text: string;
  /** Who ends the caller's turn; `null` when nothing is chosen yet. */
  turnEnder: TurnEnder | null;
}

function specOf(ref: PartRef | null | undefined, providers: readonly ProviderSpec[]): ProviderSpec | undefined {
  return ref?.provider_id ? providers.find((p) => p.id === ref.provider_id) : undefined;
}

/** "gpt-6-luna" out of "openai/gpt-6-luna": the last path segment of a model id. */
function shortModelId(id: string): string {
  const tail = id.split("/").filter(Boolean).pop() ?? id;
  return tail.split(":")[0] || tail;
}

/** The model's own name: the provider list's label, else the vendor list's, else the id without its vendor prefix. */
function modelName(spec: ProviderSpec, ref: PartRef, liveLabel?: string | null): string | null {
  const stored: ProviderRef = {
    provider_id: spec.id,
    credential_id: null,
    model: ref.model ?? null,
    fields: (ref.fields ?? {}) as ProviderRef["fields"],
  };
  const id = slotModelId(spec, stored);
  if (!id) return null;
  return findModel(spec, id)?.label ?? liveLabel ?? shortModelId(id);
}

/**
 * How a part is named in the sentence. A gateway that serves many vendors'
 * models (LiveKit Inference, OpenRouter) is named by the model; a vendor's own
 * entry is named by the entry ("Deepgram Flux"), since its models are versions.
 */
function partName(
  ref: PartRef | null | undefined,
  providers: readonly ProviderSpec[],
  { byModel, liveLabel }: { byModel: boolean; liveLabel?: string | null },
): string | null {
  if (!ref?.provider_id) return null;
  const spec = specOf(ref, providers);
  if (!spec) return ref.provider_id;
  const gateway = isInferenceProvider(spec) || spec.vendor === "OpenRouter";
  if (byModel || gateway) return modelName(spec, ref, liveLabel) ?? spec.label;
  return spec.label;
}

/**
 * Whether the agent asked for a model's own end of turn (mirrors `stt_turns_opted_in`):
 * the Fast preset, or the turn detector set to speech-to-text.
 */
export function sttTurnsOptedIn(pipeline: PipelineLike | null | undefined): boolean {
  return pipeline?.conversation_preset === "fast" || pipeline?.turn_detector?.mode === "stt";
}

/**
 * Whether the transcriber can end turns (mirrors `stt_end_of_turn`): `"entry"` when
 * the provider always does (`capabilities.end_of_turn`), `"model"` when this model
 * can once the agent opts in (`ModelSpec.end_of_turn`), else `null`.
 */
export function sttEndOfTurn(
  stt: PartRef | null | undefined,
  providers: readonly ProviderSpec[],
): "entry" | "model" | null {
  const spec = specOf(stt, providers);
  if (spec?.kind !== "stt") return null;
  if (spec.capabilities?.end_of_turn === true) return "entry";
  const id = stt?.model || spec.default_model;
  if (!id) return null;
  const bare = id.split(":", 1)[0];
  const model = (spec.models ?? []).find((candidate) => candidate.id === id || candidate.id === bare);
  return model?.end_of_turn === true ? "model" : null;
}

/** Whether the transcriber ends turns itself (mirrors `stt_decides_turns` / `transcriber_ends_turns`). */
export function sttEndsTurns(
  stt: PartRef | null | undefined,
  providers: readonly ProviderSpec[],
  optedIn = false,
): boolean {
  const ability = sttEndOfTurn(stt, providers);
  return ability === "entry" || (ability === "model" && optedIn);
}

/**
 * Where the LiveKit turn detector runs for this agent. A `local` setting on the
 * agent, or a connection without hosted turn detection, keeps it in the worker
 * (`turn_detector_kwargs`); the connection's `turn_detector_mode` is what the
 * worker reads, `inference_available` only stands in when that is not reported.
 */
export function livekitDetectorPlace(
  connection: ConnectionFacts | null | undefined,
  settings?: { mode?: string | null } | null,
): TurnPlace {
  if (settings?.mode === "local") return "worker";
  const caps = connection?.capabilities;
  if (!connection || !caps) return "unknown";
  if (caps.turn_detector_mode === "hosted") return "hosted";
  if (caps.turn_detector_mode === "local") return "worker";
  if (caps.inference_available === true) return "hosted";
  if (caps.inference_available === false) return "worker";
  return "unknown";
}

function placeOfPart(spec: ProviderSpec | undefined): TurnPlace {
  if (!spec) return "unknown";
  return spec.capabilities?.cloud_only ? "hosted" : "worker";
}

/** Who ends the caller's turn, by the worker's precedence (see the module comment). */
export function whoEndsTurn({ pipeline, providers, connection }: PipelineSummaryInput): TurnEnder | null {
  if (!pipeline) return null;
  const mode = pipeline.mode ?? "cascaded";
  if (mode === "realtime") {
    const name = partName(pipeline.realtime, providers, { byModel: true });
    return { kind: "model", name: name ?? "The model" };
  }
  const explicit = specOf(pipeline.turn_detection, providers);
  if (pipeline.turn_detection?.provider_id) {
    return {
      kind: "part",
      // LiveKit's two turn-detector entries are one model in two places; the place says which.
      name: explicit?.vendor === "LiveKit" ? "LiveKit's turn detector" : (explicit?.label ?? pipeline.turn_detection.provider_id),
      place: placeOfPart(explicit),
    };
  }
  if (mode === "cascaded" && sttEndsTurns(pipeline.stt, providers, sttTurnsOptedIn(pipeline))) {
    return { kind: "listener", name: partName(pipeline.stt, providers, { byModel: false }) ?? "The listener" };
  }
  return { kind: "livekit", place: livekitDetectorPlace(connection, pipeline.turn_detector) };
}

function placePhrase(place: TurnPlace): string {
  switch (place) {
    case "hosted":
      return "hosted by LiveKit";
    case "worker":
      return "runs inside the agent's worker";
    default:
      return "";
  }
}

/** The turn ender as a full sentence, for a part's help text. */
export function turnEnderSentence(ender: TurnEnder): string {
  switch (ender.kind) {
    case "model":
      return `${ender.name} decides when the caller has finished, on its own.`;
    case "listener":
      return `${ender.name} decides when the caller has finished, as part of listening.`;
    case "part": {
      const place = placePhrase(ender.place);
      return `${ender.name} decides when the caller has finished${place ? ` (${place})` : ""}.`;
    }
    default: {
      const place = placePhrase(ender.place);
      return `LiveKit's turn detector decides when the caller has finished${place ? ` (${place})` : ""}.`;
    }
  }
}

/** The reasoning clause for the language model: "reasoning: automatic, lowest", or `null` when it does not reason or is unknown. */
export function reasoningClause(view: ReasoningView | null | undefined, ref: PartRef | null | undefined): string | null {
  if (!view || view.reasoning !== true) return null;
  const stored = ref?.fields?.[REASONING_EFFORT_FIELD];
  const wanted = typeof stored === "string" && stored.trim() ? stored.trim() : null;
  if (wanted === null) return "reasoning: automatic, lowest";
  return `reasoning: ${effortToSend(view, wanted) ?? wanted}`;
}

/** "runs on DGX (self-hosted)" / "runs on Acme (LiveKit Cloud)". */
function runsOn(connection: ConnectionFacts | null | undefined): string | null {
  if (!connection?.name) return null;
  return `runs on ${connection.name} (${connection.deployment_type === "self_hosted" ? "self-hosted" : "LiveKit Cloud"})`;
}

/**
 * The one-line pipeline summary: which part listens, who decides that the
 * caller has finished, what thinks (with its reasoning), what speaks, and where
 * the agent runs. Parts that are not chosen yet are left out.
 */
export function pipelineSummary(input: PipelineSummaryInput): PipelineSummary {
  const { pipeline, providers, connection, llmReasoning, llmModelLabel } = input;
  const segments: string[] = [];
  const turnEnder = whoEndsTurn(input);
  if (!pipeline) return { segments, text: "", turnEnder };
  const mode = pipeline.mode ?? "cascaded";

  const turnSegment = (): string | null => {
    if (!turnEnder) return null;
    switch (turnEnder.kind) {
      case "part": {
        const place = placePhrase(turnEnder.place);
        return `${turnEnder.name}${place ? ` (${place})` : ""} decides when you've finished`;
      }
      case "livekit": {
        const place = placePhrase(turnEnder.place);
        return `LiveKit's turn detector${place ? ` (${place})` : ""} decides when you've finished`;
      }
      default:
        return null;
    }
  };

  if (mode === "realtime") {
    const model = partName(pipeline.realtime, providers, { byModel: true });
    if (model) segments.push(`${model} listens, thinks and speaks, and decides when you've finished`);
  } else {
    if (mode === "cascaded") {
      const stt = partName(pipeline.stt, providers, { byModel: false });
      if (stt) {
        segments.push(turnEnder?.kind === "listener" ? `${stt} listens and decides when you've finished` : `${stt} listens`);
      }
    }
    const turn = turnSegment();
    if (turn) segments.push(turn);
    if (mode === "half_cascade") {
      const model = partName(pipeline.realtime, providers, { byModel: true });
      if (model) segments.push(`${model} listens and thinks`);
    } else {
      const llm = partName(pipeline.llm, providers, { byModel: true, liveLabel: llmModelLabel });
      if (llm) {
        const reasoning = reasoningClause(llmReasoning, pipeline.llm);
        segments.push(`${llm} thinks${reasoning ? ` (${reasoning})` : ""}`);
      }
    }
    const tts = partName(pipeline.tts, providers, { byModel: false });
    if (tts) segments.push(`${tts} speaks`);
  }
  const face = partName(pipeline.avatar, providers, { byModel: false });
  if (face) segments.push(`${face} gives it a face`);
  const where = runsOn(connection);
  if (where) segments.push(where);
  return { segments, text: segments.join(" · "), turnEnder };
}

/* -------------------------------------------------------------------------- */
/* Cloud vs self-hosted                                                       */
/* -------------------------------------------------------------------------- */

export interface ConnectionNote {
  id: "inference" | "turn_detector" | "noise" | "phone";
  /** The thing the note is about, for the bold lead-in. */
  label: string;
  text: string;
  /** This connection lacks what LiveKit Cloud offers here. */
  limited: boolean;
}

/**
 * Short notes on where a connection differs from LiveKit Cloud, from the
 * capabilities the connection already exposes. `[]` with no connection in view.
 */
export function connectionNotes(connection: ConnectionFacts | null | undefined): ConnectionNote[] {
  const caps = connection?.capabilities;
  if (!connection || !caps) return [];
  const notes: ConnectionNote[] = [];

  const inference = caps.inference_available === true;
  notes.push({
    id: "inference",
    label: "LiveKit Inference",
    limited: !inference,
    text: inference
      ? "Available. Listening, thinking and speaking can run with no provider key."
      : "Cloud only. Here each part needs its own provider key.",
  });

  const hosted = livekitDetectorPlace(connection) === "hosted";
  notes.push({
    id: "turn_detector",
    label: "Turn detector",
    limited: !hosted,
    text: hosted
      ? "Hosted by LiveKit."
      : "LiveKit hosts it on Cloud only. Here it runs inside the agent's worker.",
  });

  const nc = caps.noise_cancellation_tier ?? "none";
  notes.push({
    id: "noise",
    label: "Noise cancellation",
    limited: nc !== "krisp",
    text:
      nc === "krisp"
        ? "LiveKit's noise filter is available."
        : nc === "ai_coustics"
          ? "LiveKit's own noise filter is Cloud only. ai-coustics works here."
          : "LiveKit's own noise filter is Cloud only.",
  });

  const phone = caps.sip_enabled === true;
  notes.push({
    id: "phone",
    label: "Phone",
    limited: !phone,
    text: phone
      ? "This connection's phone service is on."
      : "Phone calls use LiveKit Cloud telephony, which this connection doesn't have.",
  });
  return notes;
}

/** The note for the avatar card: avatar services join the room from the internet. `null` when the connection is not self-hosted. */
export function avatarConnectionNote(connection: ConnectionFacts | null | undefined): string | null {
  return connection?.deployment_type === "self_hosted"
    ? "An avatar needs a room that is reachable from the internet, because the avatar service joins it."
    : null;
}

/** The note for the noise-cancellation card: LiveKit's filter needs Cloud. `null` when the connection has it or is unknown. */
export function noiseConnectionNote(connection: ConnectionFacts | null | undefined): string | null {
  const tier = connection?.capabilities?.noise_cancellation_tier;
  if (!connection || tier === undefined || tier === "krisp") return null;
  return "LiveKit's own noise filter works on LiveKit Cloud only. The choices here that need it are marked.";
}
