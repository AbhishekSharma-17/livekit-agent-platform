"use client";

/**
 * `consent` block — a question the caller accepts or declines, for example
 * before the call is recorded or as the "you're talking to an AI" notice
 * (`request_consent` / `record_consent`, CONTRACTS-V2 §4.4, V5-15 → V5-17).
 *
 * `status` is the shared request lifecycle (`RequestableState`, like `form`
 * and `choices`): `"requested"` is the pending marker, answered through
 * `useBlockRequest` (`block_submit {block_id, values: {accepted: true |
 * false}}`). Unlike `choices`, a consent question offers exactly two
 * answers and no "not now" — declining *is* the answer, so there is nothing
 * to cancel. A spoken answer (`record_consent`) flips `status` to
 * `"submitted"` the same way, with no local `submit()` call.
 *
 * The persistent "you're talking to an AI assistant" banner
 * (`config.show_banner`) is a different, panel-level thing — it does not
 * come from this component (see `panels/composite/banner.tsx` and
 * `docs/v5/_asks.md`): a banner tied to this block's own position would
 * disappear the moment the block scrolled out of view, but the point of the
 * banner is that it never does.
 */
import * as React from "react";

import { StatusChip } from "@/components/shared/status-chip";
import { Button } from "@/components/ui/button";
import type { ConsentBlockState } from "@/contracts/lkap-contracts";
import { formatDateTime } from "@/lib/format";
import { useBlockRequest } from "@/panels/composite/use-block-request";
import { PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

type Choice = "accept" | "decline";

function ConsentQuestion({
  blockId,
  text,
  perform,
}: {
  blockId: string;
  text: string;
  perform: BlockRenderProps["panel"]["perform"];
}) {
  const [choice, setChoice] = React.useState<Choice | null>(null);
  const { sending, error, submit } = useBlockRequest(blockId, "requested", perform);
  const busy = sending !== null;

  function answer(next: Choice) {
    if (busy) return;
    setChoice(next);
    void submit({ accepted: next === "accept" });
  }

  return (
    <div data-slot="block-consent" className="flex flex-col gap-3">
      {text && <p className="text-sm leading-relaxed whitespace-pre-wrap">{text}</p>}
      {error && (
        <p role="alert" className="text-danger-text text-[0.8125rem]">
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" disabled={busy} onClick={() => answer("accept")}>
          {busy && choice === "accept" ? "Accepting…" : "Accept"}
        </Button>
        <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => answer("decline")}>
          {busy && choice === "decline" ? "Declining…" : "Decline"}
        </Button>
      </div>
    </div>
  );
}

function AnsweredConsent({ data }: { data: ConsentBlockState }) {
  if (data.accepted === null || data.accepted === undefined) {
    return <StatusChip tone="neutral" size="sm">No answer recorded</StatusChip>;
  }
  const method = data.method === "voice" ? "by voice" : "by tap";
  return (
    <div data-slot="block-consent-answered" className="flex flex-col gap-1">
      <StatusChip tone={data.accepted ? "success" : "neutral"} size="sm" dot>
        {data.accepted ? "Agreed" : "Declined"}
      </StatusChip>
      <p className="text-muted-foreground text-[0.8125rem]">
        {method}
        {typeof data.at === "number" ? ` · ${formatDateTime(data.at, { seconds: true })}` : null}
      </p>
    </div>
  );
}

export function ConsentBlock({ spec, data, panel, title, highlighted }: BlockRenderProps<ConsentBlockState>) {
  const status = data.status ?? "idle";
  const text = typeof data.text === "string" ? data.text : "";

  let body: React.ReactNode;
  if (status === "requested") {
    body = <ConsentQuestion blockId={spec.id} text={text} perform={panel.perform} />;
  } else if (status === "submitted") {
    body = <AnsweredConsent data={data} />;
  } else if (status === "cancelled") {
    body = <PanelEmpty>You dismissed this without answering.</PanelEmpty>;
  } else if (text) {
    body = <p className="text-muted-foreground text-sm">{text}</p>;
  } else {
    body = <PanelEmpty>The agent will ask for your agreement here when it needs it.</PanelEmpty>;
  }

  return (
    <BlockFrame spec={spec} title={title} highlighted={highlighted}>
      {body}
    </BlockFrame>
  );
}
