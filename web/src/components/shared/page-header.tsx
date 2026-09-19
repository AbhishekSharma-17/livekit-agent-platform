import * as React from "react";

import Link from "next/link";

import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { cn } from "@/lib/utils";

export interface BreadcrumbEntry {
  label: string;
  href?: string;
}

export interface PageHeaderProps {
  title: React.ReactNode;
  description?: React.ReactNode;
  /** Small caption above the title. */
  eyebrow?: React.ReactNode;
  /** Trail for depth ≥ 2; the last entry is the current page (no href). */
  breadcrumbs?: BreadcrumbEntry[];
  /** The page's actions (primary last). */
  actions?: React.ReactNode;
  /** Stick to the top of the scroll container with a hairline under it. */
  sticky?: boolean;
  className?: string;
}

/** Page header (docs/UI_UX_SPEC.md §2.7): h1 title, optional trail, actions right. */
export function PageHeader({
  title,
  description,
  eyebrow,
  breadcrumbs,
  actions,
  sticky = false,
  className,
}: PageHeaderProps) {
  return (
    <header
      data-slot="page-header"
      className={cn(
        "mb-6 flex flex-col gap-3",
        sticky && "sticky top-0 z-20 -mx-4 border-b border-border bg-background px-4 py-3 md:-mx-6 md:px-6",
        className,
      )}
    >
      {breadcrumbs && breadcrumbs.length > 0 ? (
        <Breadcrumb>
          <BreadcrumbList>
            {breadcrumbs.map((crumb, index) => {
              const last = index === breadcrumbs.length - 1;
              return (
                <React.Fragment key={`${crumb.label}-${index}`}>
                  <BreadcrumbItem>
                    {crumb.href && !last ? (
                      <BreadcrumbLink asChild>
                        <Link href={crumb.href}>{crumb.label}</Link>
                      </BreadcrumbLink>
                    ) : (
                      <BreadcrumbPage>{crumb.label}</BreadcrumbPage>
                    )}
                  </BreadcrumbItem>
                  {!last ? <BreadcrumbSeparator /> : null}
                </React.Fragment>
              );
            })}
          </BreadcrumbList>
        </Breadcrumb>
      ) : null}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          {eyebrow ? <div className="mb-1 text-xs font-medium text-muted-foreground">{eyebrow}</div> : null}
          <h1 className="text-[1.375rem] leading-7 font-semibold tracking-[-0.015em] text-balance">{title}</h1>
          {description ? (
            <p className="mt-1 max-w-[65ch] text-sm text-pretty text-muted-foreground">{description}</p>
          ) : null}
        </div>
        {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
    </header>
  );
}
