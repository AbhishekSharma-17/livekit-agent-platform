"use client";

/**
 * The frame every v2 block renders in: the WP-9 `PanelBlock` look (sentence
 * case `h3`, count chip, hairline above, no nested card) plus the block's
 * identity for `show_block`, tests and the capture harness.
 */
import * as React from "react";

import { cn } from "@/lib/utils";
import { PanelBlock } from "@/panels/generic/blocks";

import type { BlockSpecV2 } from "@/panels/composite/layout";
import { blockDomId } from "./types";

export function BlockFrame({
  spec,
  title,
  count,
  action,
  highlighted,
  loading,
  className,
  children,
}: {
  spec: BlockSpecV2;
  title: string | null;
  count?: number;
  action?: React.ReactNode;
  highlighted?: boolean;
  /** The lazy block's code is still loading (the `Suspense` fallback). */
  loading?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  const shared = {
    id: blockDomId(spec.id),
    "data-testid": `block-${spec.type}`,
    "data-block-id": spec.id,
    "data-block-type": spec.type,
    "data-highlighted": highlighted ? "true" : undefined,
    "data-loading": loading ? "true" : undefined,
    // Focusable only programmatically, so `show_block` can move focus here.
    tabIndex: -1,
  } as const;
  const ring = cn(
    "scroll-mt-4 outline-none transition-[box-shadow] duration-(--dur-3) ease-out",
    highlighted && "ring-brand-line ring-2 ring-inset",
    className,
  );

  if (title === null) {
    return (
      <section data-slot="panel-block" {...shared} className={cn("border-border border-t first:border-t-0", ring)}>
        {children}
      </section>
    );
  }
  return (
    <PanelBlock title={title} count={count} action={action} {...shared} className={ring}>
      {children}
    </PanelBlock>
  );
}
