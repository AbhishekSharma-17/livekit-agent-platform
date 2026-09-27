"use client";

/**
 * `captions` block — large live captions of both sides of the call (V5-31 ->
 * V5-35): reads `lkap.captions` through `useCaptionsStream`
 * (`composite/captions-stream.ts`) and shows each side's current utterance,
 * replacing an interim line with its final one in place. `show_user`/
 * `show_agent` pick which sides render; a language chip appears on a line
 * whose `CaptionSegment.language` differs from the conversation's current
 * language (`data.language`, kept by the worker).
 *
 * **`position: "bottom"`** (over the video, on avatar layouts): the config's
 * own docstring calls for the captions to overlay the video block elsewhere
 * on the panel, which needs compositing across blocks — `composite/index.tsx`
 * and `blocks/video.tsx` are outside this package's files (V5-35's card
 * lists only this file, `catalog`/`index`/`types`, `captions-stream.ts`,
 * `transcript.tsx` and `block-config-form.tsx`). This renders a
 * self-contained approximation instead: a `sticky bottom-0` strip within the
 * block's own flow, which stays pinned to the bottom of the scrolling panel
 * rather than truly layering on the video element. An ask is filed for a
 * real video overlay.
 */
import * as React from "react";

import { StatusChip } from "@/components/shared/status-chip";
import type { CaptionSegment, CaptionsBlockState } from "@/contracts/lkap-contracts";
import { cn } from "@/lib/utils";
import { languageLabel } from "@/panels/blocks/catalog";
import { latestSegmentFor, useCaptionsStream } from "@/panels/composite/captions-stream";
import { PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

const SPEAKER_LABEL: Record<CaptionSegment["speaker"], string> = { user: "You", agent: "Agent" };

function CaptionLine({ segment, defaultLanguage }: { segment: CaptionSegment; defaultLanguage: string | null }) {
  const showChip = segment.language && segment.language !== defaultLanguage;
  return (
    <p
      data-slot="block-captions-line"
      data-speaker={segment.speaker}
      className={cn("text-pretty text-lg leading-snug font-medium", segment.speaker === "user" ? "text-right" : "text-left")}
    >
      <span className="mr-2 align-middle text-xs font-semibold tracking-wide text-muted-foreground uppercase">
        {SPEAKER_LABEL[segment.speaker]}
      </span>
      {showChip ? (
        <StatusChip tone="neutral" size="sm" className="mr-2 align-middle">
          {languageLabel(segment.language as string)}
        </StatusChip>
      ) : null}
      <span className={cn(!segment.final && "text-muted-foreground italic")}>{segment.text}</span>
    </p>
  );
}

export function CaptionsBlock({ spec, data, title, highlighted }: BlockRenderProps<CaptionsBlockState>) {
  const config = spec.config as { show_user?: boolean; show_agent?: boolean; position?: "block" | "bottom" };
  const showUser = config.show_user !== false;
  const showAgent = config.show_agent !== false;
  const position = config.position === "bottom" ? "bottom" : "block";

  const segments = useCaptionsStream();
  const userLine = showUser ? latestSegmentFor(segments, "user") : null;
  const agentLine = showAgent ? latestSegmentFor(segments, "agent") : null;
  const lines = [agentLine, userLine].filter((s): s is CaptionSegment => s !== null).sort((a, b) => a.ts - b.ts);

  const body =
    lines.length === 0 ? (
      <PanelEmpty>Captions appear here once someone speaks.</PanelEmpty>
    ) : (
      <div data-slot="block-captions" className="flex flex-col gap-2">
        {lines.map((segment) => (
          <CaptionLine key={segment.speaker} segment={segment} defaultLanguage={data.language ?? null} />
        ))}
      </div>
    );

  return (
    <BlockFrame
      spec={spec}
      title={title}
      highlighted={highlighted}
      className={position === "bottom" ? "sticky bottom-0 z-10 border-t border-border bg-background/95 backdrop-blur-sm" : undefined}
    >
      {body}
    </BlockFrame>
  );
}

export default CaptionsBlock;
