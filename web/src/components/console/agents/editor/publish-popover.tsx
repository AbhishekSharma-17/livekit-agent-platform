"use client";

import * as React from "react";
import { toast } from "sonner";
import { CircleAlertIcon, CircleCheckIcon, ExternalLinkIcon, LoaderCircleIcon, TriangleAlertIcon } from "lucide-react";

import { CopyButton } from "@/components/shared/copy-button";
import { Icon } from "@/components/shared/icon";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useUpdateAgent, useValidateAgent } from "@/components/console/lib/api-hooks";
import { errorMessage } from "@/components/console/shared/error-banner";
import type { AgentOut, ValidationResult } from "@/contracts/lkap-contracts";
import { pluralize } from "@/lib/format";
import { cn } from "@/lib/utils";

import { validationMessages } from "./validation-map";

/** What the shell's save returns to the header actions. */
export interface SaveOutcome {
  agent: AgentOut;
  validation: ValidationResult | null;
}

/** `https://host/s/<slug>` (or a relative path before the window exists). */
export function publicUrl(slug: string, test = false): string {
  const path = `/s/${slug}${test ? "?mode=test" : ""}`;
  return typeof window === "undefined" ? path : `${window.location.origin}${path}`;
}

type CheckState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "done"; result: ValidationResult }
  | { status: "failed"; message: string };

export interface PublishControlProps {
  agent: AgentOut;
  dirty: boolean;
  /** Saves the form (PUT + validate). Resolves null when the save failed (the shell already reported why). */
  saveNow: () => Promise<SaveOutcome | null>;
  /** Hands a fresh validation result to the shell so the section dots update. */
  onValidated: (result: ValidationResult) => void;
  /** Moves the editor to the first section with an error. */
  goToFirstIssue: () => void;
}

const MAX_LISTED = 3;

/**
 * Publish / Unpublish (docs/UI_UX_SPEC.md §4.9). Draft: a popover that
 * validates the *saved* config on open (dirty forms first choose "Save and
 * publish" or "Publish the saved version"); errors block, warnings need
 * "Publish anyway". Live: the URL, copy, "Open page", and "Unpublish" behind
 * a confirmation dialog rendered as a sibling of the popover.
 */
export function PublishControl({ agent, dirty, saveNow, onValidated, goToFirstIssue }: PublishControlProps) {
  const updateAgent = useUpdateAgent(agent.id);
  const validateAgent = useValidateAgent(agent.id);
  const [open, setOpen] = React.useState(false);
  const [step, setStep] = React.useState<"unsaved" | "review">("review");
  const [check, setCheck] = React.useState<CheckState>({ status: "idle" });
  const [saving, setSaving] = React.useState(false);
  const [confirmUnpublish, setConfirmUnpublish] = React.useState(false);

  const url = publicUrl(agent.slug);

  async function runCheck() {
    setCheck({ status: "loading" });
    try {
      const result = await validateAgent.mutateAsync();
      onValidated(result);
      setCheck({ status: "done", result });
      return result;
    } catch (error) {
      setCheck({ status: "failed", message: errorMessage(error) });
      return null;
    }
  }

  async function publish() {
    try {
      await updateAgent.mutateAsync({ published: true });
      toast.success("Published");
      setOpen(false);
    } catch (error) {
      toast.error(`Couldn't publish — ${errorMessage(error)}`);
    }
  }

  async function unpublish() {
    try {
      await updateAgent.mutateAsync({ published: false });
      toast.success("Unpublished");
      setConfirmUnpublish(false);
    } catch (error) {
      toast.error(`Couldn't unpublish — ${errorMessage(error)}`);
    }
  }

  function onOpenChange(next: boolean) {
    setOpen(next);
    if (!next || agent.published) return;
    if (dirty) {
      setStep("unsaved");
      setCheck({ status: "idle" });
    } else {
      setStep("review");
      void runCheck();
    }
  }

  async function saveAndPublish() {
    setSaving(true);
    try {
      const outcome = await saveNow();
      if (!outcome) {
        setOpen(false);
        return;
      }
      setStep("review");
      const result = outcome.validation ?? (await runCheck());
      if (!result) return;
      setCheck({ status: "done", result });
      const { errors, warnings } = validationMessages(result);
      if (errors.length === 0 && warnings.length === 0) await publish();
    } finally {
      setSaving(false);
    }
  }

  function publishSavedVersion() {
    setStep("review");
    void runCheck();
  }

  if (agent.published) {
    return (
      <>
        <Popover open={open} onOpenChange={setOpen}>
          <PopoverTrigger asChild>
            <Button type="button" variant="outline">
              Unpublish
            </Button>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-[min(22rem,calc(100vw-2rem))] p-0">
            <div className="flex flex-col gap-3 p-4">
              <p className="text-sm font-semibold">This agent is live</p>
              <PublicUrlRow url={url} />
              <p className="text-[0.8125rem] text-muted-foreground">Anyone with the link can call this agent.</p>
            </div>
            <div className="flex justify-end gap-2 border-t border-border px-4 py-3">
              <Button asChild variant="outline" size="sm">
                <a href={url} target="_blank" rel="noopener noreferrer">
                  Open page
                  <Icon as={ExternalLinkIcon} size="sm" />
                </a>
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="text-danger-text hover:bg-danger-soft hover:text-danger-text"
                onClick={() => {
                  setOpen(false);
                  setConfirmUnpublish(true);
                }}
              >
                Unpublish
              </Button>
            </div>
          </PopoverContent>
        </Popover>
        <Dialog open={confirmUnpublish} onOpenChange={setConfirmUnpublish}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Unpublish &ldquo;{agent.name}&rdquo;?</DialogTitle>
              <DialogDescription>The public link stops answering immediately.</DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setConfirmUnpublish(false)}>
                Cancel
              </Button>
              <Button
                type="button"
                variant="destructive"
                className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                disabled={updateAgent.isPending}
                onClick={() => void unpublish()}
              >
                {updateAgent.isPending ? "Unpublishing…" : "Unpublish"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </>
    );
  }

  const messages = check.status === "done" ? validationMessages(check.result) : { errors: [], warnings: [] };
  const blocked = check.status !== "done" || messages.errors.length > 0;

  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger asChild>
        <Button type="button" variant="outline">
          Publish
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[min(24rem,calc(100vw-2rem))] p-0">
        {step === "unsaved" ? (
          <div className="flex flex-col gap-3 p-4">
            <p className="text-sm font-semibold">You have unsaved changes</p>
            <p className="text-[0.8125rem] text-pretty text-muted-foreground">
              Publishing uses the saved configuration. Save first to publish what you see.
            </p>
            <div className="flex flex-wrap justify-end gap-2">
              <Button type="button" variant="outline" size="sm" onClick={publishSavedVersion} disabled={saving}>
                Publish the saved version
              </Button>
              <Button type="button" size="sm" onClick={() => void saveAndPublish()} disabled={saving}>
                {saving ? "Saving…" : "Save and publish"}
              </Button>
            </div>
          </div>
        ) : (
          <>
            <div className="flex flex-col gap-3 p-4">
              <p className="text-sm font-semibold">Publish this agent</p>
              <p className="text-[0.8125rem] text-pretty text-muted-foreground">
                Publishing makes <span className="font-mono text-foreground">/s/{agent.slug}</span> answer calls from
                anyone with the link.
              </p>
              <CheckResult
                check={check}
                messages={messages}
                onRetry={() => void runCheck()}
                onGoToIssues={() => {
                  setOpen(false);
                  goToFirstIssue();
                }}
              />
              <PublicUrlRow url={url} />
            </div>
            <div className="flex justify-end gap-2 border-t border-border px-4 py-3">
              <Button type="button" variant="outline" size="sm" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button
                type="button"
                variant="brand"
                size="sm"
                disabled={blocked || updateAgent.isPending}
                onClick={() => void publish()}
              >
                {updateAgent.isPending ? "Publishing…" : messages.warnings.length > 0 ? "Publish anyway" : "Publish"}
              </Button>
            </div>
          </>
        )}
      </PopoverContent>
    </Popover>
  );
}

function PublicUrlRow({ url }: { url: string }) {
  return (
    <div className="flex min-w-0 items-center gap-1 rounded-sm border border-border bg-muted py-1 pr-1 pl-2.5">
      <span className="min-w-0 flex-1 truncate font-mono text-[0.8125rem]" title={url}>
        {url}
      </span>
      <CopyButton value={url} label="Copy public link" size="sm" />
    </div>
  );
}

function CheckResult({
  check,
  messages,
  onRetry,
  onGoToIssues,
}: {
  check: CheckState;
  messages: { errors: string[]; warnings: string[] };
  onRetry: () => void;
  onGoToIssues: () => void;
}) {
  if (check.status === "idle" || check.status === "loading") {
    return (
      <p role="status" className="flex items-center gap-2 text-[0.8125rem] text-muted-foreground">
        <Icon as={LoaderCircleIcon} size="sm" className="animate-spin motion-reduce:animate-none" />
        Checking the saved configuration…
      </p>
    );
  }
  if (check.status === "failed") {
    return (
      <div role="status" className="flex flex-col gap-2 rounded-md bg-danger-soft px-3 py-2.5 text-[0.8125rem] text-danger-text">
        <p>Couldn&apos;t check the configuration — {check.message}</p>
        <button type="button" onClick={onRetry} className="self-start font-medium underline underline-offset-2">
          Try again
        </button>
      </div>
    );
  }
  if (messages.errors.length > 0) {
    return (
      <div role="status" className="flex flex-col gap-2 rounded-md bg-danger-soft px-3 py-2.5 text-[0.8125rem] text-danger-text">
        <p className="flex items-center gap-1.5 font-semibold">
          <Icon as={CircleAlertIcon} size="sm" />
          Fix {pluralize(messages.errors.length, "issue", "issues")} first
        </p>
        <MessageList items={messages.errors} />
        <button type="button" onClick={onGoToIssues} className="self-start font-medium underline underline-offset-2">
          Show the issues
        </button>
      </div>
    );
  }
  if (messages.warnings.length > 0) {
    return (
      <div role="status" className="flex flex-col gap-2 rounded-md bg-warning-soft px-3 py-2.5 text-[0.8125rem] text-warning-text">
        <p className="flex items-center gap-1.5 font-semibold">
          <Icon as={TriangleAlertIcon} size="sm" />
          {pluralize(messages.warnings.length, "warning", "warnings")}
        </p>
        <MessageList items={messages.warnings} />
      </div>
    );
  }
  return (
    <p role="status" className="flex items-center gap-1.5 rounded-md bg-success-soft px-3 py-2 text-[0.8125rem] text-success-text">
      <Icon as={CircleCheckIcon} size="sm" />
      Configuration looks good
    </p>
  );
}

function MessageList({ items }: { items: string[] }) {
  const shown = items.slice(0, MAX_LISTED);
  const rest = items.length - shown.length;
  return (
    <ul className={cn("flex list-disc flex-col gap-1 pl-4 break-words")}>
      {shown.map((item, index) => (
        <li key={index}>{item}</li>
      ))}
      {rest > 0 ? <li className="list-none -ml-4">and {rest} more</li> : null}
    </ul>
  );
}
