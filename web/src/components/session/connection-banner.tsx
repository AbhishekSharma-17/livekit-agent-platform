"use client";

/**
 * Connection / agent-state banner for the session surface.
 *
 * Renders nothing while everything is healthy so the stage stays uncluttered;
 * announces politely otherwise.
 */
import * as React from "react";

import { AlertTriangleIcon, Loader2Icon, WifiOffIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { PanelConnectionState } from "@/lib/livekit";

export interface ConnectionBannerProps {
  connectionState: PanelConnectionState;
  /** `useAgent().state` — `'failed'` carries `failureReasons`. */
  agentState: string;
  failureReasons?: string[] | null;
  /** A connect/RPC error that the UI should surface verbatim. */
  error?: string | null;
  onRetry?: () => void;
}

interface BannerContent {
  tone: "info" | "warning" | "danger";
  icon: React.ReactNode;
  message: string;
  retry: boolean;
}

function contentFor({
  connectionState,
  agentState,
  failureReasons,
  error,
}: ConnectionBannerProps): BannerContent | null {
  if (error) {
    return {
      tone: "danger",
      icon: <AlertTriangleIcon className="size-4" />,
      message: error,
      retry: true,
    };
  }
  if (agentState === "failed") {
    const detail = failureReasons?.length ? ` (${failureReasons.join("; ")})` : "";
    return {
      tone: "danger",
      icon: <AlertTriangleIcon className="size-4" />,
      message: `The agent could not join this call${detail}.`,
      retry: true,
    };
  }
  if (connectionState === "reconnecting") {
    return {
      tone: "warning",
      icon: <Loader2Icon className="size-4 animate-spin" />,
      message: "Connection lost — reconnecting…",
      retry: false,
    };
  }
  if (connectionState === "disconnected") {
    return {
      tone: "info",
      icon: <WifiOffIcon className="size-4" />,
      message: "The call has ended.",
      retry: true,
    };
  }
  if (connectionState === "connecting") {
    return {
      tone: "info",
      icon: <Loader2Icon className="size-4 animate-spin" />,
      message: "Connecting to the agent…",
      retry: false,
    };
  }
  return null;
}

const TONE_CLASS: Record<BannerContent["tone"], string> = {
  info: "bg-muted/60 text-muted-foreground",
  warning: "bg-amber-500/15 text-amber-200",
  danger: "bg-red-500/15 text-red-200",
};

export function ConnectionBanner(props: ConnectionBannerProps) {
  const content = contentFor(props);

  return (
    <div role="status" aria-live="polite" className="shrink-0">
      {content && (
        <div
          data-testid="connection-banner"
          className={cn(
            "flex items-center justify-center gap-2 px-4 py-2 text-sm",
            TONE_CLASS[content.tone],
          )}
        >
          {content.icon}
          <span>{content.message}</span>
          {content.retry && props.onRetry && (
            <Button size="xs" variant="outline" onClick={props.onRetry}>
              Try again
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
