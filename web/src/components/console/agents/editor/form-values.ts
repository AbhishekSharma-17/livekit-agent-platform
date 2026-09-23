import {
  DEFAULT_AVATAR_OPTIONS,
  DEFAULT_CAPABILITIES,
  DEFAULT_KNOWLEDGE,
  DEFAULT_LIMITS,
  DEFAULT_RECORDING,
  DEFAULT_TOOLS,
  DEFAULT_VOICE,
} from "@/components/console/agents/defaults";
import type { AgentEditorForm, PanelLayoutForm } from "@/components/console/lib/schemas";
import { panelMeta } from "@/components/shared/panel-meta";
import type { AgentConfig, AgentLimits, AgentOut, AgentUpdate, PipelineConfig } from "@/contracts/lkap-contracts";

/**
 * Agent ⇄ editor form conversion (WP-3).
 *
 * The form holds only what the sections edit (`config.panel` since V2-11's
 * composer). Everything else in the stored `AgentConfig` v2 — `qa`, `flow`, `pipeline.vad` / `turn_detection` /
 * `noise_cancellation` / `avatar_options`, `voice.first_speaker`,
 * `capabilities.dtmf`, and whatever a later contract adds — is merged back
 * from the loaded agent in `buildAgentUpdate`, so a save never resets a field
 * the editor does not show (a PUT replaces the whole config and Pydantic
 * would refill the defaults, e.g. `panel.panel_id → "composite"`).
 */

export function limitsOf(agent: AgentOut): Required<AgentLimits> {
  return { ...DEFAULT_LIMITS, ...agent.limits };
}

/**
 * The stored `config.panel` as the composer edits it: every field present,
 * blocks in render order with `order` = position, `title` explicit.
 */
export function panelFormValue(agent: AgentOut): PanelLayoutForm {
  const stored = agent.config.panel;
  const panelId = stored?.panel_id || agent.ui_panel_id || "composite";
  const blocks = [...(stored?.blocks ?? [])]
    .map((block, index) => ({ block, index }))
    .sort((a, b) => (a.block.order ?? 0) - (b.block.order ?? 0) || a.index - b.index)
    .map(({ block }, order) => ({
      id: block.id,
      type: block.type,
      title: (block as { title?: string | null }).title ?? null,
      config: block.config ?? {},
      order,
    }));
  return { panel_id: panelId, layout: stored?.layout ?? panelMeta(panelId).layout, blocks };
}

export function toFormValues(agent: AgentOut): AgentEditorForm {
  const config = agent.config;
  return {
    name: agent.name,
    description: agent.description ?? "",
    ui_panel_id: agent.ui_panel_id,
    mode: agent.mode ?? "prompt",
    connection_id: agent.connection_id ?? null,
    limits: limitsOf(agent),
    allowed_origins: agent.allowed_origins ?? [],
    config: {
      instructions: config.instructions,
      pipeline: {
        ...config.pipeline,
        mode: config.pipeline.mode ?? "cascaded",
        avatar_options: { ...DEFAULT_AVATAR_OPTIONS, ...config.pipeline.avatar_options },
      },
      voice: { ...DEFAULT_VOICE, ...config.voice },
      capabilities: { ...DEFAULT_CAPABILITIES, ...config.capabilities },
      tools: { ...DEFAULT_TOOLS, ...config.tools },
      knowledge: { ...DEFAULT_KNOWLEDGE, ...config.knowledge },
      pack_settings: config.pack_settings ?? {},
      timezone: config.timezone ?? "UTC",
      recording: { ...DEFAULT_RECORDING, ...config.recording },
      panel: panelFormValue(agent),
    },
  };
}

/**
 * The slots the inactive mode leaves behind stay in form state (so switching
 * back restores them) but are nulled in the payload: the api validates every
 * non-null `ProviderRef` at save, and a half-configured leftover would fail
 * with no visible cause (docs/CONTRACTS.md §6).
 */
export function payloadPipeline(stored: PipelineConfig, edited: AgentEditorForm["config"]["pipeline"]): PipelineConfig {
  const pipeline: PipelineConfig = { ...stored, ...edited };
  switch (pipeline.mode) {
    case "cascaded":
      pipeline.realtime = null;
      break;
    case "realtime":
      pipeline.stt = null;
      pipeline.llm = null;
      pipeline.tts = null;
      break;
    case "half_cascade":
      pipeline.stt = null;
      pipeline.llm = null;
      break;
  }
  return pipeline;
}

function stableJson(value: unknown): string {
  return JSON.stringify(value, (_key, v: unknown) =>
    v && typeof v === "object" && !Array.isArray(v)
      ? Object.fromEntries(Object.entries(v as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b)))
      : v,
  );
}

function sameJson(a: unknown, b: unknown): boolean {
  return stableJson(a) === stableJson(b);
}

/** The `PUT /v1/agents/{id}` body for a submitted form. */
export function buildAgentUpdate(agent: AgentOut, values: AgentEditorForm): AgentUpdate {
  const stored = agent.config;
  const edited = values.config;
  const config: AgentConfig = {
    ...stored,
    v: 2,
    instructions: edited.instructions,
    pipeline: payloadPipeline(stored.pipeline, edited.pipeline),
    voice: { ...stored.voice, ...edited.voice },
    capabilities: { ...stored.capabilities, ...edited.capabilities },
    tools: { ...stored.tools, ...edited.tools },
    knowledge: { ...stored.knowledge, ...edited.knowledge },
    pack_settings: edited.pack_settings,
    timezone: edited.timezone,
    recording: { ...stored.recording, ...edited.recording },
  };
  // The panel composer (V2-11) owns `config.panel` and writes `ui_panel_id`
  // with it; `ui_panel_id` is the read-only mirror of `config.panel.panel_id`
  // (CONTRACTS-V2 §1.3). A form whose `ui_panel_id` alone moved (anything
  // still editing the mirror) takes the new id and that panel's layout.
  let panel: PanelLayoutForm = edited.panel ?? panelFormValue(agent);
  if (values.ui_panel_id !== agent.ui_panel_id && panel.panel_id === panelFormValue(agent).panel_id) {
    panel = { ...panel, panel_id: values.ui_panel_id, layout: panelMeta(values.ui_panel_id).layout };
  }
  config.panel = {
    ...panel,
    // `order` is the position; the worker and the browser sort by it.
    blocks: panel.blocks.map((block, order) => ({ ...block, order })),
  };

  const body: AgentUpdate = {
    name: values.name,
    description: values.description,
    ui_panel_id: panel.panel_id,
    config,
  };
  // Top-level v2 fields are sent only when they changed, so an api that does
  // not accept them yet is never asked to, and an unchanged mode or binding
  // is never re-posted.
  if ((agent.mode ?? "prompt") !== values.mode) body.mode = values.mode;
  if ((agent.connection_id ?? null) !== values.connection_id && values.connection_id) {
    body.connection_id = values.connection_id;
  }
  if (!sameJson(limitsOf(agent), values.limits)) body.limits = values.limits;
  if (!sameJson(agent.allowed_origins ?? [], values.allowed_origins)) body.allowed_origins = values.allowed_origins;
  return body;
}

/**
 * Top-level fields the api was asked to change but returned unchanged — the
 * running api predates them (e.g. `limits` before V2-02). The editor warns
 * instead of claiming they were saved.
 */
export function unappliedFields(sent: AgentUpdate, updated: AgentOut): string[] {
  const out: string[] = [];
  if (sent.mode !== undefined && sent.mode !== null && (updated.mode ?? "prompt") !== sent.mode) out.push("mode");
  if (sent.connection_id && updated.connection_id !== sent.connection_id) out.push("connection");
  if (sent.limits && !sameJson(limitsOf(updated), { ...DEFAULT_LIMITS, ...sent.limits })) out.push("limits");
  if (sent.allowed_origins && !sameJson(updated.allowed_origins ?? [], sent.allowed_origins)) out.push("allowed origins");
  return out;
}
