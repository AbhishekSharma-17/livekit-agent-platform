import * as React from "react";
import Link from "next/link";
import { ArrowRightIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * List card (docs/ui/DESIGN-SYSTEM.md section 6.7): a bordered container
 * whose rows are separated by hairlines. Rows are at least 56 px with 12 px
 * gaps: a leading avatar, mark or monogram; a title plus a secondary meta
 * line (tabular times); a trailing badge, count or "…" menu, or `ArrowRight`
 * when the row is a link. Clickable rows get a pointer and a `--muted` hover.
 */
export function ListCard({ className, label, ...props }: React.ComponentProps<"ul"> & { label?: string }) {
  return (
    <ul
      data-slot="list-card"
      aria-label={label}
      className={cn("divide-y divide-border overflow-hidden rounded-lg border border-border bg-card", className)}
      {...props}
    />
  );
}

export interface ListCardRowProps {
  leading?: React.ReactNode;
  title: React.ReactNode;
  meta?: React.ReactNode;
  /** Badge, count or row menu. Links also get a trailing `ArrowRight`. */
  trailing?: React.ReactNode;
  /** Makes the whole row a link. */
  href?: string;
  /** Makes the whole row a button (when there is no `href`). */
  onClick?: () => void;
  className?: string;
}

export function ListCardRow({ leading, title, meta, trailing, href, onClick, className }: ListCardRowProps) {
  const interactive = Boolean(href || onClick);
  const body = (
    <>
      {leading ? <span className="flex shrink-0 items-center">{leading}</span> : null}
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="truncate text-body font-medium text-foreground">{title}</span>
        {meta ? <span className="truncate text-label text-text-secondary tabular-nums">{meta}</span> : null}
      </span>
    </>
  );
  const rowClasses = "flex min-h-14 items-center gap-3 px-4 py-3";
  return (
    <li
      data-slot="list-card-row"
      className={cn("relative", interactive && "transition-colors duration-(--duration-fast) hover:bg-muted", className)}
    >
      {href ? (
        <Link href={href} className={cn(rowClasses, "cursor-pointer outline-offset-[-2px]")}>
          {body}
          {trailing ? <span className="relative z-10 flex shrink-0 items-center gap-2">{trailing}</span> : null}
          <ArrowRightIcon aria-hidden="true" className="text-text-tertiary" />
        </Link>
      ) : onClick ? (
        <div className={rowClasses}>
          <button
            type="button"
            onClick={onClick}
            className="flex min-w-0 flex-1 cursor-pointer items-center gap-3 text-left outline-offset-2 after:absolute after:inset-0 after:content-['']"
          >
            {body}
          </button>
          {trailing ? <span className="relative z-10 flex shrink-0 items-center gap-2">{trailing}</span> : null}
        </div>
      ) : (
        <div className={rowClasses}>
          {body}
          {trailing ? <span className="flex shrink-0 items-center gap-2">{trailing}</span> : null}
        </div>
      )}
    </li>
  );
}
