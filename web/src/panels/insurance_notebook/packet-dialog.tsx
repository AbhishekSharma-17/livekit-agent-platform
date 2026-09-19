"use client";

/**
 * The adjuster packet: the markdown handoff the claim workflow writes
 * (`custom.packet_markdown`), with the structured handoff summary above it —
 * a port of the demo's packet dialog.
 *
 * The dialog is fully labelled (`DialogTitle` + `DialogDescription` wired to
 * the content through Radix) so screen readers announce what opened.
 */
import * as React from "react";
import { Streamdown } from "streamdown";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

export function PacketDialog({
  markdown,
  handoff,
}: {
  markdown: string;
  handoff: Record<string, string>;
}) {
  const entries = Object.entries(handoff);
  const ready = markdown.trim().length > 0;

  return (
    <Dialog>
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
      <DialogContent className="max-h-[85vh] overflow-hidden sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Adjuster packet</DialogTitle>
          <DialogDescription>
            The handoff the claim team has written so far. It updates as the
            conversation goes on.
          </DialogDescription>
        </DialogHeader>

        <div className="max-h-[65vh] overflow-y-auto pr-1">
          {entries.length > 0 && (
            <dl className="mb-5 grid gap-2 sm:grid-cols-2">
              {entries.map(([term, value]) => (
                <div
                  key={term}
                  className="border-border/60 bg-muted/30 rounded-lg border px-3 py-2"
                >
                  <dt className="text-muted-foreground text-[0.65rem] tracking-[0.06em] uppercase">
                    {term}
                  </dt>
                  <dd className="mt-1 text-sm leading-snug break-words">
                    {value}
                  </dd>
                </div>
              ))}
            </dl>
          )}
          <div
            className="prose-sm max-w-none [&_h1]:mt-0 [&_h1]:mb-3 [&_h1]:text-lg [&_h1]:font-semibold [&_h2]:mt-5 [&_h2]:mb-2 [&_h2]:text-base [&_h2]:font-semibold [&_li]:my-1 [&_p]:my-2 [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5"
            data-testid="notebook-packet-body"
          >
            <Streamdown>{markdown}</Streamdown>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
