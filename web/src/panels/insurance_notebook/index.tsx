"use client";

/**
 * Insurance claim notebook — the `insurance_notebook` panel (CONTRACTS §11,
 * docs/INSURANCE_PACK_MAPPING.md #17–#23).
 *
 * A port of the Gemini demo's notebook: an adjuster's ruled pad that the agent
 * writes on while the claimant talks — handwritten lines, red blanks for the
 * facts still missing, taped camera polaroids and the pen sketch, the rubber
 * stamp for the routing decision — with the desk beside it holding the "still
 * needed" list, the claim team's activity feed and the adjuster packet.
 *
 * It is a pure renderer of the `UiState` envelope: the pack composes every
 * line server-side (`packs/insurance_claim/ui_state.py`), and the single way
 * out is `perform({action: "ui_action", ...})` for the sketch confirmation.
 */
import * as React from "react";
import { useMemo } from "react";

import type { PanelDefinition, PanelProps } from "@/panels/registry";

import { NOTEBOOK_CSS } from "./notebook-styles";
import { Notes, Pen, Pinboard, Stamp, isWriting } from "./paper";
import { PacketDialog } from "./packet-dialog";
import { SketchCard } from "./sketch-card";
import { headerLine, parseNotebookCustom } from "./state";
import { StillNeeded, TeamFeed } from "./studio";

/** Punch holes down the left margin; decorative only. */
const HOLES = [0, 1, 2, 3, 4, 5, 6, 7];

export function InsuranceNotebookPanel({ state, assets, perform }: PanelProps) {
  const custom = useMemo(() => parseNotebookCustom(state.custom), [state.custom]);

  const notes = state.notes ?? [];
  const activity = state.activity ?? [];
  const checklist = state.checklist ?? [];

  // Only the two kinds the pack pins; anything else stays off the board.
  const pinned = useMemo(
    () =>
      (state.assets ?? []).filter(
        (asset) => asset.kind === "evidence" || asset.kind === "sketch",
      ),
    [state.assets],
  );

  const fields = Object.entries(custom.fields);
  const writing = isWriting(activity);
  const status = state.status;

  return (
    <div
      data-testid="insurance-notebook"
      className="lkap-notebook @container h-full overflow-y-auto"
    >
      {/* The paper's own stylesheet (see notebook-styles.ts for why it is inline). */}
      <style dangerouslySetInnerHTML={{ __html: NOTEBOOK_CSS }} />

      {/* The stamp is the loudest change on the page; announce it once. */}
      <p className="sr-only" aria-live="polite">
        {status ? `Claim status: ${status.label}` : ""}
      </p>

      <div className="grid items-start gap-4 p-3 @3xl:grid-cols-[minmax(0,1fr)_minmax(15rem,18rem)]">
        <article className="paper">
          <div aria-hidden className="holes">
            {HOLES.map((hole) => (
              <span key={hole} />
            ))}
          </div>

          <header className="page-head">
            <span className="page-title">First notice of loss</span>
            <span>{headerLine(custom)}</span>
          </header>

          <h2 className="sr-only">Claim notes</h2>
          <Notes notes={notes} />

          <h2 className="sr-only">Pinned evidence</h2>
          <Pinboard assets={pinned} urls={assets} />

          {fields.length > 0 && (
            <details className="sections mt-7">
              <summary>Claim details ({fields.length})</summary>
              <div className="mt-3 grid gap-1.5 sm:grid-cols-2">
                {fields.map(([key, field]) => (
                  <div key={key} className="field" data-status={field.status}>
                    <span className="f-label">{field.label}</span>
                    <span className="f-value">{field.value}</span>
                    <span className="f-source">{field.source}</span>
                  </div>
                ))}
              </div>
            </details>
          )}

          <Pen writing={writing} />
          <Stamp status={status} />
        </article>

        <div className="flex flex-col gap-3">
          <StillNeeded
            checklist={checklist}
            progress={state.progress}
            documents={custom.documents}
          />
          {custom.sketch && (
            <SketchCard sketch={custom.sketch} perform={perform} />
          )}
          <TeamFeed activity={activity} />
          <PacketDialog
            markdown={custom.packet_markdown}
            handoff={custom.handoff}
          />
        </div>
      </div>
    </div>
  );
}

export const INSURANCE_NOTEBOOK_PANEL_ID = "insurance_notebook";

export const INSURANCE_NOTEBOOK_PANEL: PanelDefinition = {
  id: INSURANCE_NOTEBOOK_PANEL_ID,
  title: "Claim notebook",
  Component: InsuranceNotebookPanel,
  layout: "wide",
};
