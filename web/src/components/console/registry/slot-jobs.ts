/**
 * What each part of an agent's pipeline does, in plain words (V6-33).
 *
 * The console used to name the parts by their technical names ("STT", "LLM",
 * "Turn detection"). A builder configuring or testing an agent needs the job
 * first, so every part card leads with it and keeps the technical name as small
 * secondary text. One map, used by the agent editor's providers section, its
 * legacy tab and the summary rail, so the wording cannot drift.
 */

export type PartKey =
  | "stt"
  | "llm"
  | "tts"
  | "realtime"
  | "avatar"
  | "image_gen"
  | "workflow_llm"
  | "vad"
  | "turn_detection"
  | "noise_cancellation";

export interface PartJob {
  /** The job, verb first: "Listens: turns speech into text". */
  job: string;
  /** Just the verb, for tight places ("Listens"). */
  verb: string;
  /** The technical name, shown small under the job. */
  name: string;
}

export const PART_JOBS: Record<PartKey, PartJob> = {
  stt: { job: "Listens: turns speech into text", verb: "Listens", name: "Speech-to-text" },
  llm: {
    job: "Thinks: understands, decides, calls tools, writes the reply",
    verb: "Thinks",
    name: "Language model",
  },
  tts: { job: "Speaks: turns the reply into voice", verb: "Speaks", name: "Text-to-speech" },
  realtime: { job: "Listens, thinks and speaks in one model", verb: "Listens, thinks and speaks", name: "Realtime model" },
  avatar: { job: "Gives the agent a face", verb: "Shows a face", name: "Avatar" },
  image_gen: { job: "Draws pictures", verb: "Draws", name: "Image generation" },
  workflow_llm: {
    job: "Background helper: capturing details, summaries",
    verb: "Helps in the background",
    name: "Workflow model",
  },
  vad: { job: "Decides when the caller has finished", verb: "Hears speech", name: "Voice activity detection" },
  turn_detection: { job: "Decides when the caller has finished", verb: "Decides", name: "Turn detection" },
  noise_cancellation: { job: "Filters background noise", verb: "Filters noise", name: "Noise cancellation" },
};
