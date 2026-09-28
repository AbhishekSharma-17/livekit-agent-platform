"use client";

/**
 * `checklist` block — the envelope's `checklist` (WP-9 `ChecklistBlock`).
 *
 * `config.caller_can_edit` (V6-06, D-V6-19, ask #24) lets the caller tick an
 * item on screen: a real, focusable `Checkbox` in place of the read-only
 * `CheckGlyph`, sending `block_action {block_id, name: "edit", data:
 * ChecklistEdit {item_id, done}}` through the shared `useCallerEdit` hook
 * (`notebook/use-caller-edit.ts`) — the same wire the `notebook` block's own
 * checklist sections use. `edited_by === "caller"` shows "changed by you".
 */
import * as React from "react";

import { StatusChip } from "@/components/shared/status-chip";
import { Checkbox } from "@/components/ui/checkbox";
import type { ChecklistItem } from "@/contracts/lkap-contracts";
import { CheckGlyph, PanelEmpty } from "@/panels/generic/blocks";
import { useCallerEdit } from "@/panels/blocks/notebook/use-caller-edit";
import { cn } from "@/lib/utils";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

function CallerMark() {
  return <span className="text-muted-foreground ml-1.5 text-[0.6875rem]">· changed by you</span>;
}

function EditableRow({
  item,
  blockId,
  perform,
}: {
  item: ChecklistItem;
  blockId: string;
  perform: BlockRenderProps["panel"]["perform"];
}) {
  const { sending, error, send } = useCallerEdit(blockId, perform);
  const done = item.done ?? false;
  return (
    <li className="flex flex-col gap-0.5" data-slot="panel-checklist-item" data-done={done ? "true" : "false"}>
      <label className="flex cursor-pointer items-start gap-2.5">
        <Checkbox
          className="mt-0.5"
          checked={done}
          disabled={sending}
          aria-label={item.label}
          onCheckedChange={(checked) => void send({ item_id: item.id, done: checked === true })}
        />
        <span className="min-w-0 flex-1">
          <span className={cn("text-sm leading-snug", done && "text-muted-foreground line-through")}>
            {item.label}
            {item.blocking && !done && (
              <StatusChip tone="warning" size="sm" className="ml-1.5 align-middle">
                Required
              </StatusChip>
            )}
          </span>
          {item.edited_by === "caller" && <CallerMark />}
          {item.hint && <span className="text-muted-foreground mt-0.5 block text-xs">{item.hint}</span>}
        </span>
      </label>
      {error && (
        <p role="alert" className="text-danger-text ml-6.5 text-[0.8125rem]">
          {error}
        </p>
      )}
    </li>
  );
}

function ReadOnlyRow({ item }: { item: ChecklistItem }) {
  const done = item.done ?? false;
  return (
    <li className="flex items-start gap-2.5" data-slot="panel-checklist-item" data-done={done ? "true" : "false"}>
      <CheckGlyph done={done} />
      <div className="min-w-0 flex-1">
        <p className={cn("text-sm leading-snug", done && "text-muted-foreground line-through")}>
          {item.label}
          {item.blocking && !done && (
            <StatusChip tone="warning" size="sm" className="ml-1.5 align-middle">
              Required
            </StatusChip>
          )}
        </p>
        {item.hint && <p className="text-muted-foreground mt-0.5 text-xs">{item.hint}</p>}
      </div>
    </li>
  );
}

export function ChecklistBlock({ spec, panel, title, highlighted }: BlockRenderProps) {
  const items = panel.state.checklist ?? [];
  const editable = (spec.config as { caller_can_edit?: unknown } | null)?.caller_can_edit === true;

  return (
    <BlockFrame spec={spec} title={title} count={items.length} highlighted={highlighted}>
      {items.length === 0 ? (
        <PanelEmpty>No open items.</PanelEmpty>
      ) : (
        <ul data-slot="panel-checklist" className="space-y-2">
          {items.map((item) =>
            editable ? (
              <EditableRow key={item.id} item={item} blockId={spec.id} perform={panel.perform} />
            ) : (
              <ReadOnlyRow key={item.id} item={item} />
            ),
          )}
        </ul>
      )}
    </BlockFrame>
  );
}
