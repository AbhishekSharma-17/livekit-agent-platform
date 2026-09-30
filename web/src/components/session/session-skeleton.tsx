"use client";

/**
 * Loading states for the caller-facing session while its client code loads
 * (docs/ui/DESIGN-SYSTEM.md sections 6.5 and 8.1): skeletons that mirror the
 * layout they stand in for, inside a labelled `status` region, plus the error
 * state when the code could not load at all.
 *
 * Used by `app/(session)/s/[slug]/embed-layout.tsx` as the `next/dynamic`
 * fallback, so it sits on the `/s/[slug]` route's first load: keep it to the
 * skeleton, alert and button primitives (no LiveKit, nothing from the console).
 */
import * as React from "react";
import { RefreshCwIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { LoadingRegion } from "@/components/shared/loading-state";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton, SkeletonText } from "@/components/ui/skeleton";

export type SessionSkeletonChannel = "text" | "voice";

/** The embed text chat: a few message bubbles over the pinned composer. */
function TextChatSkeleton() {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex min-h-0 flex-1 flex-col gap-3 px-3 py-3">
        <Skeleton className="h-9 w-3/5 rounded-lg" />
        <Skeleton className="h-9 w-2/5 self-end rounded-lg" />
        <Skeleton className="h-14 w-4/5 rounded-lg" />
      </div>
      <div className="border-border flex items-center gap-2 border-t p-3 pb-[calc(0.75rem+env(safe-area-inset-bottom,0px))]">
        <Skeleton className="h-9 flex-1 rounded" />
        <Skeleton className="h-8.5 w-16 rounded" />
      </div>
    </div>
  );
}

/** The voice pre-call card: the agent's name and intro, the name field, Start call. */
function PreCallSkeleton() {
  return (
    <div className="flex h-full flex-col items-center justify-center p-4">
      <div className="border-border bg-card flex w-full max-w-[480px] flex-col gap-5 rounded-lg border p-5">
        <div className="flex flex-col gap-3">
          <Skeleton className="h-7 w-1/2" />
          <SkeletonText lines={2} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Skeleton className="h-3.5 w-24" />
          <Skeleton className="h-10 w-full rounded" />
        </div>
        <Skeleton className="h-11 w-full rounded" />
      </div>
    </div>
  );
}

export interface SessionSkeletonProps {
  channel: SessionSkeletonChannel;
  className?: string;
}

/** The embed's loading state, shaped like the layout that will replace it. */
export function SessionSkeleton({ channel, className }: SessionSkeletonProps) {
  return (
    <LoadingRegion
      label={channel === "text" ? "Loading the chat" : "Loading the call"}
      className={className ?? "h-dvh w-full overflow-hidden"}
    >
      {channel === "text" ? <TextChatSkeleton /> : <PreCallSkeleton />}
    </LoadingRegion>
  );
}

/** The session code failed to load (a dropped connection, a stale deploy). */
export function SessionLoadError({ className }: { className?: string }) {
  return (
    <div className={className ?? "flex h-dvh w-full items-center justify-center p-4"}>
      <Alert
        tone="danger"
        title="This page didn't load"
        className="max-w-[480px]"
        actions={
          <Button variant="secondary" size="sm" onClick={() => window.location.reload()}>
            <Icon as={RefreshCwIcon} size="sm" />
            Reload
          </Button>
        }
      >
        Check your connection, then reload the page.
      </Alert>
    </div>
  );
}
