"use client";

import * as React from "react";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Data display primitives (docs/ui/DESIGN-SYSTEM.md section 6.7): meta list,
 * stat card and grid, avatar.
 */

export interface MetaItem {
  term: React.ReactNode;
  value: React.ReactNode;
}

/** Two-column `<dl>`: 12 px tertiary terms, 13.5 px tabular values; one column on phones. */
export function MetaList({ items, className }: { items: MetaItem[]; className?: string }) {
  return (
    <dl
      data-slot="meta-list"
      className={cn("grid grid-cols-1 gap-x-6 gap-y-1 sm:grid-cols-[max-content_minmax(0,1fr)] sm:gap-y-2", className)}
    >
      {items.map((item, index) => (
        <React.Fragment key={index}>
          <dt className="text-caption text-text-tertiary sm:pt-px">{item.term}</dt>
          <dd className="mb-2 min-w-0 text-control break-words text-foreground tabular-nums sm:mb-0">{item.value}</dd>
        </React.Fragment>
      ))}
    </dl>
  );
}

export interface StatCardProps {
  label: string;
  value: React.ReactNode;
  icon?: LucideIcon;
  hint?: React.ReactNode;
  className?: string;
}

/** Stat card: a 12.5 px label with a 14 px icon, a 24 px / 600 tabular value and a 12 px hint. */
export function StatCard({ label, value, icon: Glyph, hint, className }: StatCardProps) {
  return (
    <div data-slot="stat-card" className={cn("flex flex-col gap-1.5 rounded-lg border border-border bg-card p-4", className)}>
      <div className="flex items-center gap-1.5 text-stat-label text-text-secondary">
        {Glyph ? <Glyph aria-hidden="true" className="size-3.5 text-text-tertiary" /> : null}
        <span>{label}</span>
      </div>
      <div className="text-stat font-semibold tracking-[-0.02em] text-foreground tabular-nums">{value}</div>
      {hint ? <div className="text-caption text-text-secondary">{hint}</div> : null}
    </div>
  );
}

/** Stat grid: `repeat(auto-fit, minmax(180px, 1fr))` with 12 px gaps. */
export function StatGrid({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="stat-grid"
      className={cn("grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-3", className)}
      {...props}
    />
  );
}

/** First letters of the first and last words: "Ada Lovelace" -> "AL", "ada@example.com" -> "A". */
export function initials(name: string): string {
  const words = name.trim().split(/[\s._@-]+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (name.includes("@") && !name.trim().includes(" ")) return words[0].charAt(0).toUpperCase();
  const first = words[0].charAt(0);
  const last = words.length > 1 ? words[words.length - 1].charAt(0) : "";
  return (first + last).toUpperCase();
}

const AVATAR_SIZE = { sm: "size-[22px] text-tab", md: "size-7 text-caption", lg: "size-10 text-body" } as const;

export interface AvatarProps {
  name: string;
  src?: string | null;
  /** 22, 28 (default) or 40 px. */
  size?: keyof typeof AVATAR_SIZE;
  className?: string;
}

/** Avatar: a circle with initials on `--muted-strong`; falls back to initials when the photo fails. */
export function Avatar({ name, src, size = "md", className }: AvatarProps) {
  const [failed, setFailed] = React.useState(false);
  const showImage = Boolean(src) && !failed;
  return (
    <span
      data-slot="avatar"
      role="img"
      aria-label={name}
      className={cn(
        "inline-flex shrink-0 items-center justify-center overflow-hidden rounded-pill bg-muted-strong font-semibold text-text-secondary select-none",
        AVATAR_SIZE[size],
        className,
      )}
    >
      {showImage ? (
        // eslint-disable-next-line @next/next/no-img-element -- remote avatars of unknown hosts; next/image needs configured domains
        <img src={src ?? undefined} alt="" className="size-full object-cover" onError={() => setFailed(true)} />
      ) : (
        <span aria-hidden="true">{initials(name)}</span>
      )}
    </span>
  );
}
