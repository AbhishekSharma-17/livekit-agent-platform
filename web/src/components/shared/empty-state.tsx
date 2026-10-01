import * as React from "react";
import { SearchXIcon, type LucideIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { Icon } from "./icon";

export interface EmptyStateProps {
  /** A Lucide component (preferred) or a ready-made element. */
  icon?: LucideIcon | React.ReactElement;
  /** What will appear here ("No agents yet"). */
  title: string;
  /** One sentence on why it matters or how to fill it. */
  description?: React.ReactNode;
  /** The one action that fills it. */
  action?: React.ReactNode;
  /** Optional secondary link. */
  secondary?: React.ReactNode;
  /** One line + inline action, for in-section empties. */
  compact?: boolean;
  /** `plain` drops the dashed card (inside a card that already frames it). */
  variant?: "card" | "plain";
  className?: string;
}

function renderIcon(icon: EmptyStateProps["icon"], size: "md" | "tile") {
  if (!icon) return null;
  if (React.isValidElement(icon)) return icon;
  return <Icon as={icon as LucideIcon} size={size} />;
}

/**
 * Empty state (docs/ui/DESIGN-SYSTEM.md section 6.5): centred, padding
 * 40 px 24 px, a dashed hairline on a card surface, a 40 px muted icon tile
 * with an 18 px icon, a 14 px / 600 title, a 13 px description (max 46ch)
 * and **one** action. "Nothing yet" copy is one sentence plus that action;
 * for a filtered list use `NoMatches` instead.
 */
export function EmptyState({
  icon,
  title,
  description,
  action,
  secondary,
  compact = false,
  variant = "card",
  className,
}: EmptyStateProps) {
  if (compact) {
    return (
      <div
        data-slot="empty-state"
        data-compact=""
        className={cn("flex flex-wrap items-center gap-x-3 gap-y-2 py-3 text-label text-text-secondary", className)}
      >
        {icon ? <span className="text-text-tertiary">{renderIcon(icon, "md")}</span> : null}
        <p className="min-w-0 flex-1">
          <span className="font-medium text-foreground">{title}</span>
          {description ? <span>. {description}</span> : null}
        </p>
        {action || secondary ? (
          <div className="flex items-center gap-2">
            {secondary}
            {action}
          </div>
        ) : null}
      </div>
    );
  }
  return (
    <div
      data-slot="empty-state"
      data-variant={variant}
      className={cn(
        "flex flex-col items-center justify-center gap-2 px-6 py-10 text-center",
        variant === "card" && "rounded-lg border border-dashed border-border bg-card",
        className,
      )}
    >
      {icon ? (
        <span className="mb-1 flex size-10 items-center justify-center rounded bg-muted text-text-secondary">
          {renderIcon(icon, "tile")}
        </span>
      ) : null}
      {/* h2, not h3: an empty page's first heading after the PageHeader h1
          must not skip a level (axe `heading-order`); a lower level never
          fails that rule inside a deeper section either. */}
      <h2 className="text-body font-semibold text-foreground">{title}</h2>
      {description ? <p className="max-w-[46ch] text-label text-text-secondary">{description}</p> : null}
      {action || secondary ? (
        <div className="mt-2 flex flex-wrap items-center justify-center gap-2">
          {secondary}
          {action}
        </div>
      ) : null}
    </div>
  );
}

export interface NoMatchesProps {
  /** Plural noun: "agents", "sessions". */
  items: string;
  query?: string;
  /** Clears the search and filters. */
  onClear?: () => void;
  className?: string;
}

/**
 * No matches (section 6.5): a separate empty state for a filtered list,
 * with `SearchX`, "No {items} match “{query}”" and a **Clear filters** action.
 */
export function NoMatches({ items, query, onClear, className }: NoMatchesProps) {
  const trimmed = query?.trim();
  return (
    <EmptyState
      icon={SearchXIcon}
      title={trimmed ? `No ${items} match “${trimmed}”` : `No ${items} match these filters`}
      description="Try a different name, word or category."
      action={
        onClear ? (
          <Button type="button" variant="secondary" size="sm" onClick={onClear}>
            Clear filters
          </Button>
        ) : undefined
      }
      className={className}
    />
  );
}
