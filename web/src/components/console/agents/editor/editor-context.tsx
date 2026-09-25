"use client";

import * as React from "react";
import { useWatch } from "react-hook-form";

import { useCostEstimate, useEstimateSettings } from "@/components/console/lib/cost-hooks";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import type { AgentConfig, AgentOut, CostEstimate, CostEstimateRequest } from "@/contracts/lkap-contracts";

import { buildAgentUpdate } from "./form-values";
import type { EditorSectionDef } from "./types";
import { GENERAL_SECTION, normalizeIssuePath, type EditorIssue } from "./validation-map";

/**
 * Shell state that sections may read (WP-3). Every consumer must tolerate a
 * missing provider: section components are rendered by their own tests
 * without the shell, so `useSectionIssues` returns an empty result there.
 */
export interface EditorContextValue {
  agent: AgentOut;
  /** Visible sections in nav order. */
  sections: EditorSectionDef[];
  activeSection: string;
  /** Switches section (updates `?section=` with `router.replace`). */
  goToSection: (id: string) => void;
  /** Server (last validate / failed save) and client (zod) issues, already mapped to sections. */
  issues: EditorIssue[];
  /** Switches to the issue's section and focuses its field when one can be found. */
  focusIssue: (issue: EditorIssue) => void;
}

const EditorContext = React.createContext<EditorContextValue | null>(null);

export const EditorContextProvider = EditorContext.Provider;

/** The shell's context, or null outside the editor. */
export function useEditorContext(): EditorContextValue | null {
  return React.useContext(EditorContext);
}

export interface SectionIssues {
  /** Issues mapped to this section. */
  issues: EditorIssue[];
  errors: EditorIssue[];
  warnings: EditorIssue[];
  /** Issues no section claimed; the shell lists them in every section. */
  general: EditorIssue[];
  /** First issue whose path is `path` or below it (`issueFor("pipeline.stt")`), for field-level hints. */
  issueFor: (path: string) => EditorIssue | undefined;
  focusIssue: (issue: EditorIssue) => void;
}

const NOOP = () => {};

/**
 * Issues for one section (default: the active one). Safe outside the shell:
 * returns empty lists and a no-op `focusIssue`.
 */
export function useSectionIssues(sectionId?: string): SectionIssues {
  const ctx = useEditorContext();
  return React.useMemo(() => {
    const id = sectionId ?? ctx?.activeSection;
    const all = ctx?.issues ?? [];
    const issues = all.filter((issue) => issue.section === id);
    return {
      issues,
      errors: issues.filter((issue) => issue.severity === "error"),
      warnings: issues.filter((issue) => issue.severity === "warning"),
      general: all.filter((issue) => issue.section === GENERAL_SECTION),
      issueFor: (path: string) => {
        const target = normalizeIssuePath(path);
        return all.find(
          (issue) => issue.path !== null && (issue.path === target || issue.path.startsWith(`${target}.`)),
        );
      },
      focusIssue: ctx?.focusIssue ?? NOOP,
    };
  }, [ctx, sectionId]);
}

// ---- the shared cost estimate (docs/v4/COSTS.md D-V4-47, R-V4-46) ----

/** `{pipeline, knowledge, tools, qa, recording}` only — what the estimate actually prices. */
function pricedSubsetKey(config: AgentConfig | null): string {
  if (!config) return "";
  const subset = {
    pipeline: config.pipeline,
    knowledge: config.knowledge,
    tools: config.tools,
    qa: config.qa,
    recording: config.recording,
  };
  return JSON.stringify(subset, (_key, v: unknown) =>
    v && typeof v === "object" && !Array.isArray(v)
      ? Object.fromEntries(Object.entries(v as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b)))
      : v,
  );
}

/**
 * Debounces `value` by `key`: a new `key` schedules `value` to replace the
 * held one after `delay` ms (restarting on every further change); renders
 * where `key` is unchanged are free (no timer, no state write). Content the
 * estimate doesn't price (e.g. `instructions`) can differ between the two
 * without ever triggering a refetch.
 */
function useDebouncedByKey<T>(value: T, key: string, delay: number): T {
  const [held, setHeld] = React.useState<{ key: string; value: T }>(() => ({ key, value }));
  React.useEffect(() => {
    if (key === held.key) return undefined;
    const timer = setTimeout(() => setHeld({ key, value }), delay);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `held.key` is the guard, not a trigger
  }, [key, value, delay]);
  return held.value;
}

/**
 * The estimate every V4-16 surface shares (the rail's Cost row, the estimate
 * dialog, the Providers header figure, its slot chips): the in-progress
 * draft `config` (`form-values.ts::buildAgentUpdate`, the same merge the Save
 * button uses, so an unsaved pipeline change is priced immediately),
 * debounced 500 ms and keyed on the pipeline/knowledge/tools/qa/recording
 * subset (D-V4-47's estimate assumptions never look past those), plus the
 * viewer's own assumption overrides and channel (`useEstimateSettings`,
 * `localStorage`, R-V4-46). "The header figure equals the rail's" holds
 * because both call this hook — same request, same react-query cache entry.
 *
 * Outside the editor (`useEditorContext()` is `null` — a section rendered in
 * isolation by its own tests) this returns a disabled, empty query rather
 * than throwing: `useWatch` still needs a `FormProvider` ancestor, exactly
 * like every other section hook in this file.
 */
export function useDraftCostEstimate() {
  const ctx = useEditorContext();
  const values = useWatch<AgentEditorForm>({});
  const [settings, setSettings] = useEstimateSettings(ctx?.agent.id);

  const draftConfig = React.useMemo<AgentConfig | null>(() => {
    if (!ctx) return null;
    try {
      return buildAgentUpdate(ctx.agent, values as AgentEditorForm).config ?? null;
    } catch {
      return null;
    }
  }, [ctx, values]);

  const key = pricedSubsetKey(draftConfig);
  const debounced = useDebouncedByKey(draftConfig, key, 500);

  const request: CostEstimateRequest | null = debounced
    ? {
        config: debounced,
        assumptions: Object.keys(settings.assumptions).length > 0 ? settings.assumptions : undefined,
        channel: settings.channel,
        workspace_averages: settings.workspaceAverages,
      }
    : null;

  const query = useCostEstimate(request);
  return { ...query, settings, setSettings, estimate: query.data as CostEstimate | undefined };
}
