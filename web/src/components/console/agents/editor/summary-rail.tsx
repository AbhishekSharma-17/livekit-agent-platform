"use client";

import * as React from "react";
import { useController, useWatch } from "react-hook-form";
import { ChevronRightIcon } from "lucide-react";

import { CopyButton } from "@/components/shared/copy-button";
import { Icon } from "@/components/shared/icon";
import { RelativeTime } from "@/components/shared/relative-time";
import { StatusChip } from "@/components/shared/status-chip";
import { VendorMark } from "@/components/shared/vendor-mark";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { usePacks, useProviders, useTools } from "@/components/console/lib/api-hooks";
import { BUILTIN_TOOLS, CAPABILITY_META, type CapabilityKey } from "@/components/console/lib/constants";
import { panelMeta } from "@/components/shared/panel-meta";
import type { AgentEditorForm, ProviderRefForm } from "@/components/console/lib/schemas";
import type { AgentOut, ProviderSpec } from "@/contracts/lkap-contracts";
import { pluralize } from "@/lib/format";
import { cn } from "@/lib/utils";

import { useEditorContext } from "./editor-context";
import { publicUrl } from "./publish-popover";
import type { ResolvedEditorSlots } from "./registry";
import { connectionTypeLabel, useAgentConnection } from "./use-connection";

const CAPABILITY_KEYS: CapabilityKey[] = ["camera", "screen_share", "chat_input", "vision_inject_per_turn"];

type PipelineSlot = "stt" | "llm" | "tts" | "realtime" | "avatar" | "image_gen";

const SLOT_LABEL: Record<PipelineSlot, string> = {
  stt: "Speech-to-text",
  llm: "Language model",
  tts: "Text-to-speech",
  realtime: "Realtime model",
  avatar: "Avatar",
  image_gen: "Image generation",
};

/** Slots shown in the mini-flow for a mode, in pipeline order. */
export function pipelineSlots(mode: AgentEditorForm["config"]["pipeline"]["mode"]): PipelineSlot[] {
  switch (mode) {
    case "realtime":
      return ["realtime"];
    case "half_cascade":
      return ["realtime", "tts"];
    default:
      return ["stt", "llm", "tts"];
  }
}

/** "4 built-in · 1 HTTP · 2 pack". */
export function toolsSummary(counts: { builtin: number; http: number; mcp: number; pack: number }): string {
  const parts = [`${counts.builtin} built-in`];
  if (counts.http > 0) parts.push(`${counts.http} HTTP`);
  if (counts.mcp > 0) parts.push(`${counts.mcp} MCP`);
  if (counts.pack > 0) parts.push(`${counts.pack} pack`);
  return parts.join(" · ");
}

/** Human panel label for a panel id (`composite` counts its blocks). */
export function panelLabel(panelId: string, blockCount: number): string {
  if (panelId === "composite") return `Blocks panel · ${pluralize(blockCount, "block", "blocks")}`;
  return panelMeta(panelId).label;
}

/** Called after a rail row switches section (the Summary dialog closes itself). */
const RailNavigateContext = React.createContext<(() => void) | undefined>(undefined);

interface RailRowProps {
  label: string;
  section?: string;
  children: React.ReactNode;
}

/** A labelled rail row; with `section` the whole row is a button to that section. */
function RailRow({ label, section, children }: RailRowProps) {
  const ctx = useEditorContext();
  const onNavigate = React.useContext(RailNavigateContext);
  const canLink = Boolean(section && ctx?.sections.some((s) => s.id === section));
  const body = (
    <>
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <span className="min-w-0 text-[0.8125rem] leading-[1.125rem]">{children}</span>
    </>
  );
  if (!canLink || !section || !ctx) {
    return <div className="flex flex-col gap-1 px-4 py-3">{body}</div>;
  }
  return (
    <button
      type="button"
      onClick={() => {
        ctx.goToSection(section);
        onNavigate?.();
      }}
      aria-label={`${label}: open the ${ctx.sections.find((s) => s.id === section)?.label ?? section} section`}
      className="group/rail-row relative flex w-full flex-col gap-1 px-4 py-3 pr-9 text-left outline-none transition-colors duration-(--dur-2) ease-out hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
    >
      {body}
      <Icon
        as={ChevronRightIcon}
        size="sm"
        className="absolute top-3.5 right-3 text-muted-foreground opacity-0 transition-opacity duration-(--dur-2) group-hover/rail-row:opacity-100 group-focus-visible/rail-row:opacity-100"
      />
    </button>
  );
}

function SlotLine({ slot, value, providers }: { slot: PipelineSlot; value: ProviderRefForm | null | undefined; providers: ProviderSpec[] }) {
  if (!value?.provider_id) {
    return (
      <li className="flex items-center gap-2">
        <span className="inline-flex size-5 shrink-0 items-center justify-center rounded-xs border border-dashed border-border" />
        <span className="text-muted-foreground">{SLOT_LABEL[slot]} not set</span>
      </li>
    );
  }
  const spec = providers.find((provider) => provider.id === value.provider_id);
  const modelId = value.model || spec?.default_model || null;
  const modelLabel = modelId ? (spec?.models?.find((model) => model.id === modelId)?.label ?? modelId) : null;
  return (
    <li className="flex min-w-0 items-center gap-2">
      <VendorMark vendor={spec?.vendor ?? value.provider_id} size="sm" />
      <span className="min-w-0">
        <span className="block truncate font-medium">{spec?.label ?? value.provider_id}</span>
        {modelLabel ? <span className="block truncate text-xs text-muted-foreground">{modelLabel}</span> : null}
      </span>
    </li>
  );
}

export interface SummaryRailProps {
  agent: AgentOut;
  slots: ResolvedEditorSlots;
  className?: string;
  onNavigate?: () => void;
}

/**
 * "What this agent does" (docs/UI_UX_SPEC.md §4.3 + V2 amendments §2.3):
 * pack, connection, mode, pipeline mini-flow, capabilities, panel, tools,
 * knowledge, recording, publish state, description (edited inline, saved with
 * the form), version + history slot, last saved. Rows link to their section.
 */
export function SummaryRail({ agent, slots, className, onNavigate }: SummaryRailProps) {
  // Controlled: the rail renders twice on small screens (hidden column + the Summary dialog).
  const description = useController<AgentEditorForm, "description">({ name: "description" });
  const config = useWatch<AgentEditorForm, "config">({ name: "config" });
  const mode = useWatch<AgentEditorForm, "mode">({ name: "mode" });
  const uiPanelId = useWatch<AgentEditorForm, "ui_panel_id">({ name: "ui_panel_id" });
  const providersQuery = useProviders();
  const packsQuery = usePacks();
  const toolsQuery = useTools(agent.id);
  const connectionQuery = useAgentConnection(agent.connection_id);
  const descriptionId = React.useId();

  const providers = providersQuery.data?.providers ?? [];
  const pack = packsQuery.data?.items.find((item) => item.manifest.id === agent.pack_id)?.manifest;
  const pipeline = config?.pipeline;
  const pipelineMode = pipeline?.mode ?? "cascaded";

  const attachedIds = new Set(config?.tools?.tool_ids ?? []);
  const ownTools = toolsQuery.data?.items ?? [];
  const toolCounts = {
    builtin: BUILTIN_TOOLS.filter((tool) => !(config?.tools?.builtin_disabled ?? []).includes(tool.name)).length,
    http: ownTools.filter((tool) => tool.kind === "http" && attachedIds.has(tool.id)).length,
    mcp: ownTools.filter((tool) => tool.kind === "mcp" && attachedIds.has(tool.id)).length,
    pack: pack?.tool_names.length ?? 0,
  };

  const kbCount = config?.knowledge?.kb_ids?.length ?? 0;
  // The composer edits `config.panel` in the form (V2-11); fall back to the saved one.
  const panel = config?.panel ?? agent.config.panel;
  const panelId = uiPanelId && uiPanelId !== agent.ui_panel_id ? uiPanelId : (panel?.panel_id ?? agent.ui_panel_id);
  const recordingOn = Boolean(config?.recording?.enabled);
  const VersionHistory = slots.versionHistory;

  let connectionText = "Default connection";
  if (agent.connection_id) {
    connectionText = connectionQuery.data
      ? `${connectionQuery.data.name} · ${connectionTypeLabel(connectionQuery.data)}`
      : `Connection ${agent.connection_id.slice(0, 8)}`;
  }

  const modeText = { cascaded: "Cascaded", realtime: "Realtime", half_cascade: "Half-cascade" }[pipelineMode];
  const optional: PipelineSlot[] = (["avatar", "image_gen"] as const).filter((slot) => pipeline?.[slot]?.provider_id);

  return (
    <RailNavigateContext.Provider value={onNavigate}>
    <aside aria-label="Agent summary" className={cn("overflow-hidden rounded-lg border border-border bg-card", className)}>
      <h2 className="border-b border-border px-4 py-3 text-sm font-semibold">What this agent does</h2>
      <div className="divide-y divide-border">
        <RailRow label="Status">
          {agent.published ? (
            <span className="flex flex-col gap-1.5">
              <StatusChip tone="live" size="sm" className="self-start">
                Live
              </StatusChip>
              <span className="flex min-w-0 items-center gap-1">
                <span className="min-w-0 flex-1 truncate font-mono text-xs" title={publicUrl(agent.slug)}>
                  /s/{agent.slug}
                </span>
                <CopyButton value={publicUrl(agent.slug)} label="Copy public link" size="xs" />
              </span>
            </span>
          ) : (
            <span className="flex flex-col gap-1">
              <StatusChip tone="neutral" size="sm" className="self-start">
                Draft
              </StatusChip>
              <span className="text-xs text-muted-foreground">Only test calls can reach it.</span>
            </span>
          )}
        </RailRow>
        <RailRow label="Pack">{pack?.name ?? agent.pack_id}</RailRow>
        <RailRow label="Connection" section="providers">
          {connectionText}
        </RailRow>
        <RailRow label="Mode" section={mode === "flow" ? "flow" : undefined}>
          {mode === "flow" ? "Flow" : "Prompt"}
        </RailRow>
        <RailRow label={`Pipeline · ${modeText}`} section="providers">
          <ol aria-label="Pipeline" className="flex flex-col gap-2">
            {[...pipelineSlots(pipelineMode), ...optional].map((slot) => (
              <SlotLine key={slot} slot={slot} value={pipeline?.[slot]} providers={providers} />
            ))}
          </ol>
        </RailRow>
        <RailRow label="Capabilities" section="panel">
          <ul className="flex flex-wrap gap-1.5">
            {CAPABILITY_KEYS.map((key) => {
              const meta = CAPABILITY_META[key];
              const on = Boolean(config?.capabilities?.[key]);
              return (
                <li
                  key={key}
                  title={`${meta.label}: ${on ? "on" : "off"}`}
                  className={cn(
                    "inline-flex size-7 items-center justify-center rounded-xs border",
                    on ? "border-brand-line bg-brand-soft text-brand-text" : "border-border text-muted-foreground/60",
                  )}
                >
                  <Icon as={meta.icon} size="sm" label={`${meta.label}: ${on ? "on" : "off"}`} />
                </li>
              );
            })}
          </ul>
        </RailRow>
        <RailRow label="Panel" section="panel">
          {panelLabel(panelId, panel?.blocks?.length ?? 0)}
        </RailRow>
        <RailRow label="Tools" section="tools">
          {toolsSummary(toolCounts)}
        </RailRow>
        <RailRow label="Knowledge" section="knowledge">
          {kbCount > 0 ? pluralize(kbCount, "knowledge base", "knowledge bases") : "None attached"}
        </RailRow>
        <RailRow label="Recording" section="recording">
          {recordingOn ? "On · audio" : "Off"}
        </RailRow>
        <div className="flex flex-col gap-1.5 px-4 py-3">
          <Label htmlFor={descriptionId} className="text-xs font-medium text-muted-foreground">
            Description
          </Label>
          <Textarea
            id={descriptionId}
            rows={3}
            placeholder="What this agent is for"
            className="min-h-16 resize-y text-[0.8125rem]"
            aria-describedby={`${descriptionId}-hint`}
            name={description.field.name}
            value={description.field.value}
            onChange={description.field.onChange}
            onBlur={description.field.onBlur}
          />
          <p id={`${descriptionId}-hint`} className="text-xs text-muted-foreground">
            Shown to callers on the call page. Saved with the agent.
          </p>
        </div>
        <div className="flex flex-col gap-1 px-4 py-3 text-xs text-muted-foreground">
          <span className="flex items-center gap-2">
            <span>Version {agent.config_version}</span>
            {VersionHistory ? (
              <VersionHistory agent={agent} />
            ) : (
              // Placeholder until V2-16 fills the `versionHistory` slot (no versions api yet).
              <button
                type="button"
                disabled
                title="Version history isn't available yet"
                className="rounded-xs font-medium underline underline-offset-2 disabled:cursor-not-allowed disabled:opacity-60"
              >
                History
              </button>
            )}
          </span>
          <span>
            Last saved <RelativeTime iso={agent.updated_at} />
          </span>
        </div>
      </div>
    </aside>
    </RailNavigateContext.Provider>
  );
}
