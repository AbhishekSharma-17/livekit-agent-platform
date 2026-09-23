"use client";

import { CircleAlertIcon, TriangleAlertIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { pluralize } from "@/lib/format";
import { cn } from "@/lib/utils";

import { useSectionIssues } from "./editor-context";
import { displayMessage, formPathForIssue, type EditorIssue } from "./validation-map";

/** "2 errors · 1 warning". */
export function issueCountLabel(errors: number, warnings: number): string {
  const parts: string[] = [];
  if (errors > 0) parts.push(pluralize(errors, "error", "errors"));
  if (warnings > 0) parts.push(pluralize(warnings, "warning", "warnings"));
  return parts.join(" · ");
}

/**
 * The "Issues" list at the top of a section (docs/UI_UX_SPEC.md §4.3): this
 * section's issues plus the unmapped ("general") ones, errors first, each
 * with a "Show field" link when the issue names a field. Renders nothing when
 * there are no issues, and nothing outside the editor shell.
 */
export function SectionIssueList({ sectionId, className }: { sectionId?: string; className?: string }) {
  const { issues, general, focusIssue } = useSectionIssues(sectionId);
  const all: EditorIssue[] = [...issues, ...general].sort(
    (a, b) => (a.severity === b.severity ? 0 : a.severity === "error" ? -1 : 1),
  );
  if (all.length === 0) return null;

  const errors = all.filter((issue) => issue.severity === "error").length;
  const warnings = all.length - errors;

  return (
    <div
      role="region"
      aria-label="Issues in this section"
      data-slot="section-issues"
      className={cn("overflow-hidden rounded-lg border border-border bg-card", className)}
    >
      <p className="border-b border-border px-4 py-2.5 text-sm font-semibold">{issueCountLabel(errors, warnings)}</p>
      <ul className="divide-y divide-border">
        {all.map((issue) => {
          const isError = issue.severity === "error";
          const canFocus = formPathForIssue(issue.path) !== null;
          return (
            <li
              key={issue.key}
              data-severity={issue.severity}
              className={cn(
                "flex items-start gap-2.5 px-4 py-2.5 text-[0.8125rem] leading-[1.125rem]",
                isError ? "bg-danger-soft text-danger-text" : "bg-warning-soft text-warning-text",
              )}
            >
              <Icon
                as={isError ? CircleAlertIcon : TriangleAlertIcon}
                size="sm"
                label={isError ? "Error" : "Warning"}
                className="mt-0.5 shrink-0"
              />
              <div className="min-w-0 flex-1">
                <p className="text-pretty break-words">{displayMessage(issue)}</p>
                {issue.path ? <p className="mt-0.5 font-mono text-xs break-all opacity-80">{issue.path}</p> : null}
              </div>
              {canFocus ? (
                <button
                  type="button"
                  onClick={() => focusIssue(issue)}
                  className="shrink-0 rounded-xs text-xs font-medium underline underline-offset-2 outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
                >
                  Show field
                </button>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
