"use client";

/**
 * `steps` block — a progress timeline (`set_steps`, or the flow position with
 * `config.source == "flow"`; CONTRACTS-V2 §4.4, V5-08 → V5-12).
 *
 * D-V5-33: a legacy `custom` block whose `config.kind == "flow_progress"`
 * keeps its raw `FlowState` mirror on the wire (`{current_node, label, path,
 * disposition}`), but is rendered by this same component — `custom.tsx`
 * calls `stepsFromFlowProgress` below to adapt it, since the mirror carries
 * no label for a node other than the current one.
 */
import * as React from "react";
import { useMemo } from "react";

import type { StepItem, StepsBlockState } from "@/contracts/lkap-contracts";
import { formatTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

type StepStatus = NonNullable<StepItem["status"]>;

const DOT_CLASS: Record<StepStatus, string> = {
  pending: "border-input border-2 bg-background",
  active: "bg-brand ring-brand-soft ring-4",
  done: "bg-success",
  skipped: "bg-muted-foreground/40",
  failed: "bg-danger",
};

/** Never color alone (a11y): every dot's meaning is also read by screen readers. */
const STATUS_LABEL: Record<StepStatus, string> = {
  pending: "Not started yet",
  active: "In progress",
  done: "Done",
  skipped: "Skipped",
  failed: "Failed",
};

function StepRow({ step, showNotes, isLast }: { step: StepItem; showNotes: boolean; isLast: boolean }) {
  const status = step.status ?? "pending";
  return (
    <li data-slot="block-step" data-status={status} className="flex gap-3">
      <div className="flex flex-col items-center">
        <span aria-hidden="true" className={cn("mt-0.5 size-2.5 shrink-0 rounded-full", DOT_CLASS[status])} />
        {!isLast && <span aria-hidden="true" className="bg-border mt-1 w-px flex-1" />}
      </div>
      <div className={cn("min-w-0 flex-1", !isLast && "pb-3.5")}>
        <p
          className={cn(
            "text-sm leading-5",
            status === "pending" && "text-muted-foreground",
            status === "skipped" && "text-muted-foreground line-through",
          )}
        >
          {step.label}
          <span className="sr-only"> — {STATUS_LABEL[status]}</span>
        </p>
        {showNotes && step.note && <p className="text-muted-foreground mt-0.5 text-[0.8125rem] leading-snug">{step.note}</p>}
        {typeof step.at === "number" && <span className="text-muted-foreground text-[0.6875rem]">{formatTime(step.at)}</span>}
      </div>
    </li>
  );
}

export function StepsBlock({ spec, data, title, highlighted }: BlockRenderProps<StepsBlockState>) {
  const steps = useMemo(() => (Array.isArray(data.steps) ? data.steps : []), [data.steps]);
  const showNotes = (spec.config as { show_notes?: unknown } | null)?.show_notes !== false;

  return (
    <BlockFrame spec={spec} title={title} highlighted={highlighted}>
      {steps.length === 0 ? (
        <PanelEmpty>No steps yet.</PanelEmpty>
      ) : (
        <ol data-slot="block-steps" className="flex flex-col">
          {steps.map((step, index) => (
            <StepRow key={step.id} step={step} showNotes={showNotes} isLast={index === steps.length - 1} />
          ))}
        </ol>
      )}
    </BlockFrame>
  );
}

/* -------------------------------------------------------------------------- */
/* D-V5-33: the `flow_progress` custom-block adapter                          */
/* -------------------------------------------------------------------------- */

interface FlowProgressMirror {
  current_node?: unknown;
  label?: unknown;
  path?: unknown;
  disposition?: unknown;
}

/**
 * The raw flow mirror (agent `flow/runtime.py::publish_progress`) turned into
 * the same shape a `source="flow"` `steps` block gets: every id in `path` is
 * `"done"` except the current node, which is `"active"` (or `"done"` once
 * `disposition` is set, i.e. the flow reached an end node). Only the current
 * node's real label is known; earlier nodes fall back to their id, same as
 * the flow runtime does for an unlabelled node.
 */
export function stepsFromFlowProgress(raw: unknown): StepsBlockState {
  const mirror = (raw && typeof raw === "object" ? raw : {}) as FlowProgressMirror;
  const current = typeof mirror.current_node === "string" ? mirror.current_node : null;
  const finished = typeof mirror.disposition === "string" && mirror.disposition.length > 0;
  const path = Array.isArray(mirror.path) ? mirror.path.filter((id): id is string => typeof id === "string") : [];
  const ids = current && !path.includes(current) ? [...path, current] : path;
  const steps: StepItem[] = ids.map((id) => {
    const isCurrent = id === current;
    const label = isCurrent && typeof mirror.label === "string" && mirror.label ? mirror.label : id;
    const status: StepStatus = isCurrent ? (finished ? "done" : "active") : "done";
    return { id, label, status };
  });
  return { steps, current: finished ? null : current };
}
