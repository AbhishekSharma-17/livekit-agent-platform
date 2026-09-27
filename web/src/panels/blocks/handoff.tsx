"use client";

/**
 * `handoff` block — where the hand-off of the caller to a person stands
 * (CONTRACTS-V2 §4.4, V5-32 → V5-36): `transfer_call` writes
 * `HandoffBlockState` as it runs, `requested → connecting → connected |
 * timeout | ended`. On a phone call the caller never sees a screen, but the
 * block still carries the state — a Live tab (V5-38) mirrors it, and this
 * renderer covers the browser/text channels and the console's own preview.
 *
 * No LiveKit bindings here (unlike `video`/`upload`/`captions`), so this
 * block is not in `LAZY_BLOCK_TYPES`.
 *
 * `mode`/`target`/`agent_name` are read straight off the wire — never typed
 * from config — because they describe what actually happened
 * (`HandoffBlockState`'s own docstring: a warm request that cannot run warm
 * still reports `mode: "cold"`). `queue_position` and `agent_name` are only
 * ever shown when the block's own config asks for them
 * (`show_queue`/`show_agent_name`) *and* the worker has filled them in.
 */
import * as React from "react";

import { StatusChip, type StatusTone } from "@/components/shared/status-chip";
import type { HandoffBlockState } from "@/contracts/lkap-contracts";
import { PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

interface HandoffConfig {
  showQueue: boolean;
  showAgentName: boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** `BlockSpec.config` for a `handoff` block (`HandoffBlockConfig`); both fields default on. */
function configOf(config: unknown): HandoffConfig {
  const record = isRecord(config) ? config : {};
  return {
    showQueue: record.show_queue !== false,
    showAgentName: record.show_agent_name !== false,
  };
}

type HandoffStatus = NonNullable<HandoffBlockState["status"]>;

const STATUS_LABEL: Record<HandoffStatus, string> = {
  idle: "Not started",
  requested: "Handing over",
  connecting: "Connecting",
  connected: "Connected",
  timeout: "Not connected",
  ended: "Handed over",
};

const STATUS_TONE: Record<HandoffStatus, StatusTone> = {
  idle: "neutral",
  requested: "info",
  connecting: "info",
  connected: "success",
  timeout: "warning",
  ended: "neutral",
};

/** The plain-words line a caller (or a supervisor mirroring the state) reads for each status. */
function statusText(data: HandoffBlockState, config: HandoffConfig): string {
  const target = data.target ?? "a person";
  switch (data.status) {
    case "requested":
      // `escalate_to_human` writes a plain, fixed line per mode into `reason`
      // (mode stays null there — ask #212/#250 item 3); `transfer_call` never
      // sets `reason` at `requested`, so its wording is unchanged (ask #254).
      return data.reason ?? `Handing you over to ${target}.`;
    case "connecting":
      return data.mode === "warm" ? `Calling ${target} first…` : `Connecting you to ${target}…`;
    case "connected": {
      const who = config.showAgentName && data.agent_name ? data.agent_name : target;
      return `You're talking to ${who}.`;
    }
    case "timeout":
      return `${data.reason ?? "Nobody answered."} You're back with the agent.`;
    case "ended":
      return "Your call was handed over.";
    case "idle":
    default:
      return "";
  }
}

/** A plain queue line, shown only while waiting and when the config and the data both allow it. */
function queueText(data: HandoffBlockState, config: HandoffConfig): string | null {
  if (!config.showQueue) return null;
  if (data.status !== "requested" && data.status !== "connecting") return null;
  if (data.queue_position === null || data.queue_position === undefined) return null;
  return data.queue_position === 0 ? "You're next in the queue." : `You're number ${data.queue_position} in the queue.`;
}

export function HandoffBlock({ spec, data, title, highlighted }: BlockRenderProps<HandoffBlockState>) {
  const config = configOf(spec.config);
  const status: HandoffStatus = data.status ?? "idle";
  const text = statusText(data, config);
  const queue = queueText(data, config);

  return (
    <BlockFrame spec={spec} title={title} highlighted={highlighted}>
      <div data-slot="block-handoff" className="flex flex-col gap-2">
        {status === "idle" ? (
          <PanelEmpty>The agent will show you here if it hands you over to a person.</PanelEmpty>
        ) : (
          <>
            <StatusChip tone={STATUS_TONE[status]} size="sm" dot>
              {STATUS_LABEL[status]}
            </StatusChip>
            {/* One persistent live region whose text changes with `status` — a region that
                is conditionally mounted per state announces nothing. */}
            <p role="status" aria-live="polite" className="text-sm leading-relaxed">
              {text}
            </p>
            {queue ? <p className="text-muted-foreground text-[0.8125rem]">{queue}</p> : null}
          </>
        )}
      </div>
    </BlockFrame>
  );
}

export default HandoffBlock;
