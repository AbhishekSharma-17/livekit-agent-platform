"use client";

/**
 * `choices` block — quick replies the caller taps *or* answers by voice
 * (`request_choice` / `resolve_choice`, CONTRACTS-V2 §4.4, V5-08 → V5-12).
 *
 * Renders straight from state, like `form` (V5-03): `status: "requested"` is
 * the pending marker, and the answer goes back through `useBlockRequest`
 * (`block_submit {block_id, values: {selected: [...]}}`, or
 * `{block_id, cancelled: true}`). A single-answer question submits the
 * moment an option is tapped — the same "say it or tap it" answer either
 * way; a multi-answer question collects taps first and sends them together.
 * A spoken answer (`resolve_choice`) flips `status` to `"submitted"` the same
 * way, with no local `submit()` call, so the "submitted" branch below reads
 * correctly whichever channel answered.
 */
import * as React from "react";
import { useMemo, useState } from "react";

import { StatusChip, type StatusTone } from "@/components/shared/status-chip";
import { Button } from "@/components/ui/button";
import type { ChoiceOption, ChoiceReveal, ChoicesBlockState } from "@/contracts/lkap-contracts";
import { cn } from "@/lib/utils";
import { useBlockRequest } from "@/panels/composite/use-block-request";
import { PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

type ChoiceTone = NonNullable<ChoiceOption["tone"]>;

/** Small dot, never a side stripe (UI_UX_SPEC §2.1). */
const TONE_DOT: Record<ChoiceTone, string> = {
  neutral: "bg-muted-foreground",
  info: "bg-info",
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
};

type ChoiceLayout = "buttons" | "list" | "chips";

function layoutOf(config: unknown): ChoiceLayout {
  const layout = (config as { layout?: unknown } | null)?.layout;
  return layout === "list" || layout === "chips" ? layout : "buttons";
}

const CONTAINER_CLASS: Record<ChoiceLayout, string> = {
  buttons: "flex flex-wrap gap-2",
  list: "flex flex-col gap-2",
  chips: "flex flex-wrap gap-1.5",
};

function OptionButton({
  option,
  layout,
  selected,
  disabled,
  onToggle,
  thumb,
}: {
  option: ChoiceOption;
  layout: ChoiceLayout;
  selected: boolean;
  disabled: boolean;
  onToggle: () => void;
  thumb: string | undefined;
}) {
  const dot = option.tone ? (
    <span aria-hidden="true" className={cn("size-1.5 shrink-0 rounded-full", TONE_DOT[option.tone])} />
  ) : null;
  const image = thumb ? (
    // eslint-disable-next-line @next/next/no-img-element -- a resolved session-asset blob URL
    <img src={thumb} alt="" aria-hidden="true" className="size-4 shrink-0 rounded-full object-cover" />
  ) : null;

  if (layout === "chips") {
    return (
      <button
        type="button"
        title={option.hint ?? undefined}
        aria-pressed={selected}
        disabled={disabled}
        onClick={onToggle}
        className={cn(
          "focus-visible:ring-ring inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm transition-colors focus-visible:ring-2 focus-visible:outline-none disabled:opacity-50",
          selected ? "border-brand bg-brand-soft text-brand-text" : "border-border hover:bg-muted",
        )}
      >
        {dot}
        {image}
        {option.label}
      </button>
    );
  }
  return (
    <Button
      type="button"
      variant={selected ? "default" : "outline"}
      size="sm"
      title={option.hint ?? undefined}
      aria-pressed={selected}
      disabled={disabled}
      onClick={onToggle}
      className={cn("gap-1.5", layout === "list" && "w-full justify-start")}
    >
      {dot}
      {image}
      {option.label}
    </Button>
  );
}

/**
 * The editable question; remounted (fresh draft **and** fresh
 * `useBlockRequest`) per question, keyed by its prompt and options
 * (`ChoicesBlock`'s `requestKey`) — the same trick `FormEditor` uses.
 */
function ChoicesEditor({
  blockId,
  prompt,
  options,
  multi,
  layout,
  perform,
  thumbOf,
}: {
  blockId: string;
  prompt: string;
  options: ChoiceOption[];
  multi: boolean;
  layout: ChoiceLayout;
  perform: BlockRenderProps["panel"]["perform"];
  thumbOf: (assetId: string | null | undefined) => string | undefined;
}) {
  const [draft, setDraft] = useState<string[]>([]);
  const { sending, error, submit, cancel } = useBlockRequest(blockId, "requested", perform);
  const busy = sending !== null;

  function toggle(id: string) {
    if (busy) return;
    if (!multi) {
      void submit({ selected: [id] });
      return;
    }
    setDraft((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }

  function onSend() {
    if (busy || draft.length === 0) return;
    void submit({ selected: draft });
  }

  function onCancel() {
    if (busy) return;
    void cancel();
  }

  return (
    <div data-slot="block-choices" className="flex flex-col gap-3">
      <div className={CONTAINER_CLASS[layout]} role="group" aria-label={prompt || "Choices"}>
        {options.map((option) => (
          <OptionButton
            key={option.id}
            option={option}
            layout={layout}
            selected={multi && draft.includes(option.id)}
            disabled={busy}
            onToggle={() => toggle(option.id)}
            thumb={thumbOf(option.image_asset_id)}
          />
        ))}
      </div>
      {error && (
        <p role="alert" className="text-danger-text text-[0.8125rem]">
          {error}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {multi && (
          <Button type="button" size="sm" onClick={onSend} disabled={busy || draft.length === 0}>
            {sending === "submit" ? "Sending…" : "Send"}
          </Button>
        )}
        <Button type="button" size="sm" variant="ghost" onClick={onCancel} disabled={busy}>
          Not now
        </Button>
      </div>
    </div>
  );
}

function SubmittedAnswer({
  options,
  selected,
  reveal,
}: {
  options: ChoiceOption[];
  selected: string[];
  reveal: ChoiceReveal | null;
}) {
  const byId = useMemo(() => new Map(options.map((option) => [option.id, option])), [options]);
  const correct = reveal?.correct ?? [];
  // ask #43: a `block_submit` whose ids are not options of the block is not
  // stored — the block reads `status: "submitted"` with `selected: []`.
  if (selected.length === 0) {
    return (
      <StatusChip tone="neutral" size="sm">
        No answer recorded
      </StatusChip>
    );
  }
  return (
    <div data-slot="block-choices-submitted" className="flex flex-col gap-2.5">
      <ul className="flex flex-wrap gap-2">
        {selected.map((id) => {
          const isCorrect = reveal ? correct.includes(id) : null;
          const tone: StatusTone = isCorrect === false ? "danger" : "success";
          return (
            <li key={id}>
              <StatusChip tone={tone} size="sm" dot>
                {byId.get(id)?.label ?? id}
              </StatusChip>
            </li>
          );
        })}
      </ul>
      {reveal && (
        <div className="text-sm">
          {correct.length > 0 && (
            <p className="text-muted-foreground">
              Correct answer{correct.length > 1 ? "s" : ""}:{" "}
              {correct.map((id) => byId.get(id)?.label ?? id).join(", ")}
            </p>
          )}
          {reveal.explanation && <p className="mt-1">{reveal.explanation}</p>}
        </div>
      )}
    </div>
  );
}

export function ChoicesBlock({ spec, data, panel, title, highlighted }: BlockRenderProps<ChoicesBlockState>) {
  const status = data.status ?? "idle";
  const options = Array.isArray(data.options) ? data.options : [];
  const selected = Array.isArray(data.selected) ? data.selected : [];
  const multi = data.multi === true;
  const layout = layoutOf(spec.config);
  // A new question (new prompt or options) remounts the editor with a fresh draft.
  const requestKey = useMemo(() => JSON.stringify([data.prompt ?? "", data.options ?? []]), [data.prompt, data.options]);
  const thumbOf = React.useCallback(
    (assetId: string | null | undefined) => (assetId ? panel.assets.get(assetId) : undefined),
    [panel.assets],
  );

  let body: React.ReactNode;
  if (status === "requested") {
    body = (
      <ChoicesEditor
        key={requestKey}
        blockId={spec.id}
        prompt={typeof data.prompt === "string" ? data.prompt : ""}
        options={options}
        multi={multi}
        layout={layout}
        perform={panel.perform}
        thumbOf={thumbOf}
      />
    );
  } else if (status === "cancelled") {
    body = <PanelEmpty>You dismissed this without answering.</PanelEmpty>;
  } else if (status === "submitted") {
    body = <SubmittedAnswer options={options} selected={selected} reveal={data.reveal ?? null} />;
  } else {
    body = <PanelEmpty>The agent will ask a question here when it has one.</PanelEmpty>;
  }

  return (
    <BlockFrame spec={spec} title={title} highlighted={highlighted}>
      {typeof data.prompt === "string" && data.prompt && <p className="mb-2.5 text-sm font-medium">{data.prompt}</p>}
      {body}
    </BlockFrame>
  );
}
