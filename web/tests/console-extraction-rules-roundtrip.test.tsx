import { describe, expect, it } from "vitest";

import { buildAgentUpdate, toFormValues } from "@/components/console/agents/editor/form-values";
import { agentEditorFormSchema } from "@/components/console/lib/schemas";
import type { AgentOut, ExtractionConfig, Rule } from "@/contracts/lkap-contracts";

/**
 * V6-13's ask #74: "The editor form's merge-back (`editor/form-values.ts`) already keeps
 * both keys on save; pin that with a test when the tabs land." This is that pin — an agent
 * built entirely outside the console (an MCP `agent_update` patch, a kit) keeps its
 * `config.extraction`/`config.rules` byte-for-byte through a save the Extraction and Rules
 * tabs never touched, and a save that *does* edit one of them leaves the other untouched.
 */

const EXTRACTION: ExtractionConfig = {
  enabled: true,
  fields: [
    {
      name: "policy_number",
      type: "string",
      description: "",
      required: true,
      options: null,
      label: "Policy number",
      hint: "the 8-character policy number, like PX-12345",
      sensitive: false,
      show_in: "details:claim_details",
    },
  ],
  triggers: [{ kind: "every_n_turns", n: 2 }],
  min_turn_chars: 12,
  still_needed: "checklist",
};

const RULES: Rule[] = [
  {
    id: "urgent_hazard",
    label: "Flag an urgent hazard",
    when: "var.hazard matches /fire|gas/i",
    then: [{ do: "status.set", label: "Urgent", tone: "danger" }],
    once: true,
    enabled: true,
  },
];

function agent(overrides: Partial<AgentOut> = {}): AgentOut {
  return {
    id: "a-1",
    slug: "claims",
    name: "Claims",
    description: "",
    pack_id: "generic",
    ui_panel_id: "generic",
    published: false,
    config_version: 1,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    config: {
      instructions: "Hi there",
      pipeline: {
        mode: "cascaded",
        stt: { provider_id: "deepgram-stt" },
        llm: { provider_id: "openai-llm" },
        tts: { provider_id: "cartesia-tts" },
      },
      panel: {
        panel_id: "generic",
        layout: "side",
        blocks: [
          { id: "claim_details", type: "details", title: null, config: {}, order: 0 },
          { id: "checklist", type: "checklist", title: null, config: {}, order: 1 },
        ],
      },
      extraction: EXTRACTION,
      rules: RULES,
    },
    ...overrides,
  } as AgentOut;
}

describe("extraction/rules survive a save the console never edited (ask #74)", () => {
  it("round-trips through toFormValues -> the zod schema (the resolver's own parse) -> buildAgentUpdate unchanged", () => {
    const theAgent = agent();
    const values = toFormValues(theAgent);

    // `agentEditorFormSchema.safeParse` is exactly what `zodResolver` runs on submit — the
    // step that would silently strip `config.extraction`/`config.rules` from the payload
    // if either were missing from the schema (`lib/schemas.ts`'s file-header warning).
    const parsed = agentEditorFormSchema.safeParse(values);
    expect(parsed.success).toBe(true);
    if (!parsed.success) return;

    const update = buildAgentUpdate(theAgent, parsed.data);
    expect(update.config?.extraction).toEqual(EXTRACTION);
    expect(update.config?.rules).toEqual(RULES);
  });

  it("editing the Extraction tab's fields leaves config.rules untouched", () => {
    const theAgent = agent();
    const values = toFormValues(theAgent);
    values.config.extraction = { ...values.config.extraction!, enabled: false };
    const update = buildAgentUpdate(theAgent, values);
    expect(update.config?.rules).toEqual(RULES);
    expect(update.config?.extraction?.enabled).toBe(false);
  });

  it("editing the Rules tab leaves config.extraction untouched", () => {
    const theAgent = agent();
    const values = toFormValues(theAgent);
    values.config.rules = [];
    const update = buildAgentUpdate(theAgent, values);
    expect(update.config?.extraction).toEqual(EXTRACTION);
    expect(update.config?.rules).toEqual([]);
  });

  it("an agent with neither configured resolves unchanged (nothing forced on)", () => {
    const theAgent = agent({
      config: { instructions: "Hi", pipeline: { mode: "cascaded" } },
    });
    const values = toFormValues(theAgent);
    expect(values.config.extraction?.enabled).toBe(false);
    expect(values.config.rules).toEqual([]);
    const update = buildAgentUpdate(theAgent, values);
    expect(update.config?.extraction?.enabled).toBe(false);
    expect(update.config?.rules).toEqual([]);
  });
});
