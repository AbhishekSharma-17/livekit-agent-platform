"use client";

/**
 * `details` block — a key-value "what we have so far" card (`set_details`,
 * CONTRACTS-V2 §4.4, V5-08 → V5-12): typed formatting for money, date, phone,
 * email and badge rows. `config.fields` seeds the starting rows; the agent
 * upserts by `key` and may add more.
 *
 * `config.caller_can_edit` (V6-06, D-V6-19, ask #24) lets the caller change a
 * row's value inline, sending `block_action {block_id, name: "edit", data:
 * DetailsEdit {key, value}}` through the shared `useCallerEdit` hook
 * (`notebook/use-caller-edit.ts`). `edited_by === "caller"` shows "changed by
 * you"; the caller cannot add rows or change labels/types.
 */
import * as React from "react";
import { useMemo, useState } from "react";
import { PencilIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { StatusChip } from "@/components/shared/status-chip";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { DetailsItem, DetailsBlockState } from "@/contracts/lkap-contracts";
import { EMPTY_VALUE, formatDateTime, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";
import { MAX_CALLER_EDIT_CHARS, useCallerEdit } from "@/panels/blocks/notebook/use-caller-edit";
import { PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import type { BlockRenderProps } from "./types";

type DetailsTone = NonNullable<DetailsItem["tone"]>;

/** Small dot, never a side stripe (UI_UX_SPEC §2.1). */
const TONE_DOT: Record<DetailsTone, string> = {
  neutral: "bg-muted-foreground",
  info: "bg-info",
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
};

const BARE_DATE = /^\d{4}-\d{2}-\d{2}$/;

function formatMoney(value: number): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(value);
}

/** One item's display text, by `type` (`table.tsx::formatCell` for the numeric/date rules). */
export function formatDetailsValue(item: Pick<DetailsItem, "value" | "type">): string {
  const value = item.value;
  if (value === null || value === undefined || value === "") return "Not yet";
  switch (item.type) {
    case "number": {
      const n = typeof value === "number" ? value : Number(value);
      return Number.isFinite(n) ? n.toLocaleString("en-US") : String(value);
    }
    case "money": {
      const n = typeof value === "number" ? value : Number(value);
      return Number.isFinite(n) ? formatMoney(n) : String(value);
    }
    case "date": {
      // A bare `YYYY-MM-DD` is a calendar date: show it as written, no timezone shift.
      if (typeof value === "string" && BARE_DATE.test(value)) return value;
      if (typeof value !== "string" && typeof value !== "number") return String(value);
      const text = formatDateTime(value);
      return text === EMPTY_VALUE ? String(value) : text;
    }
    default:
      return String(value);
  }
}

function columnsClass(columns: unknown): string {
  return columns === 2 ? "grid-cols-1 sm:grid-cols-2" : "grid-cols-1";
}

function DetailsRow({
  item,
  editable,
  blockId,
  perform,
}: {
  item: DetailsItem;
  editable: boolean;
  blockId: string;
  perform: BlockRenderProps["panel"]["perform"];
}) {
  const empty = item.value === null || item.value === undefined || item.value === "";
  const text = formatDetailsValue(item);
  const { sending, error, clearError, send } = useCallerEdit(blockId, perform);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(empty ? "" : String(item.value));

  async function save() {
    if (draft.length > MAX_CALLER_EDIT_CHARS) return;
    const result = await send({ key: item.key, value: draft });
    if (result.ok) setEditing(false);
  }

  return (
    <div data-slot="block-details-item" data-key={item.key} className="flex flex-col gap-0.5">
      <dt className="text-muted-foreground flex items-center gap-1.5 text-xs">
        {item.tone && item.type !== "badge" && (
          <span
            aria-hidden="true"
            data-slot="details-tone-dot"
            data-tone={item.tone}
            className={cn("size-1.5 shrink-0 rounded-full", TONE_DOT[item.tone])}
          />
        )}
        {item.label}
      </dt>
      {editing ? (
        <div className="flex flex-col gap-1.5">
          <Input
            autoFocus
            value={draft}
            maxLength={MAX_CALLER_EDIT_CHARS}
            aria-label={`${item.label} value`}
            onChange={(event) => {
              clearError();
              setDraft(event.target.value);
            }}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                setEditing(false);
                setDraft(empty ? "" : String(item.value));
              }
            }}
          />
          <div className="flex items-center gap-2">
            <Button type="button" size="sm" disabled={sending} onClick={() => void save()}>
              {sending ? "Saving…" : "Save"}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={sending}
              onClick={() => {
                setEditing(false);
                setDraft(empty ? "" : String(item.value));
                clearError();
              }}
            >
              Cancel
            </Button>
          </div>
          {error && (
            <p role="alert" className="text-danger-text text-[0.8125rem]">
              {error}
            </p>
          )}
        </div>
      ) : (
        <dd className={cn("group flex items-center gap-1.5 text-sm", empty && "text-muted-foreground italic")}>
          {item.type === "badge" && !empty ? (
            <StatusChip tone={item.tone ?? "neutral"} size="sm">
              {text}
            </StatusChip>
          ) : item.type === "phone" && !empty ? (
            <a href={`tel:${text}`} className="underline underline-offset-2">
              {text}
            </a>
          ) : item.type === "email" && !empty ? (
            <a href={`mailto:${text}`} className="underline underline-offset-2">
              {text}
            </a>
          ) : (
            text
          )}
          {typeof item.updated_at === "number" && (
            <span className="text-muted-foreground ml-1.5 text-[0.6875rem] not-italic">· {formatRelative(item.updated_at)}</span>
          )}
          {item.edited_by === "caller" && <span className="text-muted-foreground ml-1.5 text-[0.6875rem] not-italic">· changed by you</span>}
          {editable && item.type !== "badge" && (
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              aria-label={`Edit ${item.label}`}
              className="opacity-0 focus-visible:opacity-100 group-hover:opacity-100"
              onClick={() => setEditing(true)}
            >
              <Icon as={PencilIcon} size="sm" />
            </Button>
          )}
        </dd>
      )}
    </div>
  );
}

export function DetailsBlock({ spec, data, panel, title, highlighted }: BlockRenderProps<DetailsBlockState>) {
  const items = useMemo(() => (Array.isArray(data.items) ? data.items : []), [data.items]);
  const columns = (spec.config as { columns?: unknown } | null)?.columns;
  const editable = (spec.config as { caller_can_edit?: unknown } | null)?.caller_can_edit === true;

  return (
    <BlockFrame spec={spec} title={title} count={items.length} highlighted={highlighted}>
      {items.length === 0 ? (
        <PanelEmpty>No details yet.</PanelEmpty>
      ) : (
        <dl data-slot="block-details" className={cn("grid gap-x-4 gap-y-3", columnsClass(columns))}>
          {items.map((item) => (
            <DetailsRow key={item.key} item={item} editable={editable} blockId={spec.id} perform={panel.perform} />
          ))}
        </dl>
      )}
    </BlockFrame>
  );
}
