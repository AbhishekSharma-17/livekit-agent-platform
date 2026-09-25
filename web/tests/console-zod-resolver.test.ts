import { describe, expect, it } from "vitest";

import { zodResolver } from "@/components/console/lib/zod-resolver";
import { agentEditorFormSchema } from "@/components/console/lib/schemas";

/**
 * Regression test: `InstructionsTab`/`ProvidersTab` read nested paths like
 * `errors.config.instructions.message` and `errors.config.pipeline.stt`.
 * A resolver that only writes flat dotted keys (e.g. `errors["config.instructions"]`)
 * satisfies react-hook-form's submit gate but leaves every field-level error
 * invisible — this was caught by review, not by the four build gates.
 */
const LIMITS = {
  max_concurrent_sessions: 5,
  max_session_duration_s: 1800,
  rate_per_ip_per_min: 6,
  rate_per_agent_per_min: 60,
};
const RECORDING = { enabled: false, audio_only: true, storage_config_id: null, retention_days: null };
const AVATAR_OPTIONS = { participant_name: "Avatar", video_quality: null, idle_timeout_s: null, max_duration_s: null };
const PANEL = { panel_id: "composite", layout: "side" as const, blocks: [] };
const TELEPHONY = { transfer_targets: [] };
// V4-13 (BACKGROUND-TOOLS.md §7): `voiceConfigSchema`/`toolsConfigSchema` gained
// `thinking_sound`/`execution_default`/`builtin_execution` — required fields now.
const VOICE = { greeting: "hi", greeting_mode: "say" as const, language: "en", allow_interruptions: true, thinking_sound: "none" as const };
const TOOLS = {
  builtin_disabled: [],
  http_request_enabled: false,
  tool_ids: [],
  max_tool_steps: 3,
  execution_default: "blocking" as const,
  builtin_execution: {},
};

describe("zodResolver", () => {
  it("builds a nested error tree matching the form shape", async () => {
    const resolver = zodResolver(agentEditorFormSchema);
    const result = await resolver(
      {
        name: "",
        description: "",
        ui_panel_id: "generic",
        mode: "prompt",
        connection_id: null,
        limits: LIMITS,
        allowed_origins: [],
        config: {
          instructions: "",
          pipeline: { mode: "cascaded", stt: null, llm: null, tts: null, avatar_options: AVATAR_OPTIONS, turn_handling: {} },
          voice: VOICE,
          capabilities: { camera: false, screen_share: false, chat_input: true, vision_inject_per_turn: true },
          tools: TOOLS,
          knowledge: { kb_ids: [], auto_inject: true, top_k: 4 },
          pack_settings: {},
          timezone: "UTC",
          recording: RECORDING,
          panel: PANEL,
          telephony: TELEPHONY,
        },
      },
      undefined,
      { shouldUseNativeValidation: false, fields: {} },
    );

    expect(result.errors.name).toBeDefined();
    expect(result.errors.name?.message).toBeTruthy();
    expect(result.errors.config).toBeDefined();

    // Nested access exactly as the tab components use it.
    const nested = result.errors as unknown as {
      config?: { instructions?: { message?: string }; pipeline?: { stt?: { message?: string } } };
    };
    expect(nested.config?.instructions?.message).toBeTruthy();
    expect(nested.config?.pipeline?.stt?.message).toBeTruthy();
  });

  it("returns no errors for a valid form", async () => {
    const resolver = zodResolver(agentEditorFormSchema);
    const result = await resolver(
      {
        name: "Claims intake",
        description: "",
        ui_panel_id: "generic",
        mode: "prompt",
        connection_id: null,
        limits: LIMITS,
        allowed_origins: [],
        config: {
          instructions: "You are a helpful assistant.",
          pipeline: {
            mode: "cascaded",
            stt: { provider_id: "livekit-inference-stt", credential_id: null, model: null, fields: {} },
            llm: { provider_id: "livekit-inference-llm", credential_id: null, model: null, fields: {} },
            tts: { provider_id: "livekit-inference-tts", credential_id: null, model: null, fields: {} },
            avatar_options: AVATAR_OPTIONS,
            turn_handling: {},
          },
          voice: VOICE,
          capabilities: { camera: false, screen_share: false, chat_input: true, vision_inject_per_turn: true },
          tools: TOOLS,
          knowledge: { kb_ids: [], auto_inject: true, top_k: 4 },
          pack_settings: {},
          timezone: "UTC",
          recording: RECORDING,
          panel: PANEL,
          telephony: TELEPHONY,
        },
      },
      undefined,
      { shouldUseNativeValidation: false, fields: {} },
    );

    expect(Object.keys(result.errors)).toHaveLength(0);
  });

  describe("v2 editor fields", () => {
    const ref = (provider_id: string) => ({ provider_id, credential_id: null, model: null, fields: {} });

    function form(overrides: { pipeline?: Record<string, unknown>; limits?: Record<string, unknown>; allowed_origins?: string[]; recording?: Record<string, unknown> } = {}) {
      return {
        name: "Claims intake",
        description: "",
        ui_panel_id: "generic",
        mode: "prompt" as const,
        connection_id: null,
        limits: { ...LIMITS, ...overrides.limits },
        allowed_origins: overrides.allowed_origins ?? [],
        config: {
          instructions: "Help.",
          pipeline: {
            mode: "cascaded",
            stt: ref("livekit-inference-stt"),
            llm: ref("livekit-inference-llm"),
            tts: ref("livekit-inference-tts"),
            avatar_options: AVATAR_OPTIONS,
            turn_handling: {},
            ...overrides.pipeline,
          },
          voice: VOICE,
          capabilities: { camera: false, screen_share: false, chat_input: true, vision_inject_per_turn: true },
          tools: TOOLS,
          knowledge: { kb_ids: [], auto_inject: true, top_k: 4 },
          pack_settings: {},
          timezone: "UTC",
          recording: { ...RECORDING, ...overrides.recording },
          panel: PANEL,
          telephony: TELEPHONY,
        },
      };
    }

    async function errorsFor(values: ReturnType<typeof form>) {
      const resolver = zodResolver(agentEditorFormSchema);
      const result = await resolver(values as never, undefined, { shouldUseNativeValidation: false, fields: {} });
      return result.errors as unknown;
    }

    /** The error message at a dotted path of the nested error tree (`limits.rate_per_ip_per_min`). */
    function messageAt(tree: unknown, path: string): string | undefined {
      let node: unknown = tree;
      for (const key of path.split(".")) {
        node = node && typeof node === "object" ? (node as Record<string, unknown>)[key] : undefined;
      }
      return node && typeof node === "object" ? ((node as { message?: string }).message ?? undefined) : undefined;
    }

    it("requires a realtime model and a voice in half-cascade mode, but not stt/llm", async () => {
      const errors = await errorsFor(form({ pipeline: { mode: "half_cascade", stt: null, llm: null, tts: null, realtime: null } }));
      expect(messageAt(errors, "config.pipeline.realtime")).toBe("Choose a realtime model.");
      expect(messageAt(errors, "config.pipeline.tts")).toBe("Choose a text-to-speech provider.");
      expect(messageAt(errors, "config.pipeline.stt")).toBeUndefined();
      expect(messageAt(errors, "config.pipeline.llm")).toBeUndefined();
    });

    it("accepts a complete half-cascade pipeline", async () => {
      const errors = await errorsFor(
        form({ pipeline: { mode: "half_cascade", stt: null, llm: null, realtime: ref("google-realtime"), tts: ref("livekit-inference-tts") } }),
      );
      expect(Object.keys(errors as object)).toHaveLength(0);
    });

    it.each([
      ["max_concurrent_sessions", Number.NaN, "Enter a number"],
      ["max_concurrent_sessions", 0, "At least 1"],
      ["max_session_duration_s", 30, "At least 60 seconds"],
      ["rate_per_ip_per_min", 1.5, "Whole numbers only"],
    ])("rejects limits.%s = %s", async (key, value, message) => {
      const errors = await errorsFor(form({ limits: { [key]: value } }));
      expect(messageAt(errors, `limits.${key}`)).toBe(message);
    });

    it("validates allowed origins", async () => {
      expect(Object.keys((await errorsFor(form({ allowed_origins: ["https://example.com", "*", "http://localhost:3000"] }))) as object)).toHaveLength(0);
      const errors = await errorsFor(form({ allowed_origins: ["example.com/path"] }));
      expect(messageAt(errors, "allowed_origins.0")).toMatch(/origin like https:\/\/example.com/);
    });

    it("allows an empty retention but not zero days", async () => {
      expect(Object.keys((await errorsFor(form({ recording: { retention_days: null } }))) as object)).toHaveLength(0);
      const errors = await errorsFor(form({ recording: { retention_days: 0 } }));
      expect(messageAt(errors, "config.recording.retention_days")).toBe("At least 1 day");
    });
  });

  describe("V2-19: flow instructions (asks V2-16-5) and transfer destinations (R-V2-21)", () => {
    const ref = (provider_id: string) => ({ provider_id, credential_id: null, model: null, fields: {} });
    const FLOW = {
      nodes: [
        { id: "start", kind: "start" as const, greeting: "Hi" },
        { id: "ask", kind: "agent" as const, instructions: "Ask." },
      ],
      edges: [{ id: "e1", source: "start", target: "ask", condition: "always" }],
    };

    function config(overrides: Record<string, unknown>) {
      return {
        name: "Intake",
        description: "",
        ui_panel_id: "generic",
        mode: "prompt" as const,
        connection_id: null,
        limits: LIMITS,
        allowed_origins: [],
        config: {
          instructions: "Help.",
          pipeline: {
            mode: "cascaded" as const,
            stt: ref("livekit-inference-stt"),
            llm: ref("livekit-inference-llm"),
            tts: ref("livekit-inference-tts"),
            avatar_options: AVATAR_OPTIONS,
            turn_handling: {},
          },
          voice: VOICE,
          capabilities: { camera: false, screen_share: false, chat_input: true, vision_inject_per_turn: true },
          tools: TOOLS,
          knowledge: { kb_ids: [], auto_inject: true, top_k: 4 },
          pack_settings: {},
          timezone: "UTC",
          recording: RECORDING,
          panel: PANEL,
          telephony: TELEPHONY,
          ...overrides,
        },
      };
    }

    async function configErrors(overrides: Record<string, unknown>) {
      const resolver = zodResolver(agentEditorFormSchema);
      const values = config(overrides) as Parameters<typeof resolver>[0];
      const result = await resolver(values, undefined, { shouldUseNativeValidation: false, fields: {} });
      return result.errors as unknown as {
        config?: {
          instructions?: { message?: string };
          telephony?: { transfer_targets?: Record<number, { label?: { message?: string }; to?: { message?: string } }> };
        };
      };
    }

    it("still requires instructions for a prompt agent", async () => {
      const errors = await configErrors({ instructions: "  " });
      expect(errors.config?.instructions?.message).toBe("Instructions are required");
    });

    it("lets a flow agent (config.flow has nodes) save empty instructions", async () => {
      expect(Object.keys(await configErrors({ instructions: "", flow: FLOW }))).toHaveLength(0);
    });

    it("requires instructions when the flow is empty", async () => {
      const errors = await configErrors({ instructions: "", flow: { nodes: [], edges: [] } });
      expect(errors.config?.instructions?.message).toBe("Instructions are required");
    });

    it("accepts E.164, tel: and sip: destinations", async () => {
      const telephony = {
        transfer_targets: [
          { label: "Sales", to: "+15550001111" },
          { label: "Desk", to: "sip:desk@pbx.example.com" },
          { label: "Night", to: "tel:+15550002222" },
        ],
      };
      expect(Object.keys(await configErrors({ telephony }))).toHaveLength(0);
    });

    it("rejects a free-text destination and a duplicate name", async () => {
      const telephony = {
        transfer_targets: [
          { label: "Sales", to: "+15550001111" },
          { label: "sales", to: "call mom" },
        ],
      };
      const targets = (await configErrors({ telephony })).config?.telephony?.transfer_targets;
      expect(targets?.[1]?.to?.message).toMatch(/\+15551234567/);
      expect(targets?.[1]?.label?.message).toBe("Two destinations can't share a name.");
      expect(targets?.[0]).toBeUndefined();
    });
  });
});
