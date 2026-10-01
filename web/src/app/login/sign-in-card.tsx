import * as React from "react";
import Link from "next/link";

import { LkapLogo } from "@/components/shared/lkap-logo";
import { LoadingRegion } from "@/components/shared/loading-state";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * The LKAP mark at 40 px, above the title. It is the page's way home, a link
 * named "LKAP home". The skeleton shows the same mark, so nothing shifts when
 * the form hydrates.
 */
function HomeMark() {
  return (
    <Link
      href="/"
      aria-label="LKAP home"
      data-slot="sign-in-logo"
      className="mb-5 inline-flex w-fit rounded outline-none focus-visible:shadow-focus"
    >
      <LkapLogo variant="mark" size="lg" />
    </Link>
  );
}

/**
 * The sign-in form card (docs/ui/DESIGN-SYSTEM.md section 7.4, "Sign-in"): a
 * 380 px card on a hairline with no shadow (section 6.7), the LKAP mark, a
 * 26 px title (section 3) and a one-sentence description. The title is the
 * page's only `h1`. Hook-free, so the page's `Suspense` fallback can use its
 * frame too.
 */
export function SignInCard({
  title,
  description,
  children,
  footer,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <div data-slot="sign-in-card" className="w-full max-w-[380px] rounded-lg border border-border bg-card p-6 max-sm:p-5">
      <HomeMark />
      <div className="mb-6 flex flex-col gap-1.5">
        <h1 className="text-display font-semibold tracking-[-0.025em] text-foreground">{title}</h1>
        <p className="text-body text-text-secondary">{description}</p>
      </div>
      {children}
      {footer ? <div className="mt-5 border-t border-border pt-4 text-label text-text-secondary">{footer}</div> : null}
    </div>
  );
}

/**
 * Loading state for the card (section 8.1): the form reads the query string
 * (`?invite=`, `?next=`) on the client, so until it hydrates this skeleton
 * mirrors the card: the real mark, then title, description, two fields and
 * the button.
 */
export function SignInCardSkeleton() {
  return (
    <div data-slot="sign-in-card" className="w-full max-w-[380px] rounded-lg border border-border bg-card p-6 max-sm:p-5">
      <HomeMark />
      <LoadingRegion label="Loading sign-in" className="flex flex-col">
        <div className="mb-6 flex flex-col gap-2.5">
          <Skeleton className="h-7 w-28" />
          <Skeleton className="h-3.5 w-4/5" />
        </div>
        <div className="flex flex-col gap-4">
          {[0, 1].map((field) => (
            <div key={field} className="flex flex-col gap-1.5">
              <Skeleton className="h-3.5 w-16" />
              <Skeleton className="h-9 w-full rounded" />
            </div>
          ))}
          <Skeleton className="mt-1 h-8.5 w-full rounded" />
        </div>
      </LoadingRegion>
    </div>
  );
}
