import * as React from "react";
import Link from "next/link";
import { ArrowLeftIcon } from "lucide-react";

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
  /** One sentence: what this page is for. */
  description?: React.ReactNode;
  /** Small uppercase caption above the title (the only uppercase text). */
  eyebrow?: React.ReactNode;
  /** A back link that names where it goes: `{ href, label: "Back to agents" }`. */
  back?: { href: string; label: string };
  /** A status badge beside the title. */
  badge?: React.ReactNode;
  /** Trail for depth ≥ 2; the last entry is the current page (no href). */
  breadcrumbs?: BreadcrumbEntry[];
  /** Secondary actions first, then the ONE primary action, last. */
  actions?: React.ReactNode;
  /** Stick to the top of the scroll container with a hairline under it. */
  sticky?: boolean;
  className?: string;
}

/**
 * Page header (docs/ui/DESIGN-SYSTEM.md section 7.3): an optional back link
 * (ArrowLeft, 28 px, 13 px secondary, pulled 8 px left), an eyebrow, the h1
 * (22 px / 600) with an optional status badge, a one-sentence intro
 * (secondary, 68ch) and the actions with the primary last. Space between,
 * aligned to the end, wrapping with 16/24 px gaps; 24 px below it (18 px on
 * phones), where it aligns to the start and the actions go full width.
 */
export function PageHeader({
  title,
  description,
  eyebrow,
  back,
  badge,
  breadcrumbs,
  actions,
  sticky = false,
  className,
}: PageHeaderProps) {
  return (
    <header
      data-slot="page-header"
      className={cn(
        "mb-[18px] flex flex-col gap-3 sm:mb-6",
        sticky && "sticky top-0 z-20 -mx-4 border-b border-border bg-background px-4 py-3 sm:-mx-5 sm:px-5 min-[901px]:-mx-6 min-[901px]:px-6",
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
      <div className="flex flex-col items-start gap-4 sm:flex-row sm:flex-wrap sm:items-end sm:justify-between sm:gap-x-6">
        <div data-slot="page-header-text" className="min-w-0 flex-1">
          {back ? (
            <Link
              href={back.href}
              data-slot="page-back-link"
              className="-ml-2 mb-1 inline-flex h-7 items-center gap-1.5 rounded-sm px-2 text-label text-text-secondary transition-colors duration-(--duration-fast) hover:bg-muted hover:text-foreground"
            >
              <ArrowLeftIcon aria-hidden="true" className="size-[15px]" />
              {back.label}
            </Link>
          ) : null}
          {eyebrow ? (
            <div data-slot="page-eyebrow" className="mb-1 text-caption font-medium tracking-[0.04em] text-text-tertiary uppercase">
              {eyebrow}
            </div>
          ) : null}
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
            <h1 className="text-page font-semibold tracking-[-0.018em] text-balance text-foreground">{title}</h1>
            {badge}
          </div>
          {description ? (
            <p className="mt-1 max-w-[68ch] text-body leading-[1.55] text-pretty text-text-secondary">{description}</p>
          ) : null}
        </div>
        {actions ? (
          <div data-slot="page-actions" className="flex w-full flex-wrap items-center gap-2 sm:w-auto sm:shrink-0">
            {actions}
          </div>
        ) : null}
      </div>
    </header>
  );
}

export interface PageProps extends React.ComponentProps<"section"> {
  /** `default` 1200 px; `wide` 1440 px for dense tools; `narrow` 880 px for forms and reading. */
  width?: "default" | "wide" | "narrow";
}

const PAGE_WIDTH = { default: "max-w-[1200px]", wide: "max-w-[1440px]", narrow: "max-w-[880px]" } as const;

/**
 * Page container (section 4, 7.3): centred, padded `28px 32px 72px`, then
 * `20px 20px 56px` at 900 px and below and `16px 16px 48px` at 640 px and
 * below. Compose `PageHeader`, page-level alerts, then content.
 */
export function Page({ width = "default", className, ...props }: PageProps) {
  return (
    <section
      data-slot="page"
      data-width={width}
      className={cn(
        "mx-auto w-full px-8 pt-7 pb-18 max-[900px]:px-5 max-[900px]:pt-5 max-[900px]:pb-14 max-sm:px-4 max-sm:pt-4 max-sm:pb-12",
        PAGE_WIDTH[width],
        className,
      )}
      {...props}
    />
  );
}
