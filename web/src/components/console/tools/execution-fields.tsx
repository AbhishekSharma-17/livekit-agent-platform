"use client";

import * as React from "react";

import { Field } from "@/components/shared/field";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { ToolExecution } from "@/contracts/lkap-contracts";

/**
 * Shared execution controls (R-V5-8, docs/v5/PLAN-V5.md V5-50): extracted from
 * V4-13's `http-tool-editor-dialog.tsx` so `provider-tool-editor-dialog.tsx`
 * (an app action, docs/v5/COMPOSIO.md D-V5-C8) renders the exact same "Runs /
 * what the agent says first / fillers / cancellable / repeated calls / give
 * up after" controls, posting the same `ToolExecution` shape. Field labels,
 * hints and `${uid}-execution-*` ids are unchanged from before the
 * extraction — `console-http-tool-editor.test.tsx` binds to them by label
 * text and must keep passing unmodified.
 *
 * Background-tool execution policy. The contract's `mode`/`cancellable`/
 * `on_duplicate` are nullable ("agent decides"); the editor spells each out
 * as an explicit "Default" option rather than guessing a value, so an
 * untouched tool keeps posting `null` and a GET (or read) tool keeps
 * inheriting the agent's "Read tools run" setting (a tool that changes
 * something still always blocks unless a mode is chosen explicitly).
 */
export type ModeDraft = "default" | "blocking" | "background" | "auto";
export type CancellableDraft = "default" | "true" | "false";
export type DuplicateDraft = "default" | "allow" | "reject" | "replace" | "confirm";

export interface ExecutionDraft {
  mode: ModeDraft;
  announce: string;
  auto_threshold_ms: number;
  /** One filler phrase per line; ≤ 5 lines kept (`ToolExecution.fillers`, `max_length=5`). */
  fillersText: string;
  filler_delay_s: number;
  filler_interval_s: number;
  cancellable: CancellableDraft;
  on_duplicate: DuplicateDraft;
  max_duration_s: number;
}

export const DEFAULT_EXECUTION_DRAFT: ExecutionDraft = {
  mode: "default",
  announce: "",
  auto_threshold_ms: 700,
  fillersText: "",
  filler_delay_s: 4,
  filler_interval_s: 8,
  cancellable: "default",
  on_duplicate: "default",
  max_duration_s: 60,
};

export function fillersFromText(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(0, 5);
}

export function executionDraftFromValue(execution: ToolExecution | undefined): ExecutionDraft {
  if (!execution) return DEFAULT_EXECUTION_DRAFT;
  return {
    mode: execution.mode ?? "default",
    announce: execution.announce ?? "",
    auto_threshold_ms: execution.auto_threshold_ms ?? 700,
    fillersText: (execution.fillers ?? []).join("\n"),
    filler_delay_s: execution.filler_delay_s ?? 4,
    filler_interval_s: execution.filler_interval_s ?? 8,
    cancellable: execution.cancellable === null || execution.cancellable === undefined
      ? "default"
      : execution.cancellable
        ? "true"
        : "false",
    on_duplicate: execution.on_duplicate ?? "default",
    max_duration_s: execution.max_duration_s ?? 60,
  };
}

export function executionFromDraft(draft: ExecutionDraft): ToolExecution {
  return {
    mode: draft.mode === "default" ? null : draft.mode,
    announce: draft.announce.trim() === "" ? null : draft.announce,
    auto_threshold_ms: draft.auto_threshold_ms,
    fillers: fillersFromText(draft.fillersText) as ToolExecution["fillers"],
    filler_delay_s: draft.filler_delay_s,
    filler_interval_s: draft.filler_interval_s,
    cancellable: draft.cancellable === "default" ? null : draft.cancellable === "true",
    on_duplicate: draft.on_duplicate === "default" ? null : draft.on_duplicate,
    duplicate_scope: "name_and_args",
    max_duration_s: draft.max_duration_s,
  };
}

/** An explicit choice of "In the background" or "Automatic" — not "Default" (inherits the agent setting) or "Blocking". */
export function isNonBlocking(mode: ModeDraft): boolean {
  return mode === "background" || mode === "auto";
}

/** The api validator's exact wording (`config_service.py::tool_execution_issues`). */
export function silentReplyConflictMessage(name: string): string {
  return `'${name || "this tool"}' has silent_reply on, which would swallow its background announcement; turn one of them off`;
}

/**
 * The "Runs" select alone — kept separate from `ExecutionFields` below
 * because the HTTP editor places it inside its own "Response" grid
 * (alongside `max_result_chars`/`result_path`/`silent_reply`, which aren't
 * shared), while the provider editor places it in its own layout.
 */
export function RunsField({
  uid,
  mode,
  onChange,
  isRead,
  lockBlocking = false,
  errorMessage,
}: {
  uid: string;
  mode: ModeDraft;
  onChange: (mode: ModeDraft) => void;
  /** A GET HTTP tool, or a `read`-risk app action: "Agent default" follows the agent's "Read tools run" setting. */
  isRead: boolean;
  /**
   * A destructive app action always runs blocking (the contract's
   * `_silent_reply_blocks` rejects a non-blocking mode with a 422) — hide
   * the two non-blocking options rather than let Save fail.
   */
  lockBlocking?: boolean;
  errorMessage?: string;
}) {
  return (
    <Field
      label="Runs"
      htmlFor={`${uid}-execution-mode`}
      error={errorMessage}
      hint={
        lockBlocking
          ? "This action can't be undone, so it always blocks and can't be cancelled."
          : isRead
            ? 'Left at "Agent default", this follows the agent\'s "Read tools run" setting (Instructions & voice → Conversation).'
            : 'This tool changes something, so "Agent default" always blocks; choose a mode below to change that.'
      }
    >
      <Select value={mode} onValueChange={(v) => onChange(v as ModeDraft)}>
        <SelectTrigger id={`${uid}-execution-mode`} className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="default">Agent default</SelectItem>
          <SelectItem value="blocking">Blocking</SelectItem>
          {!lockBlocking ? <SelectItem value="background">In the background</SelectItem> : null}
          {!lockBlocking ? <SelectItem value="auto">Automatic</SelectItem> : null}
        </SelectContent>
      </Select>
    </Field>
  );
}

/**
 * The rest of the execution controls (docs/v4/BACKGROUND-TOOLS.md §7): the
 * explanatory copy, the non-blocking-only fields (announce / threshold /
 * cancellable / repeated calls / give up after) and the fillers group. Does
 * not render "Runs" itself — see `RunsField` above.
 */
export function ExecutionFields({
  uid,
  draft,
  onChange,
  isRead,
}: {
  uid: string;
  draft: ExecutionDraft;
  onChange: (next: ExecutionDraft) => void;
  isRead: boolean;
}) {
  function set<K extends keyof ExecutionDraft>(key: K, value: ExecutionDraft[K]) {
    onChange({ ...draft, [key]: value });
  }

  return (
    <>
      <p className="text-[0.8125rem] text-muted-foreground">
        How this tool behaves while it runs. &quot;Blocking&quot; waits for the result before the agent
        replies; the other modes let the agent keep talking.
      </p>
      {!isRead ? (
        <p className="text-[0.8125rem] text-muted-foreground">
          This tool changes something; the agent asks before running it twice.
        </p>
      ) : null}
      {isNonBlocking(draft.mode) ? (
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label="What the agent says first"
            htmlFor={`${uid}-execution-announce`}
            optional
            hint='Its own words; default "Working on <name>."'
          >
            <Input
              id={`${uid}-execution-announce`}
              value={draft.announce}
              onChange={(e) => set("announce", e.target.value)}
              placeholder="Fetching that now."
            />
          </Field>
          {draft.mode === "auto" ? (
            <Field
              label="Switches to background after"
              htmlFor={`${uid}-execution-threshold`}
              hint="Milliseconds."
            >
              <Input
                id={`${uid}-execution-threshold`}
                type="number"
                inputMode="numeric"
                value={draft.auto_threshold_ms}
                onChange={(e) => set("auto_threshold_ms", Number(e.target.value))}
              />
            </Field>
          ) : null}
          <Field
            label="Can be cancelled"
            htmlFor={`${uid}-execution-cancellable`}
            hint="Default: read tools can be cancelled; tools that change something can't."
          >
            <Select
              value={draft.cancellable}
              onValueChange={(v) => set("cancellable", v as ExecutionDraft["cancellable"])}
            >
              <SelectTrigger id={`${uid}-execution-cancellable`} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="default">Default</SelectItem>
                <SelectItem value="true">Yes</SelectItem>
                <SelectItem value="false">No</SelectItem>
              </SelectContent>
            </Select>
          </Field>
          <Field
            label="Repeated calls"
            htmlFor={`${uid}-execution-duplicate`}
            hint="Default: reject a repeat of a read tool already running; ask again before repeating anything that changes something."
          >
            <Select
              value={draft.on_duplicate}
              onValueChange={(v) => set("on_duplicate", v as ExecutionDraft["on_duplicate"])}
            >
              <SelectTrigger id={`${uid}-execution-duplicate`} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="default">Default</SelectItem>
                <SelectItem value="reject">Reject the repeat</SelectItem>
                <SelectItem value="confirm">Ask again to confirm</SelectItem>
                <SelectItem value="replace">Replace the running call</SelectItem>
                <SelectItem value="allow">Allow it</SelectItem>
              </SelectContent>
            </Select>
          </Field>
          <Field label="Give up after" htmlFor={`${uid}-execution-max-duration`} hint="Seconds.">
            <Input
              id={`${uid}-execution-max-duration`}
              type="number"
              inputMode="numeric"
              value={draft.max_duration_s}
              onChange={(e) => set("max_duration_s", Number(e.target.value))}
            />
          </Field>
        </div>
      ) : null}
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <Field
            label="Fillers while waiting"
            htmlFor={`${uid}-execution-fillers`}
            optional
            hint="One phrase per line, up to five. Spoken as written; needs a voice."
          >
            <Textarea
              id={`${uid}-execution-fillers`}
              rows={3}
              className="text-sm"
              value={draft.fillersText}
              onChange={(e) => set("fillersText", e.target.value)}
              placeholder={"Still checking.\nAlmost there."}
            />
          </Field>
        </div>
        <Field label="First filler after" htmlFor={`${uid}-execution-filler-delay`} hint="Seconds.">
          <Input
            id={`${uid}-execution-filler-delay`}
            type="number"
            inputMode="numeric"
            value={draft.filler_delay_s}
            onChange={(e) => set("filler_delay_s", Number(e.target.value))}
          />
        </Field>
        <Field label="Then every" htmlFor={`${uid}-execution-filler-interval`} hint="Seconds.">
          <Input
            id={`${uid}-execution-filler-interval`}
            type="number"
            inputMode="numeric"
            value={draft.filler_interval_s}
            onChange={(e) => set("filler_interval_s", Number(e.target.value))}
          />
        </Field>
      </div>
    </>
  );
}
