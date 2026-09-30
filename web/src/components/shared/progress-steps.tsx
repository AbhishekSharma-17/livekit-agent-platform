import * as React from "react";
import { CircleAlertIcon, CircleCheckIcon, CircleIcon } from "lucide-react";

import { Progress } from "@/components/ui/progress";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

export type StepStatus = "done" | "current" | "upcoming" | "failed";

export interface Step {
  id: string;
  label: string;
  status: StepStatus;
  /** Optional detail under the label ("12 of 40 documents"). */
  detail?: React.ReactNode;
}

const STATUS_WORD: Record<StepStatus, string> = {
  done: "Done",
  current: "In progress",
  upcoming: "Not started",
  failed: "Failed",
};

/**
 * Step list (docs/ui/DESIGN-SYSTEM.md sections 6.5 and 7.4 "Long-running
 * task page"): named steps with the current one marked `aria-current="step"`.
 * Each step says its state in words (screen-reader text), not only an icon.
 */
export function StepList({ steps, className }: { steps: Step[]; className?: string }) {
  return (
    <ol data-slot="step-list" className={cn("flex flex-col gap-3", className)}>
      {steps.map((step) => (
        <li
          key={step.id}
          data-status={step.status}
          aria-current={step.status === "current" ? "step" : undefined}
          className="flex items-start gap-2.5"
        >
          <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center" aria-hidden="true">
            {step.status === "done" ? (
              <CircleCheckIcon className="text-success-text" />
            ) : step.status === "current" ? (
              <Spinner />
            ) : step.status === "failed" ? (
              <CircleAlertIcon className="text-destructive-text" />
            ) : (
              <CircleIcon className="text-text-disabled" />
            )}
          </span>
          <span className="flex min-w-0 flex-col gap-0.5">
            <span
              className={cn(
                "text-control leading-5",
                step.status === "current" ? "font-semibold text-foreground" : "text-text-secondary",
                step.status === "failed" && "text-destructive-text",
              )}
            >
              {step.label}
              <span className="sr-only"> ({STATUS_WORD[step.status]})</span>
            </span>
            {step.detail ? <span className="text-caption text-text-tertiary tabular-nums">{step.detail}</span> : null}
          </span>
        </li>
      ))}
    </ol>
  );
}

export interface ProgressStepsProps {
  /** Accessible name of the bar, e.g. "Indexing progress". */
  label: string;
  /** 0–100; derived from the steps when omitted. */
  value?: number;
  steps: Step[];
  className?: string;
}

/**
 * A progress bar plus the named step list, with an `sr-only` status line so
 * screen readers hear each change ("Step 2 of 4: Embedding chunks").
 */
export function ProgressSteps({ label, value, steps, className }: ProgressStepsProps) {
  const done = steps.filter((step) => step.status === "done").length;
  const percent = value ?? (steps.length === 0 ? 0 : Math.round((done / steps.length) * 100));
  const currentIndex = steps.findIndex((step) => step.status === "current" || step.status === "failed");
  const current = currentIndex >= 0 ? steps[currentIndex] : undefined;
  const announcement = current
    ? `Step ${currentIndex + 1} of ${steps.length}: ${current.label}${current.status === "failed" ? " failed" : ""}`
    : done === steps.length && steps.length > 0
      ? "All steps done"
      : "";
  return (
    <div data-slot="progress-steps" className={cn("flex flex-col gap-4", className)}>
      <Progress value={percent} aria-label={label} />
      <StepList steps={steps} />
      <p role="status" aria-live="polite" className="sr-only">
        {announcement}
      </p>
    </div>
  );
}
