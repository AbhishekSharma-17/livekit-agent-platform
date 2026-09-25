"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
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
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import type { ToolExecution } from "@/contracts/lkap-contracts";

/**
 * Background-tool execution policy for one built-in (BACKGROUND-TOOLS.md §2,
 * §7). Mirrors `http-tool-editor-dialog.tsx`'s draft shape (kept local, not
 * shared — the card grants this file but not a new shared module).
 */
type CancellableDraft = "default" | "true" | "false";
type DuplicateDraft = "default" | "allow" | "reject" | "replace" | "confirm";

export interface ExecutionDraft {
  mode: "blocking" | "background" | "auto";
  announce: string;
  auto_threshold_ms: number;
  fillersText: string;
  filler_delay_s: number;
  filler_interval_s: number;
  cancellable: CancellableDraft;
  on_duplicate: DuplicateDraft;
  max_duration_s: number;
}

const DEFAULT_EXECUTION_DRAFT: ExecutionDraft = {
  mode: "blocking",
  announce: "",
  auto_threshold_ms: 700,
  fillersText: "",
  filler_delay_s: 4,
  filler_interval_s: 8,
  cancellable: "default",
  on_duplicate: "default",
  max_duration_s: 60,
};

function fillersFromText(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(0, 5);
}

export function executionDraftFromValue(execution: ToolExecution | undefined): ExecutionDraft {
  if (!execution) return DEFAULT_EXECUTION_DRAFT;
  return {
    mode: execution.mode ?? "blocking",
    announce: execution.announce ?? "",
    auto_threshold_ms: execution.auto_threshold_ms ?? 700,
    fillersText: (execution.fillers ?? []).join("\n"),
    filler_delay_s: execution.filler_delay_s ?? 4,
    filler_interval_s: execution.filler_interval_s ?? 8,
    cancellable:
      execution.cancellable === null || execution.cancellable === undefined
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
    mode: draft.mode,
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

/**
 * The Tools tab's "Execution" chip on a backgroundable built-in
 * (`search_knowledge`, `http_request`, `describe_current_frame`;
 * `BACKGROUNDABLE_BUILTINS`). A controlled dialog: the caller owns
 * `config.tools.builtin_execution[name]` and hands it in as `value`, and
 * `onSave` receives the whole `ToolExecution` to write back — no side
 * drawers (R-V3-2), and no direct `react-hook-form` path-string wiring for a
 * dynamic dictionary key.
 */
export function BuiltinExecutionDialog({
  name,
  label,
  value,
  onSave,
  trigger,
}: {
  /** The built-in's tool name, e.g. `"search_knowledge"`. */
  name: string;
  /** Its display label, e.g. "Search knowledge". */
  label: string;
  value: ToolExecution | undefined;
  onSave: (execution: ToolExecution) => void;
  trigger: React.ReactNode;
}) {
  const uid = React.useId();
  const [open, setOpen] = React.useState(false);
  const [draft, setDraft] = React.useState<ExecutionDraft>(() => executionDraftFromValue(value));

  React.useEffect(() => {
    if (open) setDraft(executionDraftFromValue(value));
  }, [open, value]);

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    event.stopPropagation();
    onSave(executionFromDraft(draft));
    setOpen(false);
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent size="md" aria-describedby={`${uid}-description`} data-tool-name={name}>
        <form onSubmit={handleSubmit} className="flex min-h-0 flex-1 flex-col" noValidate>
          <DialogHeader>
            <DialogTitle>Execution — {label}</DialogTitle>
            <DialogDescription id={`${uid}-description`}>
              How &quot;{label}&quot; behaves while it runs (BACKGROUND-TOOLS.md §2). Overrides the agent&apos;s
              &quot;Read tools run&quot; default for this built-in only.
            </DialogDescription>
          </DialogHeader>

          <DialogBody className="gap-4">
            <Field label="Runs" htmlFor={`${uid}-mode`}>
              <Select
                value={draft.mode}
                onValueChange={(v) => setDraft((d) => ({ ...d, mode: v as ExecutionDraft["mode"] }))}
              >
                <SelectTrigger id={`${uid}-mode`} className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="blocking">Blocking — wait for the result</SelectItem>
                  <SelectItem value="background">In the background — always</SelectItem>
                  <SelectItem value="auto">Automatic — background only if slow</SelectItem>
                </SelectContent>
              </Select>
            </Field>

            {draft.mode !== "blocking" ? (
              <div className="grid gap-4 sm:grid-cols-2">
                <Field
                  label="What the agent says first"
                  htmlFor={`${uid}-announce`}
                  optional
                  hint='Its own words; default "Working on <name>."'
                >
                  <Input
                    id={`${uid}-announce`}
                    value={draft.announce}
                    onChange={(e) => setDraft((d) => ({ ...d, announce: e.target.value }))}
                  />
                </Field>
                {draft.mode === "auto" ? (
                  <Field label="Switches to background after" htmlFor={`${uid}-threshold`} hint="Milliseconds.">
                    <Input
                      id={`${uid}-threshold`}
                      type="number"
                      inputMode="numeric"
                      value={draft.auto_threshold_ms}
                      onChange={(e) => setDraft((d) => ({ ...d, auto_threshold_ms: Number(e.target.value) }))}
                    />
                  </Field>
                ) : null}
                <Field
                  label="Can be cancelled"
                  htmlFor={`${uid}-cancellable`}
                  hint="Default: yes for a read tool like this one."
                >
                  <Select
                    value={draft.cancellable}
                    onValueChange={(v) => setDraft((d) => ({ ...d, cancellable: v as CancellableDraft }))}
                  >
                    <SelectTrigger id={`${uid}-cancellable`} className="w-full">
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
                  htmlFor={`${uid}-duplicate`}
                  hint="Default: reject a repeat while one is already running."
                >
                  <Select
                    value={draft.on_duplicate}
                    onValueChange={(v) => setDraft((d) => ({ ...d, on_duplicate: v as DuplicateDraft }))}
                  >
                    <SelectTrigger id={`${uid}-duplicate`} className="w-full">
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
                <Field label="Give up after" htmlFor={`${uid}-max-duration`} hint="Seconds.">
                  <Input
                    id={`${uid}-max-duration`}
                    type="number"
                    inputMode="numeric"
                    value={draft.max_duration_s}
                    onChange={(e) => setDraft((d) => ({ ...d, max_duration_s: Number(e.target.value) }))}
                  />
                </Field>
              </div>
            ) : null}

            <Field
              label="Fillers while waiting"
              htmlFor={`${uid}-fillers`}
              optional
              hint="One phrase per line, up to five. Spoken as written; needs a voice."
            >
              <Textarea
                id={`${uid}-fillers`}
                rows={3}
                className="text-sm"
                value={draft.fillersText}
                onChange={(e) => setDraft((d) => ({ ...d, fillersText: e.target.value }))}
                placeholder={"Still checking.\nAlmost there."}
              />
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="First filler after" htmlFor={`${uid}-filler-delay`} hint="Seconds.">
                <Input
                  id={`${uid}-filler-delay`}
                  type="number"
                  inputMode="numeric"
                  value={draft.filler_delay_s}
                  onChange={(e) => setDraft((d) => ({ ...d, filler_delay_s: Number(e.target.value) }))}
                />
              </Field>
              <Field label="Then every" htmlFor={`${uid}-filler-interval`} hint="Seconds.">
                <Input
                  id={`${uid}-filler-interval`}
                  type="number"
                  inputMode="numeric"
                  value={draft.filler_interval_s}
                  onChange={(e) => setDraft((d) => ({ ...d, filler_interval_s: Number(e.target.value) }))}
                />
              </Field>
            </div>
          </DialogBody>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit">Save</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
