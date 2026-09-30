"use client";

import * as React from "react";
import { toast } from "sonner";
import { ExternalLinkIcon } from "lucide-react";

import { CopyButton } from "@/components/shared/copy-button";
import { Icon } from "@/components/shared/icon";
import { LoadingRow } from "@/components/shared/loading-state";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useUpdateAgent, useValidateAgent } from "@/components/console/lib/api-hooks";
import { friendlyError } from "@/components/console/lib/friendly-error";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { errorMessage } from "@/components/console/shared/error-banner";
import { ApiError } from "@/lib/api";
import type { AgentOut, PublishGateRefusal, ValidationResult } from "@/contracts/lkap-contracts";
import { pluralize } from "@/lib/format";
import { cn } from "@/lib/utils";

import { useEditorContext } from "./editor-context";
import { validationMessages } from "./validation-map";

/**
 * The `422 tests_failing` publish refusal's `details.reason` (V5-29), in
 * plain words. `error` reads as "the tests didn't run", never "failed" —
 * `agent_tests.py`'s own rule.
 */
function testsFailingMessage(details: PublishGateRefusal): string {
  const needed = Math.round(details.min_pass_ratio * 100);
  switch (details.reason) {
    case "missing":
      return `This version hasn't been tested yet. Run its tests first — publishing needs ${needed}% of cases to pass.`;
    case "running":
      return "Its tests are still running. Wait for them to finish, then publish.";
    case "failing": {
      const got = details.pass_ratio != null ? Math.round(details.pass_ratio * 100) : null;
      return got != null
        ? `Only ${got}% of its tests passed; publishing needs ${needed}%.`
        : `Its last test run didn't reach ${needed}% passing.`;
    }
    case "error":
      // `details.error` is the runner's raw text: logs only, never shown (docs/ui/DESIGN-SYSTEM.md section 3).
      return `The tests didn't run. Run them again, or turn off "Require passing tests".`;
  }
}

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
 * a confirmation dialog rendered as a sibling of the popover. The editor
 * renders it only for people who can write (decision D12); busy buttons say
 * the gerund ("Publishing…"), never a spinner.
 */
export function PublishControl({ agent, dirty, saveNow, onValidated, goToFirstIssue }: PublishControlProps) {
  const updateAgent = useUpdateAgent(agent.id);
  const validateAgent = useValidateAgent(agent.id);
  const ctx = useEditorContext();
  const [open, setOpen] = React.useState(false);
  const [step, setStep] = React.useState<"unsaved" | "review">("review");
  const [check, setCheck] = React.useState<CheckState>({ status: "idle" });
  const [saving, setSaving] = React.useState(false);
  const [confirmUnpublish, setConfirmUnpublish] = React.useState(false);
  // V5-33: the `422 tests_failing` publish refusal (`config.publish_gate.require_tests`).
  const [testsFailing, setTestsFailing] = React.useState<PublishGateRefusal | null>(null);

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
    setTestsFailing(null);
    try {
      await updateAgent.mutateAsync({ published: true });
      toast.success("Published");
      setOpen(false);
    } catch (error) {
      if (error instanceof ApiError && error.code === "tests_failing") {
        setTestsFailing(error.details as PublishGateRefusal);
        return;
      }
      const friendly = friendlyError(error, { action: "publish" });
      toast.error(friendly.title, { description: friendly.message });
    }
  }

  // A failure throws into the confirmation, which says why and stays open.
  async function unpublish() {
    await updateAgent.mutateAsync({ published: false });
    toast.success("Unpublished");
  }

  function onOpenChange(next: boolean) {
    setOpen(next);
    if (!next || agent.published) return;
    setTestsFailing(null);
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
            <Button type="button" variant="secondary">
              Unpublish
            </Button>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-[min(22rem,calc(100vw-2rem))] p-0" aria-label="Published agent">
            <div className="flex flex-col gap-3 p-4">
              <p className="text-body font-semibold">This agent is live</p>
              <PublicUrlRow url={url} />
              <p className="text-label text-text-secondary">Anyone with the link can call this agent.</p>
            </div>
            <div className="flex flex-wrap justify-end gap-2 border-t border-border bg-muted px-4 py-3">
              <Button asChild variant="secondary" size="sm">
                <a href={url} target="_blank" rel="noopener noreferrer">
                  Open page
                  <Icon as={ExternalLinkIcon} size="sm" />
                </a>
              </Button>
              <Button
                type="button"
                variant="danger-outline"
                size="sm"
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
        <ConfirmDialog
          open={confirmUnpublish}
          onOpenChange={setConfirmUnpublish}
          title={`Unpublish “${agent.name}”?`}
          description="The public link stops answering immediately."
          confirmLabel="Unpublish"
          onConfirm={unpublish}
        />
      </>
    );
  }

  const messages = check.status === "done" ? validationMessages(check.result) : { errors: [], warnings: [] };
  const blocked = check.status !== "done" || messages.errors.length > 0;

  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger asChild>
        <Button type="button" variant="secondary">
          Publish
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[min(24rem,calc(100vw-2rem))] p-0" aria-label="Publish this agent">
        {step === "unsaved" ? (
          <div className="flex flex-col gap-3 p-4">
            <p className="text-body font-semibold">You have unsaved changes</p>
            <p className="text-label text-pretty text-text-secondary">
              Publishing uses the saved configuration. Save first to publish what you see.
            </p>
            <div className="flex flex-wrap justify-end gap-2">
              <Button type="button" variant="secondary" size="sm" onClick={publishSavedVersion} disabled={saving}>
                Publish the saved version
              </Button>
              <Button
                type="button"
                variant="primary"
                size="sm"
                onClick={() => void saveAndPublish()}
                busy={saving}
                busyLabel="Saving…"
              >
                Save and publish
              </Button>
            </div>
          </div>
        ) : (
          <>
            <div className="flex flex-col gap-3 p-4">
              <p className="text-body font-semibold">Publish this agent</p>
              <p className="text-label text-pretty text-text-secondary">
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
              {testsFailing ? (
                <Alert tone="danger" title="Tests failing">
                  <p>{testsFailingMessage(testsFailing)}</p>
                  {ctx ? (
                    <Button
                      type="button"
                      variant="link-destructive"
                      className="mt-1"
                      onClick={() => {
                        setOpen(false);
                        ctx.goToSection("tests");
                      }}
                    >
                      Open Tests
                    </Button>
                  ) : null}
                </Alert>
              ) : null}
              <PublicUrlRow url={url} />
            </div>
            <div className="flex flex-wrap justify-end gap-2 border-t border-border bg-muted px-4 py-3">
              <Button type="button" variant="secondary" size="sm" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button
                type="button"
                variant="primary"
                size="sm"
                disabled={blocked}
                busy={updateAgent.isPending}
                busyLabel="Publishing…"
                onClick={() => void publish()}
              >
                {messages.warnings.length > 0 ? "Publish anyway" : "Publish"}
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
      <span className="min-w-0 flex-1 truncate font-mono text-label" title={url}>
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
    return <LoadingRow label="Checking the saved configuration…" className="py-0" />;
  }
  if (check.status === "failed") {
    return (
      <Alert
        tone="danger"
        title="Couldn't check the configuration"
        actions={
          <Button type="button" variant="secondary" size="sm" onClick={onRetry}>
            Retry
          </Button>
        }
      >
        {check.message}
      </Alert>
    );
  }
  if (messages.errors.length > 0) {
    return (
      <Alert tone="danger" title={`Fix ${pluralize(messages.errors.length, "issue", "issues")} first`}>
        <MessageList items={messages.errors} />
        <Button type="button" variant="link-destructive" className="mt-1" onClick={onGoToIssues}>
          Show the issues
        </Button>
      </Alert>
    );
  }
  if (messages.warnings.length > 0) {
    return (
      <Alert tone="warning" title={pluralize(messages.warnings.length, "warning", "warnings")}>
        <MessageList items={messages.warnings} />
      </Alert>
    );
  }
  return <Alert tone="success">Configuration looks good</Alert>;
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
