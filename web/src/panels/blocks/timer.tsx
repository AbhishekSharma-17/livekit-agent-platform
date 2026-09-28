"use client";

/**
 * `timer` block — a countdown or a stopwatch the agent starts (`start_timer`,
 * V6-23, D-V6-20, ask #215). Read-only: neither `update_block` nor a caller
 * action can write it (`catalog.ts::UPDATABLE_BLOCK_TYPES` leaves it out).
 *
 * **The page counts on its own clock.** `duration_s` is the only number this
 * component trusts from the worker; the moment it first sees `status ===
 * "running"` for a given `started_at`, it stamps its own `Date.now()` and
 * counts down (or up) from there using `setInterval` — it never compares the
 * worker's `ends_at` against the browser's clock, because the two clocks
 * differ (ask #215). When the worker itself ends the timer (`timer_ended`)
 * or the agent stops it, `status` flips away from `"running"` and this
 * component simply stops ticking and shows the settled state.
 */
import * as React from "react";
import { useEffect, useRef, useState } from "react";

import { StatusChip } from "@/components/shared/status-chip";
import type { TimerBlockState } from "@/contracts/lkap-contracts";
import { PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

function formatDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.round(totalSeconds));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  const mm = String(minutes).padStart(2, "0");
  const ss = String(secs).padStart(2, "0");
  return hours > 0 ? `${hours}:${mm}:${ss}` : `${minutes}:${ss}`;
}

/** Counts on the page's own clock from `duration_s`, from the moment it first sees `running` for this `started_at`. */
function useLocalClock(status: TimerBlockState["status"], startedAt: number | null | undefined): number | null {
  const [localStart, setLocalStart] = useState<number | null>(() => (status === "running" ? Date.now() : null));
  const lastStartedAt = useRef<number | null | undefined>(startedAt);

  useEffect(() => {
    if (status === "running" && startedAt !== lastStartedAt.current) {
      setLocalStart(Date.now());
    } else if (status !== "running") {
      setLocalStart(null);
    }
    lastStartedAt.current = startedAt;
  }, [status, startedAt]);

  const [, tick] = useState(0);
  useEffect(() => {
    if (status !== "running") return;
    const id = setInterval(() => tick((n) => n + 1), 250);
    return () => clearInterval(id);
  }, [status]);

  return localStart;
}

export function TimerBlock({ spec, data, title, highlighted }: BlockRenderProps<TimerBlockState>) {
  const status = data.status ?? "idle";
  const mode = data.mode ?? "countdown";
  const durationS = data.duration_s ?? 0;
  const localStart = useLocalClock(status, data.started_at);

  const elapsedS = localStart !== null ? (Date.now() - localStart) / 1000 : 0;
  const seconds = mode === "elapsed" ? Math.min(elapsedS, durationS) : Math.max(durationS - elapsedS, 0);

  let body: React.ReactNode;
  if (status === "idle") {
    body = <PanelEmpty>No timer running.</PanelEmpty>;
  } else {
    body = (
      <div data-slot="block-timer" className="flex flex-col items-center gap-2 py-2">
        {data.label && <p className="text-muted-foreground text-sm">{data.label}</p>}
        <span
          role="timer"
          aria-label={`${mode === "elapsed" ? "Elapsed" : "Time remaining"}: ${formatDuration(seconds)}`}
          className="text-foreground font-mono text-4xl font-semibold tabular-nums"
        >
          {formatDuration(seconds)}
        </span>
        {status === "running" && (
          <StatusChip tone="info" size="sm" dot>
            {mode === "elapsed" ? "Counting up" : "Counting down"}
          </StatusChip>
        )}
        {status === "ended" && (
          <StatusChip tone="warning" size="sm" dot>
            Time&rsquo;s up
          </StatusChip>
        )}
        {status === "stopped" && (
          <StatusChip tone="neutral" size="sm" dot>
            Stopped
          </StatusChip>
        )}
      </div>
    );
  }

  return (
    <BlockFrame spec={spec} title={title} highlighted={highlighted}>
      {body}
    </BlockFrame>
  );
}

export default TimerBlock;
