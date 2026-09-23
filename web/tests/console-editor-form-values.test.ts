import { describe, expect, it } from "vitest";

import { buildAgentUpdate, toFormValues, unappliedFields } from "@/components/console/agents/editor/form-values";
import { agentEditorFormSchema } from "@/components/console/lib/schemas";
import type { AgentOut } from "@/contracts/lkap-contracts";

const ref = (provider_id: string) => ({ provider_id, credential_id: null, model: null, fields: {} });

/** A v2 agent as the live api returns it, including fields the editor does not show. */
function agent(overrides: Partial<AgentOut> = {}): AgentOut {
  return {
    id: "a-1",
    slug: "claims",
    name: "Claims",
    description: "",
    pack_id: "insurance_claim",
    ui_panel_id: "insurance_notebook",
    published: false,
    config_version: 3,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-20T00:00:00Z",
    mode: "prompt",
    connection_id: null,
    limits: { max_concurrent_sessions: 5, max_session_duration_s: 1800, rate_per_ip_per_min: 6, rate_per_agent_per_min: 60 },
    allowed_origins: [],
    config: {
      v: 2,
      instructions: "Be helpful.",
      pipeline: {
        mode: "cascaded",
        stt: ref("livekit-inference-stt"),
        llm: ref("livekit-inference-llm"),
        tts: ref("livekit-inference-tts"),
        realtime: ref("google-realtime"),
        vad: ref("silero-vad"),
        noise_cancellation: null,
        avatar_options: { participant_name: "Avatar", video_quality: "high" },
        turn_handling: {},
      },
      voice: { greeting: "Hi", greeting_mode: "say", language: "en", allow_interruptions: true, first_speaker: "user" },
      capabilities: { camera: true, screen_share: false, chat_input: true, vision_inject_per_turn: true, dtmf: true },
      tools: { builtin_disabled: [], http_request_enabled: false, tool_ids: [], max_tool_steps: 3 },
      knowledge: { kb_ids: [], auto_inject: true, top_k: 4 },
      panel: { panel_id: "insurance_notebook", layout: "wide", blocks: [] },
      recording: { enabled: false, audio_only: true, storage_config_id: null, retention_days: null },
      qa: { enabled: true, rubric_prompt: "Score empathy", model: null },
      flow: null,
      pack_settings: { region: "eu" },
      timezone: "Europe/Berlin",
    },
    ...overrides,
  };
}

/** What `handleSubmit` hands to `onValid`: the zod-parsed form (unknown keys stripped). */
function submitted(a: AgentOut, edit?: (values: ReturnType<typeof toFormValues>) => void) {
  const values = toFormValues(a);
  edit?.(values);
  const parsed = agentEditorFormSchema.safeParse(values);
  if (!parsed.success) throw new Error(JSON.stringify(parsed.error.issues));
  return parsed.data;
}

describe("toFormValues", () => {
  it("fills v2 defaults for older agents", () => {
    const values = toFormValues(agent({ limits: undefined, allowed_origins: undefined, mode: undefined }));
    expect(values.mode).toBe("prompt");
    expect(values.limits.max_session_duration_s).toBe(1800);
    expect(values.allowed_origins).toEqual([]);
    expect(values.config.recording).toEqual({ enabled: false, audio_only: true, storage_config_id: null, retention_days: null });
  });

  it("round-trips through the schema", () => {
    expect(agentEditorFormSchema.safeParse(toFormValues(agent())).success).toBe(true);
  });
});

describe("buildAgentUpdate", () => {
  it("posts AgentConfig v2 and keeps every field the editor does not show", () => {
    const a = agent();
    const body = buildAgentUpdate(a, submitted(a, (v) => (v.config.instructions = "New.")));
    expect(body.config?.v).toBe(2);
    expect(body.config?.instructions).toBe("New.");
    expect(body.config?.panel).toEqual({ panel_id: "insurance_notebook", layout: "wide", blocks: [] });
    expect(body.config?.qa).toEqual({ enabled: true, rubric_prompt: "Score empathy", model: null });
    expect(body.config?.flow).toBeNull();
    expect(body.config?.pipeline.vad).toEqual(ref("silero-vad"));
    // avatar_options is now an editable form field (V2-13's providers section): the form always carries all
    // four sub-fields (defaults fill in the ones a stored partial object omitted), unlike `pipeline.vad`/etc.
    // above, which stay untouched `ProviderRef`s because the form never edited them in this fixture.
    expect(body.config?.pipeline.avatar_options).toEqual({
      participant_name: "Avatar",
      video_quality: "high",
      idle_timeout_s: null,
      max_duration_s: null,
    });
    expect(body.config?.voice?.first_speaker).toBe("user");
    expect(body.config?.capabilities?.dtmf).toBe(true);
    expect(body.config?.pack_settings).toEqual({ region: "eu" });
    expect(body.config?.timezone).toBe("Europe/Berlin");
  });

  it.each([
    ["cascaded", { realtime: null }, ["stt", "llm", "tts"]],
    ["realtime", { stt: null, llm: null, tts: null }, ["realtime"]],
    ["half_cascade", { stt: null, llm: null }, ["realtime", "tts"]],
  ] as const)("nulls the slots %s does not use", (mode, nulled, kept) => {
    const a = agent();
    const body = buildAgentUpdate(a, submitted(a, (v) => (v.config.pipeline.mode = mode)));
    for (const [slot, value] of Object.entries(nulled)) {
      expect(body.config?.pipeline[slot as keyof typeof nulled]).toBe(value);
    }
    for (const slot of kept) expect(body.config?.pipeline[slot]).not.toBeNull();
  });

  it("sends top-level v2 fields only when they changed", () => {
    const a = agent();
    const clean = buildAgentUpdate(a, submitted(a));
    expect(clean).not.toHaveProperty("limits");
    expect(clean).not.toHaveProperty("allowed_origins");
    expect(clean).not.toHaveProperty("mode");
    expect(clean).not.toHaveProperty("connection_id");

    const changed = buildAgentUpdate(
      a,
      submitted(a, (v) => {
        v.limits.max_concurrent_sessions = 2;
        v.allowed_origins = ["https://example.com"];
        v.connection_id = "conn-1";
      }),
    );
    expect(changed.limits?.max_concurrent_sessions).toBe(2);
    expect(changed.allowed_origins).toEqual(["https://example.com"]);
    expect(changed.connection_id).toBe("conn-1");
  });

  it("keeps config.panel.panel_id in step when the panel id changes", () => {
    const a = agent();
    const body = buildAgentUpdate(a, submitted(a, (v) => (v.ui_panel_id = "generic")));
    expect(body.ui_panel_id).toBe("generic");
    // The layout follows the new panel (generic is a side panel; the notebook is wide).
    expect(body.config?.panel).toEqual({ panel_id: "generic", layout: "side", blocks: [] });
  });

  it("writes the recording settings", () => {
    const a = agent();
    const body = buildAgentUpdate(
      a,
      submitted(a, (v) => (v.config.recording = { ...v.config.recording, enabled: true, retention_days: 30 })),
    );
    expect(body.config?.recording).toEqual({ enabled: true, audio_only: true, storage_config_id: null, retention_days: 30 });
  });
});

describe("unappliedFields", () => {
  it("names fields the api returned unchanged", () => {
    const a = agent();
    const sent = buildAgentUpdate(
      a,
      submitted(a, (v) => {
        v.limits.max_concurrent_sessions = 2;
        v.allowed_origins = ["*"];
      }),
    );
    expect(unappliedFields(sent, a)).toEqual(["limits", "allowed origins"]);
    expect(
      unappliedFields(sent, agent({ limits: { ...a.limits, max_concurrent_sessions: 2 }, allowed_origins: ["*"] })),
    ).toEqual([]);
  });
});
