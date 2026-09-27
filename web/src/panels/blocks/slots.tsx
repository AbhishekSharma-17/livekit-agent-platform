"use client";

/**
 * `slots` block — bookable times the caller taps (`request_slot` /
 * `resolve_slot`, `SlotsBlockState`, V5-43 → V5-44, `docs/v5/_asks.md`
 * #310).
 *
 * Grouped by calendar day in `data.timezone` (the caller's own zone, or the
 * business's with `timezone_mode="agent"` — the worker already resolved
 * which one and put it in the state, so this only ever reads `data.timezone`
 * and never the config's `timezone_mode` for the grouping itself). A tap
 * answers `block_submit {values: {selected: <slot id>}}` through
 * `useBlockRequest`, the same pending/answered lifecycle `choices` uses: the
 * worker reads only `selected` and takes `start`/`end` from its own `slots`,
 * never from the browser (`SlotsBlockState`'s own docstring) — so this
 * never sends `start`/`end` either. A full slot (`capacity === 0`) is shown
 * disabled, never omitted (the caller should see it was offered and taken).
 */
import * as React from "react";

import { StatusChip } from "@/components/shared/status-chip";
import { Button } from "@/components/ui/button";
import type { SlotsBlockState, TimeSlot } from "@/contracts/lkap-contracts";
import { formatTime } from "@/lib/format";
import { useBlockRequest } from "@/panels/composite/use-block-request";
import { PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

/** `Intl.DateTimeFormat` throws on an unrecognised zone; fall back to the viewer's own. */
function safeTimeZone(timezone: string | null | undefined): string | undefined {
  if (!timezone) return undefined;
  try {
    new Intl.DateTimeFormat("en-GB", { timeZone: timezone });
    return timezone;
  } catch {
    return undefined;
  }
}

/** Sortable `YYYY-MM-DD` day key of `iso` in `timeZone` — groups slots by calendar day, not by UTC day. */
function dayKeyOf(iso: string, timeZone: string | undefined): string {
  return new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", timeZone }).format(
    new Date(iso),
  );
}

/** "Monday 5 October" for a day heading. */
function dayHeadingOf(iso: string, timeZone: string | undefined): string {
  return new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long", timeZone }).format(
    new Date(iso),
  );
}

interface DayGroup {
  key: string;
  heading: string;
  slots: TimeSlot[];
}

/** `slots` grouped by calendar day, in first-seen day order, capped at `maxDays`. */
function groupByDay(slots: TimeSlot[], timeZone: string | undefined, maxDays: number): DayGroup[] {
  const byKey = new Map<string, DayGroup>();
  for (const slot of slots) {
    const key = dayKeyOf(slot.start, timeZone);
    let group = byKey.get(key);
    if (!group) {
      if (byKey.size >= maxDays) continue;
      group = { key, heading: dayHeadingOf(slot.start, timeZone), slots: [] };
      byKey.set(key, group);
    }
    group.slots.push(slot);
  }
  return [...byKey.values()];
}

function daysVisibleOf(config: unknown): number {
  const value = (config as { days_visible?: unknown } | null)?.days_visible;
  return typeof value === "number" && value >= 1 ? value : 7;
}

function allowCustomOf(config: unknown): boolean {
  return (config as { allow_custom?: unknown } | null)?.allow_custom === true;
}

function SlotButton({
  slot,
  selected,
  disabled,
  timeZone,
  onPick,
}: {
  slot: TimeSlot;
  selected: boolean;
  disabled: boolean;
  timeZone: string | undefined;
  onPick: () => void;
}) {
  const full = slot.capacity === 0;
  const timeLabel = `${formatTime(slot.start, { timeZone })} – ${formatTime(slot.end, { timeZone })}`;
  return (
    <Button
      type="button"
      variant={selected ? "default" : "outline"}
      size="sm"
      disabled={disabled || full}
      aria-pressed={selected}
      title={slot.label ?? undefined}
      onClick={onPick}
      className="flex-col items-start gap-0 py-1.5"
    >
      <span>{timeLabel}</span>
      {full ? <span className="text-[0.6875rem] font-normal opacity-80">Full</span> : slot.label ? (
        <span className="text-[0.6875rem] font-normal opacity-80">{slot.label}</span>
      ) : null}
    </Button>
  );
}

function SlotsEditor({
  blockId,
  slots,
  timeZone,
  allowCustom,
  maxDays,
  perform,
}: {
  blockId: string;
  slots: TimeSlot[];
  timeZone: string | undefined;
  allowCustom: boolean;
  maxDays: number;
  perform: BlockRenderProps["panel"]["perform"];
}) {
  const { sending, error, submit } = useBlockRequest(blockId, "requested", perform);
  const busy = sending !== null;
  const groups = React.useMemo(() => groupByDay(slots, timeZone, maxDays), [slots, timeZone, maxDays]);

  function pick(slot: TimeSlot) {
    if (busy || slot.capacity === 0) return;
    void submit({ selected: slot.id });
  }

  return (
    <div data-slot="block-slots" className="flex flex-col gap-3">
      {groups.length === 0 ? (
        <PanelEmpty>No times to show yet.</PanelEmpty>
      ) : (
        groups.map((group) => (
          <div key={group.key} className="flex flex-col gap-1.5">
            <h4 className="text-muted-foreground text-[0.8125rem] font-medium">{group.heading}</h4>
            <div className="flex flex-wrap gap-2" role="group" aria-label={group.heading}>
              {group.slots.map((slot) => (
                <SlotButton
                  key={slot.id}
                  slot={slot}
                  selected={false}
                  disabled={busy}
                  timeZone={timeZone}
                  onPick={() => pick(slot)}
                />
              ))}
            </div>
          </div>
        ))
      )}
      {error && (
        <p role="alert" className="text-danger-text text-[0.8125rem]">
          {error}
        </p>
      )}
      {allowCustom && (
        <p className="text-muted-foreground text-[0.8125rem]">Or tell the agent another time.</p>
      )}
    </div>
  );
}

export function SlotsBlock({ spec, data, panel, title, highlighted }: BlockRenderProps<SlotsBlockState>) {
  const status = data.status ?? "idle";
  const slots = React.useMemo(() => (Array.isArray(data.slots) ? data.slots : []), [data.slots]);
  const timeZone = safeTimeZone(data.timezone);
  const maxDays = daysVisibleOf(spec.config);
  const allowCustom = allowCustomOf(spec.config);
  const requestKey = React.useMemo(() => JSON.stringify([data.prompt ?? "", slots.map((s) => s.id)]), [data.prompt, slots]);
  const selectedSlot = data.selected ? slots.find((slot) => slot.id === data.selected) : undefined;

  let body: React.ReactNode;
  if (status === "requested") {
    body = (
      <SlotsEditor
        key={requestKey}
        blockId={spec.id}
        slots={slots}
        timeZone={timeZone}
        allowCustom={allowCustom}
        maxDays={maxDays}
        perform={panel.perform}
      />
    );
  } else if (status === "cancelled") {
    body = <PanelEmpty>You dismissed this without picking a time.</PanelEmpty>;
  } else if (status === "submitted") {
    body = selectedSlot ? (
      <StatusChip tone="success" size="sm" dot>
        {dayHeadingOf(selectedSlot.start, timeZone)} · {formatTime(selectedSlot.start, { timeZone })} –{" "}
        {formatTime(selectedSlot.end, { timeZone })}
      </StatusChip>
    ) : (
      <StatusChip tone="neutral" size="sm">
        No time recorded
      </StatusChip>
    );
  } else {
    body = <PanelEmpty>The agent will offer times here when there are some to pick.</PanelEmpty>;
  }

  return (
    <BlockFrame spec={spec} title={title} highlighted={highlighted}>
      {typeof data.prompt === "string" && data.prompt && <p className="mb-2.5 text-sm font-medium">{data.prompt}</p>}
      {timeZone && status === "requested" && (
        <p className="text-muted-foreground mb-2 text-[0.75rem]">Times shown in {timeZone}.</p>
      )}
      {body}
    </BlockFrame>
  );
}

export default SlotsBlock;
