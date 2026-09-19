import * as React from "react";

import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

import { Icon } from "./icon";

export interface EmptyStateProps {
  /** A Lucide component (preferred) or a ready-made element. */
  icon?: LucideIcon | React.ReactElement;
  /** What will appear here ("No agents yet"). */
  title: string;
  /** One sentence on why it matters. */
  description?: string;
  /** The one primary action. */
  action?: React.ReactNode;
  /** Optional secondary link. */
  secondary?: React.ReactNode;
  /** One line + inline action, for in-section empties (§6). */
  compact?: boolean;
  className?: string;
}

function renderIcon(icon: EmptyStateProps["icon"], size: "md" | "lg") {
  if (!icon) return null;
  if (React.isValidElement(icon)) return icon;
  return <Icon as={icon as LucideIcon} size={size} />;
}

/** Empty state (docs/UI_UX_SPEC.md §4.1, §6): icon, title, why, one action. */
export function EmptyState({ icon, title, description, action, secondary, compact = false, className }: EmptyStateProps) {
  if (compact) {
    return (
      <div
        data-slot="empty-state"
        data-compact=""
        className={cn("flex flex-wrap items-center gap-x-3 gap-y-2 py-3 text-sm text-muted-foreground", className)}
      >
        {icon ? <span className="text-muted-foreground">{renderIcon(icon, "md")}</span> : null}
        <p className="min-w-0 flex-1">
          <span className="font-medium text-foreground">{title}</span>
          {description ? <span> — {description}</span> : null}
        </p>
        {action || secondary ? (
          <div className="flex items-center gap-2">
            {action}
            {secondary}
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div
      data-slot="empty-state"
      className={cn("flex flex-col items-center justify-center gap-3 px-6 py-14 text-center", className)}
    >
      {icon ? (
        <span className="mb-1 flex size-10 items-center justify-center rounded-md bg-muted text-muted-foreground">
          {renderIcon(icon, "lg")}
        </span>
      ) : null}
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      {description ? <p className="max-w-sm text-sm text-muted-foreground">{description}</p> : null}
      {action || secondary ? (
        <div className="mt-1 flex flex-wrap items-center justify-center gap-3">
          {action}
          {secondary}
        </div>
      ) : null}
    </div>
  );
}
