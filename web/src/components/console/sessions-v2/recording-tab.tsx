"use client";

import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AudioLinesIcon, DownloadIcon, RefreshCwIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/shared/empty-state";
import { Icon } from "@/components/shared/icon";
import { StatusChip, type StatusTone } from "@/components/shared/status-chip";
import type { SessionTabProps } from "@/components/console/sessions/detail/types";
import { formatDuration } from "@/lib/format";

/**
 * Recording tab (docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §2.4): a simple audio
 * player + download link for the signed URL `GET /v1/sessions/{id}` already
 * returns in `session.recording.url` (CONTRACTS-V2 §3.4). The console proxy
 * follows redirects by default, so nothing here calls
 * `GET /v1/sessions/{id}/recording` directly — that 302 route is for
 * external, non-proxied consumers (e.g. `<a href>` straight to the api).
 */
const STATUS_TONE: Record<string, StatusTone> = {
  none: "neutral",
  requested: "info",
  active: "info",
  ready: "success",
  failed: "danger",
};

const STATUS_LABEL: Record<string, string> = {
  none: "No recording",
  requested: "Requested",
  active: "Recording",
  ready: "Ready",
  failed: "Failed",
};

export function RecordingTab({ session }: SessionTabProps) {
  const queryClient = useQueryClient();
  const [expired, setExpired] = React.useState(false);
  const recording = session.recording ?? { status: "none" };
  const status = recording.status ?? "none";

  function refresh() {
    setExpired(false);
    queryClient.invalidateQueries({ queryKey: ["sessions", session.id] });
  }

  if (status !== "ready" || !recording.url) {
    return (
      <EmptyState
        icon={AudioLinesIcon}
        title={STATUS_LABEL[status] ?? status}
        description={
          status === "failed"
            ? (recording.error ?? "The recording could not be produced for this session.")
            : status === "none"
              ? "This session's agent doesn't have recording turned on, or the call hasn't ended yet."
              : "The recording is still being processed — check back in a moment."
        }
        action={
          status === "requested" || status === "active" ? (
            <Button type="button" variant="outline" size="sm" onClick={refresh}>
              <Icon as={RefreshCwIcon} size="sm" />
              Refresh
            </Button>
          ) : undefined
        }
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <StatusChip tone={STATUS_TONE[status]}>{STATUS_LABEL[status]}</StatusChip>
        {typeof recording.duration_s === "number" ? (
          <span className="font-mono text-xs tabular-nums text-muted-foreground">
            {formatDuration(recording.duration_s * 1000)}
          </span>
        ) : null}
      </div>
      {expired ? (
        <EmptyState
          icon={AudioLinesIcon}
          compact
          title="This link expired"
          action={
            <Button type="button" variant="outline" size="sm" onClick={refresh}>
              <Icon as={RefreshCwIcon} size="sm" />
              Get a fresh link
            </Button>
          }
        />
      ) : (
        <audio controls src={recording.url} className="w-full" onError={() => setExpired(true)}>
          Your browser cannot play this recording.
        </audio>
      )}
      <div>
        <Button asChild variant="outline" size="sm">
          <a href={recording.url} download>
            <Icon as={DownloadIcon} size="sm" />
            Download
          </a>
        </Button>
      </div>
    </div>
  );
}
