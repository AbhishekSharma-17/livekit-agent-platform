"use client";

/**
 * "Does this look right?" — the one intent this panel can send.
 *
 * The sketch itself is pinned to the board; this card carries the question and
 * the confirmation, which goes back to `Pack.on_ui_action` as
 * `ui_action confirm_sketch` (the panel's only outbound channel).
 */
import * as React from "react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import type { PanelUiAction } from "@/panels/registry";

import { Card } from "./studio";
import type { NotebookSketch } from "./state";

export function SketchCard({
  sketch,
  perform,
}: {
  sketch: NotebookSketch;
  perform: (action: PanelUiAction) => Promise<unknown>;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (sketch.confirmed) {
    return (
      <Card title="Incident sketch">
        <p className="text-sm text-emerald-300" data-testid="notebook-sketch-confirmed">
          Sketch v{sketch.version} confirmed. Thanks — it goes in the packet.
        </p>
      </Card>
    );
  }

  async function confirm() {
    setPending(true);
    setError(null);
    try {
      await perform({
        action: "ui_action",
        payload: {
          name: "confirm_sketch",
          data: { asset_id: sketch.asset_id, version: sketch.version },
        },
      });
    } catch {
      setError("Could not send that just now — tell the agent instead.");
    } finally {
      setPending(false);
    }
  }

  return (
    <Card title="Incident sketch">
      <p className="mb-2 text-sm leading-snug">
        Does this look right? Sketch v{sketch.version} is pinned to the notebook.
      </p>
      {sketch.brief && (
        <p className="text-muted-foreground/80 mb-2.5 text-[0.7rem] leading-snug">
          Drawn from: {sketch.brief}
        </p>
      )}
      <Button
        size="sm"
        className="w-full"
        onClick={confirm}
        disabled={pending}
        data-testid="notebook-confirm-sketch"
      >
        {pending ? "Sending…" : "Yes, that's right"}
      </Button>
      {error && (
        <p role="status" className="mt-2 text-[0.7rem] text-red-300">
          {error}
        </p>
      )}
    </Card>
  );
}
