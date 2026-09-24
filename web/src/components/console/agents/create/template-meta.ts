/**
 * Display metadata and pure helpers for the New agent gallery
 * (docs/v4/TEMPLATES.md §6). Plain data, no React: the tile, the preview, the
 * dialog's post-create toast and the editor's "Next steps" card all read it.
 */
import {
  BookOpenIcon,
  BracesIcon,
  CalendarClockIcon,
  ClipboardCheckIcon,
  ClipboardPenLineIcon,
  ClipboardListIcon,
  CodeXmlIcon,
  FlaskConicalIcon,
  GlobeIcon,
  Grid3x3Icon,
  ImagePlusIcon,
  ImagesIcon,
  LibraryIcon,
  LifeBuoyIcon,
  MonitorUpIcon,
  PhoneForwardedIcon,
  PhoneIcon,
  QuoteIcon,
  ScanEyeIcon,
  SquareDashedIcon,
  Table2Icon,
  TargetIcon,
  VideoIcon,
  WebhookIcon,
  WorkflowIcon,
  type LucideIcon,
} from "lucide-react";

import { BLOCK_CATALOG } from "@/panels/blocks/catalog";
import { panelMeta } from "@/components/shared/panel-meta";
import type {
  AgentConfig,
  NextStep,
  PipelineConfig,
  ProviderSpec,
  StarterTemplate,
  TemplateOut,
} from "@/contracts/lkap-contracts";
import { pluralize } from "@/lib/format";

export type TemplateChip = NonNullable<StarterTemplate["chips"]>[number];
export type EditorSectionId = NonNullable<NextStep["section"]>;

export interface ChipMeta {
  label: string;
  icon: LucideIcon;
  /** One sentence for the chip's tooltip. */
  help: string;
}

/** The closed chip vocabulary (`TemplateChip` in `lkap_contracts.templates`): label + icon per chip. */
export const TEMPLATE_CHIP_META: Record<TemplateChip, ChipMeta> = {
  rag: { label: "Knowledge", icon: BookOpenIcon, help: "Answers from knowledge bases you upload." },
  citations: { label: "Citations", icon: QuoteIcon, help: "Shows the sources behind each answer." },
  http_tools: { label: "HTTP tools", icon: GlobeIcon, help: "Calls your APIs during the conversation." },
  forms: { label: "Forms", icon: ClipboardListIcon, help: "Asks the caller to confirm details on an on-screen form." },
  table: { label: "Table", icon: Table2Icon, help: "Collects rows into an on-screen table." },
  flow: { label: "Flow", icon: WorkflowIcon, help: "Follows a step-by-step conversation flow." },
  variables: { label: "Variables", icon: BracesIcon, help: "Captures named answers as it goes." },
  camera: { label: "Camera", icon: VideoIcon, help: "Sees the caller's camera." },
  screen_share: { label: "Screen share", icon: MonitorUpIcon, help: "Sees a shared window or tab." },
  gallery: { label: "Gallery", icon: ImagesIcon, help: "Pins frames to an on-screen gallery." },
  telephony: { label: "Phone", icon: PhoneIcon, help: "Answers calls to a phone number." },
  dtmf: { label: "Keypad", icon: Grid3x3Icon, help: "Understands keypad presses (DTMF)." },
  transfer: { label: "Transfer", icon: PhoneForwardedIcon, help: "Can hand the call to a person." },
  webhook: { label: "Webhook", icon: WebhookIcon, help: "Posts the outcome to your systems." },
  qa: { label: "Call scoring", icon: ClipboardCheckIcon, help: "Scores every call against a rubric." },
  code_tools: { label: "Code tools", icon: CodeXmlIcon, help: "Ships Python tools with the pack." },
  knowledge_seeds: { label: "Seeded knowledge", icon: LibraryIcon, help: "Comes with knowledge bases filled in." },
  image_gen: { label: "Images", icon: ImagePlusIcon, help: "Draws images during the call." },
};

export type TemplateCategory = StarterTemplate["category"];

/** The tile's leading glyph per category. */
export const TEMPLATE_CATEGORY_META: Record<TemplateCategory, { label: string; icon: LucideIcon }> = {
  blank: { label: "Blank", icon: SquareDashedIcon },
  support: { label: "Support", icon: LifeBuoyIcon },
  scheduling: { label: "Scheduling", icon: CalendarClockIcon },
  vision: { label: "Vision", icon: ScanEyeIcon },
  phone: { label: "Phone", icon: PhoneIcon },
  sales: { label: "Sales", icon: TargetIcon },
  forms: { label: "Forms", icon: ClipboardPenLineIcon },
  example: { label: "Example", icon: FlaskConicalIcon },
};

export type TemplateBadgeKind = "needs_keys" | "optional_key" | "keys_present" | "phone_number" | "webhook" | "example_pack";

export interface BadgeMeta {
  label: string;
  tone: "warning" | "info" | "neutral" | "success";
}

export const TEMPLATE_BADGE_META: Record<TemplateBadgeKind, BadgeMeta> = {
  needs_keys: { label: "Needs keys", tone: "warning" },
  optional_key: { label: "Optional key", tone: "info" },
  keys_present: { label: "Keys present", tone: "success" },
  phone_number: { label: "Needs a phone number", tone: "warning" },
  webhook: { label: "Needs a webhook", tone: "info" },
  example_pack: { label: "Example pack", tone: "neutral" },
};

export interface TemplateBadge {
  kind: TemplateBadgeKind;
  label: string;
  tone: BadgeMeta["tone"];
  /** Tooltip naming the vendor or the requirement. */
  title: string;
}

/** provider_id → display vendor / label, from `useProviders()`. */
export type ProviderLookup = Map<string, Pick<ProviderSpec, "vendor" | "label">>;

export function providerLookup(providers: ProviderSpec[] | undefined): ProviderLookup {
  const map: ProviderLookup = new Map();
  for (const spec of providers ?? []) map.set(spec.id, { vendor: spec.vendor, label: spec.label });
  return map;
}

function vendorOf(providerId: string, providers: ProviderLookup): string {
  return providers.get(providerId)?.vendor ?? providerId;
}

/**
 * The badge row of a tile (§6.2). `keyProviderIds` is the set of provider ids
 * the workspace already holds a key for; when it covers every key the starter
 * lists, the key badge becomes a muted "Keys present" check.
 */
export function templateBadges(
  template: StarterTemplate,
  keyProviderIds: ReadonlySet<string>,
  providers: ProviderLookup,
): TemplateBadge[] {
  const badges: TemplateBadge[] = [];
  const keys = template.requires?.provider_keys ?? [];
  if (keys.length > 0) {
    const vendors = keys
      .map((key) => `${vendorOf(key.provider_id, providers)}${key.purpose ? ` (${key.purpose})` : ""}`)
      .join(", ");
    if (keys.every((key) => keyProviderIds.has(key.provider_id))) {
      badges.push({ kind: "keys_present", ...TEMPLATE_BADGE_META.keys_present, title: `Keys present: ${vendors}` });
    } else if (keys.some((key) => !key.optional)) {
      badges.push({ kind: "needs_keys", ...TEMPLATE_BADGE_META.needs_keys, title: `Needs a key for ${vendors}` });
    } else {
      badges.push({
        kind: "optional_key",
        ...TEMPLATE_BADGE_META.optional_key,
        title: `Works without it. An optional key for ${vendors} unlocks an extra.`,
      });
    }
  }
  if (template.requires?.telephony) {
    badges.push({
      kind: "phone_number",
      ...TEMPLATE_BADGE_META.phone_number,
      title: "Needs a SIP trunk, a phone number and a dispatch rule to take real calls. Test chat works without one.",
    });
  }
  if (template.requires?.webhook_endpoint) {
    badges.push({
      kind: "webhook",
      ...TEMPLATE_BADGE_META.webhook,
      title: "Posts results to a webhook endpoint you add in Settings. Optional for trying it out.",
    });
  }
  if ((template.pack_id ?? "generic") !== "generic") {
    badges.push({
      kind: "example_pack",
      ...TEMPLATE_BADGE_META.example_pack,
      title: "Built on a code pack with its own tools and panel.",
    });
  }
  return badges;
}

/** True when the starter needs no vendor key at all (the "Runs on LiveKit Inference" line). */
export function runsOnInference(template: StarterTemplate): boolean {
  return (template.requires?.provider_keys ?? []).length === 0;
}

/* -------------------------------------------------------------------------- */
/* Preview facts                                                              */
/* -------------------------------------------------------------------------- */

/** The pipeline a new agent gets: the starter's own, else the pack's recommendation. */
export function effectivePipeline(item: TemplateOut): PipelineConfig {
  return item.template.pipeline ?? item.pack.recommended_pipeline;
}

/** Pipeline provider ids in display order (realtime: one; cascaded: STT, LLM, TTS). */
export function pipelineProviderIds(pipeline: PipelineConfig): string[] {
  const mode = pipeline.mode ?? "cascaded";
  if (mode === "realtime") return pipeline.realtime ? [pipeline.realtime.provider_id] : [];
  const refs = mode === "half_cascade" ? [pipeline.realtime, pipeline.tts] : [pipeline.stt, pipeline.llm, pipeline.tts];
  return [...new Set(refs.filter((ref): ref is NonNullable<typeof ref> => Boolean(ref)).map((ref) => ref.provider_id))];
}

export const PIPELINE_MODE_LABEL: Record<NonNullable<PipelineConfig["mode"]>, string> = {
  cascaded: "Cascaded",
  realtime: "Realtime",
  half_cascade: "Half-cascade",
};

/** "Blocks: Status, Sources, Notes, Activity", or the pack panel's label. */
export function panelFact(item: TemplateOut): string {
  const layout = item.template.panel ?? item.pack.default_panel ?? null;
  const panelId = layout?.panel_id ?? item.pack.ui_panel_id;
  const blocks = layout?.blocks ?? [];
  if (panelId === "composite" && blocks.length > 0) {
    const labels = [...blocks]
      .sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
      .map((block) => BLOCK_CATALOG[block.type as keyof typeof BLOCK_CATALOG]?.label ?? block.type);
    return `Blocks: ${labels.join(", ")}`;
  }
  return panelMeta(panelId).label;
}

/** "2 HTTP tools: check_availability, book_appointment" / "4 code tools", or null. */
export function toolsFact(item: TemplateOut): string | null {
  const seeds = item.template.tool_seeds ?? [];
  if (seeds.length > 0) {
    return `${pluralize(seeds.length, "HTTP tool", "HTTP tools")}: ${seeds.map((seed) => seed.definition.name).join(", ")}`;
  }
  const codeTools = item.pack.tool_names.length;
  if (codeTools > 0) return pluralize(codeTools, "code tool", "code tools");
  return null;
}

/** "Seeds 2 knowledge bases", or null. Template seeds win over the pack's. */
export function knowledgeFact(item: TemplateOut): string | null {
  const seeds = (item.template.kb_seeds ?? []).length || (item.pack.kb_seeds ?? []).length;
  return seeds > 0 ? `Seeds ${pluralize(seeds, "knowledge base", "knowledge bases")}` : null;
}

/**
 * "6 nodes, 4 variables", or null. The flow's `global` node is instructions
 * shared by every step, not a step, so it is not counted.
 */
export function flowFact(template: StarterTemplate): string | null {
  const nodes = template.flow?.nodes ?? [];
  if (nodes.length === 0) return null;
  const steps = nodes.filter((node) => node.kind !== "global").length;
  const variables = template.flow?.variables?.length ?? 0;
  const parts = [pluralize(steps, "node", "nodes")];
  if (variables > 0) parts.push(pluralize(variables, "variable", "variables"));
  return parts.join(", ");
}

/** The capabilities a new agent gets (starter overlay, else the pack's). */
export function effectiveCapabilities(item: TemplateOut): NonNullable<StarterTemplate["capabilities"]> {
  return item.template.capabilities ?? item.pack.capabilities;
}

/** The instructions shown in the preview's disclosure (the first `limit` characters). */
export function instructionsExcerpt(item: TemplateOut, limit = 600): string {
  const text = (item.template.instructions ?? item.pack.default_instructions ?? "").trim();
  return text.length > limit ? `${text.slice(0, limit).trimEnd()}…` : text;
}

/* -------------------------------------------------------------------------- */
/* After creating                                                             */
/* -------------------------------------------------------------------------- */

/** The editor section a new agent opens on: the first next step that names one, else Providers. */
export function landingSection(template: StarterTemplate): EditorSectionId {
  return (template.next_steps ?? []).find((step) => step.section)?.section ?? "providers";
}

/** `/console/agents/<id>?section=…&from=<template_id>` (§6.4). */
export function agentLandingHref(agentId: string, template: StarterTemplate): string {
  const params = new URLSearchParams({ section: landingSection(template), from: template.id });
  return `/console/agents/${agentId}?${params.toString()}`;
}

/**
 * What seeding gated (D-V4-8, R-V4-4): the starter asked for something the
 * created config doesn't have. Client-side comparison of the starter (and its
 * pack's defaults) with the returned config; the api response shape is
 * unchanged. One plain sentence per difference, each naming the fix.
 */
export function gatedDifferences(
  item: TemplateOut,
  config: AgentConfig,
  providers: ProviderLookup = new Map(),
): string[] {
  const notes: string[] = [];
  const asked = effectiveCapabilities(item);
  if (asked.dtmf && !config.capabilities?.dtmf) {
    notes.push("DTMF is off until a SIP trunk is reachable on this connection.");
  }

  const pipeline = effectivePipeline(item);
  const keys = item.template.requires?.provider_keys ?? [];
  const purposeOf = (providerId: string, fallback: string) =>
    keys.find((key) => key.provider_id === providerId)?.purpose || fallback;

  const askedMode = pipeline.mode ?? "cascaded";
  const gotMode = config.pipeline?.mode ?? "cascaded";
  if (askedMode !== "cascaded" && gotMode === "cascaded" && pipeline.realtime) {
    const providerId = pipeline.realtime.provider_id;
    notes.push(
      `Running on LiveKit Inference — add a ${vendorOf(providerId, providers)} key for ${purposeOf(providerId, "realtime voice")}.`,
    );
  }
  if (pipeline.image_gen && !config.pipeline?.image_gen) {
    const providerId = pipeline.image_gen.provider_id;
    notes.push(
      `Running on LiveKit Inference — add a ${vendorOf(providerId, providers)} key for ${purposeOf(providerId, "image generation")}.`,
    );
  }
  if (item.template.recording?.enabled && !config.recording?.enabled) {
    notes.push("Recording is off until this connection has Egress and a storage config.");
  }
  return notes;
}

/** Where a next step goes: a section of this agent's editor, or another console page. */
export function nextStepHref(step: NextStep, agentHref: string): string | null {
  if (step.href) return step.href;
  if (step.section) return `${agentHref}?section=${step.section}`;
  return null;
}

/** Title-case fallback for a pack id (`insurance_claim` → "Insurance claim"). */
export function humanizeId(id: string): string {
  const text = id.replace(/^pack:/, "").replace(/[_-]+/g, " ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

