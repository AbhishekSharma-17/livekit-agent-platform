"use client";

/**
 * The adjuster packet: the markdown handoff the claim workflow writes
 * (`custom.packet_markdown`), with the structured handoff summary above it —
 * a port of the demo's packet dialog.
 *
 * The dialog is fully labelled (`DialogTitle` + `DialogDescription` wired to
 * the content through Radix) so screen readers announce what opened.
 *
 * It can also be opened by the agent: `lkap.ui.request` `open_dialog` with
 * `{dialog: "packet"}` (CONTRACTS-V2 §4.4, ruling R-V2-3b) reaches
 * `openPacketDialog()` below, which the panel's `handleRequest` calls. The
 * indirection exists because `PanelDefinition` is a module-level object while
 * the dialog's open state belongs to the mounted component: a mounted dialog
 * subscribes, and the request is declined when none is mounted.
 */
import * as React from "react";
import { useEffect, useRef, useState } from "react";
import { Streamdown } from "streamdown";

import { DescriptionList } from "@/components/shared/description-list";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

/** What `openPacketDialog()` was able to do. */
export type PacketOpenOutcome = "opened" | "empty" | "absent";

type PacketOpener = () => PacketOpenOutcome;

/** Mounted packet dialogs. Normally one; a set keeps remounts honest. */
const OPENERS = new Set<PacketOpener>();

/**
 * Open the adjuster packet on behalf of the agent.
 *
 * @returns `"opened"` when a mounted dialog opened, `"empty"` when the
 * workflow has not written a packet yet (nothing to show, so nothing opens),
 * `"absent"` when no notebook is mounted.
 */
export function openPacketDialog(): PacketOpenOutcome {
  let outcome: PacketOpenOutcome = "absent";
  for (const opener of OPENERS) {
    const result = opener();
    if (result === "opened") return "opened";
    outcome = result;
  }
  return outcome;
}

export function PacketDialog({
  markdown,
  handoff,
}: {
  markdown: string;
  handoff: Record<string, string>;
}) {
  const entries = Object.entries(handoff);
  const ready = markdown.trim().length > 0;
  const [open, setOpen] = useState(false);

  // The opener is registered once; `ready` is read through a ref so a packet
  // arriving mid-call does not churn the subscription.
  const readyRef = useRef(ready);
  readyRef.current = ready;
  useEffect(() => {
    const opener: PacketOpener = () => {
      if (!readyRef.current) return "empty";
      setOpen(true);
      return "opened";
    };
    OPENERS.add(opener);
    return () => {
      OPENERS.delete(opener);
    };
  }, []);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="w-full"
          disabled={!ready}
          data-testid="notebook-packet-trigger"
        >
          {ready ? "Read the adjuster packet" : "Packet not written yet"}
        </Button>
      </DialogTrigger>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>Adjuster packet</DialogTitle>
          <DialogDescription>
            The handoff the claim team has written so far. It updates as the
            conversation goes on.
          </DialogDescription>
        </DialogHeader>

        <DialogBody className="block">
          {entries.length > 0 && (
            <DescriptionList
              className="mb-5"
              columns={2}
              items={entries.map(([term, detail]) => ({ term, detail }))}
            />
          )}
          <div
            className="prose-sm max-w-none [&_h1]:mt-0 [&_h1]:mb-3 [&_h1]:text-lg [&_h1]:font-semibold [&_h2]:mt-5 [&_h2]:mb-2 [&_h2]:text-base [&_h2]:font-semibold [&_li]:my-1 [&_p]:my-2 [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5"
            data-testid="notebook-packet-body"
          >
            <Streamdown>{markdown}</Streamdown>
          </div>
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
