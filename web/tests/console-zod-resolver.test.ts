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
describe("zodResolver", () => {
  it("builds a nested error tree matching the form shape", async () => {
    const resolver = zodResolver(agentEditorFormSchema);
    const result = await resolver(
      {
        name: "",
        description: "",
        ui_panel_id: "generic",
        config: {
          instructions: "",
          pipeline: { mode: "cascaded", stt: null, llm: null, tts: null, turn_handling: {} },
          voice: { greeting: "hi", greeting_mode: "say", language: "en", allow_interruptions: true },
          capabilities: { camera: false, screen_share: false, chat_input: true, vision_inject_per_turn: true },
          tools: { builtin_disabled: [], http_request_enabled: false, tool_ids: [], max_tool_steps: 3 },
          knowledge: { kb_ids: [], auto_inject: true, top_k: 4 },
          pack_settings: {},
          timezone: "UTC",
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
        config: {
          instructions: "You are a helpful assistant.",
          pipeline: {
            mode: "cascaded",
            stt: { provider_id: "livekit-inference-stt", credential_id: null, model: null, fields: {} },
            llm: { provider_id: "livekit-inference-llm", credential_id: null, model: null, fields: {} },
            tts: { provider_id: "livekit-inference-tts", credential_id: null, model: null, fields: {} },
            turn_handling: {},
          },
          voice: { greeting: "hi", greeting_mode: "say", language: "en", allow_interruptions: true },
          capabilities: { camera: false, screen_share: false, chat_input: true, vision_inject_per_turn: true },
          tools: { builtin_disabled: [], http_request_enabled: false, tool_ids: [], max_tool_steps: 3 },
          knowledge: { kb_ids: [], auto_inject: true, top_k: 4 },
          pack_settings: {},
          timezone: "UTC",
        },
      },
      undefined,
      { shouldUseNativeValidation: false, fields: {} },
    );

    expect(Object.keys(result.errors)).toHaveLength(0);
  });
});
