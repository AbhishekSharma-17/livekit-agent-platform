"use client";

/**
 * The four `notebook` section kinds (V6-08 → V6-10, D-V6-15): `text` running
 * notes, a `checklist`, a `details` summary card, and the `ink` placeholder
 * until the drawing board (V6-12).
 *
 * Every section reads its content defensively — `blockStateOf` (`catalog.ts`)
 * is a shallow merge of the block's top-level state keys, so a live
 * `sections` map that is missing this section (a config changed mid-session)
 * or holds the wrong `kind` under this id falls back to the section's own
 * empty content, exactly like the worker's `ui.blocks.notebook_section_state`.
 *
 * `caller_can_write` (the block's config) is the single switch for every
 * caller affordance here: adding/editing/removing a note, ticking a
 * checklist item, changing a details row's value — all through the shared
 * `useCallerEdit` hook and `NotebookEdit`'s three shapes (`ui_protocol.py`).
 */
import * as React from "react";
import { useEffect, useRef, useState } from "react";
import { PencilIcon, PlusIcon, XIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { CheckGlyph, PanelEmpty } from "@/panels/generic/blocks";
import { SafeMarkdown } from "@/lib/safe-markdown";
import type {
  ChecklistItem,
  DetailsItem,
  NotebookChecklistSection,
  NotebookDetailsSection,
  NotebookEntry,
  NotebookInkSection,
  NotebookTextSection,
} from "@/contracts/lkap-contracts";
import { cn } from "@/lib/utils";
import { panelLayoutOf } from "@/panels/composite/layout";
import type { PanelProps } from "@/panels/registry";

// `Block` back from `../index` (`blocks/index.tsx`) — safe: `notebook.tsx` (this file's
// only importer) is itself reached from `index.tsx` through a dynamic `import()`
// (`React.lazy`), so `index.tsx` has already finished evaluating by the time this
// module runs, exactly like `blocks/layout.tsx`'s own back-reference (see its docblock).
import { Block } from "../index";
import { MAX_CALLER_EDIT_CHARS, useCallerEdit } from "./use-caller-edit";

export type NotebookSectionKind = "text" | "checklist" | "details" | "ink";

export interface NotebookSectionConfigLike {
  id: string;
  title: string;
  kind: NotebookSectionKind;
  /** An `ink` section's drawing board (V6-12, D-V6-16); `null` while it has none yet. */
  canvasBlockId: string | null;
}

export type NotebookSectionContent =
  | NotebookTextSection
  | NotebookChecklistSection
  | NotebookDetailsSection
  | NotebookInkSection
  | undefined;

function isKind<K extends NotebookSectionKind>(
  content: NotebookSectionContent,
  kind: K,
): content is Extract<NotebookTextSection | NotebookChecklistSection | NotebookDetailsSection | NotebookInkSection, { kind?: K }> {
  return !!content && (content as { kind?: string }).kind === kind;
}

/** The section's real content, or its empty content when missing or the wrong kind (see file docblock). */
export function sectionContent(
  sections: Record<string, NotebookSectionContent> | undefined,
  section: NotebookSectionConfigLike,
): NotebookSectionContent {
  const content = sections?.[section.id];
  return isKind(content, section.kind) ? content : undefined;
}

interface SectionBaseProps {
  blockId: string;
  section: NotebookSectionConfigLike;
  callerCanWrite: boolean;
  perform: PanelProps["perform"];
}

function ErrorLine({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <p role="alert" className="text-danger-text mt-1 text-[0.8125rem]">
      {error}
    </p>
  );
}

function CallerMark({ children }: { children: React.ReactNode }) {
  return (
    <span data-slot="notebook-caller-mark" className="lkap-notebook-caller-mark text-muted-foreground ml-1.5 not-italic">
      · {children}
    </span>
  );
}

/* -------------------------------------------------------------------------- */
/* text                                                                        */
/* -------------------------------------------------------------------------- */

/**
 * Which entries just arrived, for the ink-in animation. Seeded with every id
 * already present on first render (a reconnect snapshot never animates), so
 * only entries that appear *after* the section is mounted get the effect;
 * a caller's own new note never animates either (they just wrote it).
 */
function useFreshEntries(ids: readonly string[]): ReadonlySet<string> {
  const seen = useRef<Set<string> | null>(null);
  if (seen.current === null) seen.current = new Set(ids);
  const fresh = new Set(ids.filter((id) => !seen.current!.has(id)));
  useEffect(() => {
    for (const id of ids) seen.current!.add(id);
  });
  return fresh;
}

function TextEntryRow({
  entry,
  fresh,
  editable,
  perform,
  blockId,
  sectionId,
}: {
  entry: NotebookEntry;
  fresh: boolean;
  editable: boolean;
  perform: PanelProps["perform"];
  blockId: string;
  sectionId: string;
}) {
  const { sending, error, clearError, send } = useCallerEdit(blockId, perform);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(entry.text);

  async function save() {
    const text = draft.trim();
    if (text.length === 0 || text.length > MAX_CALLER_EDIT_CHARS) return;
    const result = await send({ section_id: sectionId, entry_id: entry.id, text });
    if (result.ok) setEditing(false);
  }

  async function remove() {
    await send({ section_id: sectionId, entry_id: entry.id, text: "" });
  }

  if (editing) {
    return (
      <li className="lkap-notebook-entry flex flex-col gap-1.5" data-slot="notebook-entry" data-entry-id={entry.id}>
        <Input
          autoFocus
          value={draft}
          maxLength={MAX_CALLER_EDIT_CHARS}
          aria-label="Edit note text"
          onChange={(event) => {
            clearError();
            setDraft(event.target.value);
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              setEditing(false);
              setDraft(entry.text);
            }
          }}
        />
        <div className="flex items-center gap-2">
          <Button type="button" size="sm" disabled={sending || draft.trim().length === 0} onClick={() => void save()}>
            {sending ? "Saving…" : "Save"}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={sending}
            onClick={() => {
              setEditing(false);
              setDraft(entry.text);
              clearError();
            }}
          >
            Cancel
          </Button>
          <span className="text-muted-foreground ml-auto text-[0.6875rem] tabular-nums">
            {draft.length}/{MAX_CALLER_EDIT_CHARS}
          </span>
        </div>
        <ErrorLine error={error} />
      </li>
    );
  }

  return (
    <li
      className={cn("lkap-notebook-entry group flex items-start gap-1.5", fresh && "lkap-notebook-ink-in")}
      data-slot="notebook-entry"
      data-entry-id={entry.id}
      data-author={entry.author ?? "agent"}
    >
      <span className="min-w-0 flex-1 leading-snug break-words">
        <SafeMarkdown text={entry.text} allowLinks className="inline [&_p]:inline [&_p]:m-0" />
        {entry.author === "caller" ? (
          <CallerMark>written by you</CallerMark>
        ) : entry.edited_by === "caller" ? (
          <CallerMark>changed by you</CallerMark>
        ) : null}
      </span>
      {editable && (
        <span className="flex shrink-0 items-center gap-0.5 opacity-0 focus-within:opacity-100 group-hover:opacity-100">
          <Button type="button" variant="ghost" size="icon-xs" aria-label="Edit this note" onClick={() => setEditing(true)}>
            <Icon as={PencilIcon} size="sm" />
          </Button>
          <Button type="button" variant="ghost" size="icon-xs" aria-label="Remove this note" disabled={sending} onClick={() => void remove()}>
            <Icon as={XIcon} size="sm" />
          </Button>
        </span>
      )}
      {!editing && <ErrorLine error={error} />}
    </li>
  );
}

function AddNoteForm({ blockId, sectionId, perform }: { blockId: string; sectionId: string; perform: PanelProps["perform"] }) {
  const { sending, error, clearError, send } = useCallerEdit(blockId, perform);
  const [draft, setDraft] = useState("");

  async function add() {
    const text = draft.trim();
    if (text.length === 0 || text.length > MAX_CALLER_EDIT_CHARS) return;
    const result = await send({ section_id: sectionId, text });
    if (result.ok) setDraft("");
  }

  return (
    <form
      className="mt-2 flex flex-col gap-1.5"
      onSubmit={(event) => {
        event.preventDefault();
        void add();
      }}
    >
      <div className="flex items-center gap-2">
        <Input
          value={draft}
          placeholder="Add a note"
          maxLength={MAX_CALLER_EDIT_CHARS}
          aria-label="Add a note"
          onChange={(event) => {
            clearError();
            setDraft(event.target.value);
          }}
        />
        <Button type="submit" size="sm" disabled={sending || draft.trim().length === 0}>
          <Icon as={PlusIcon} size="sm" />
          {sending ? "Adding…" : "Add"}
        </Button>
      </div>
      <ErrorLine error={error} />
    </form>
  );
}

export function TextSectionView({
  blockId,
  section,
  content,
  callerCanWrite,
  perform,
}: SectionBaseProps & { content: NotebookTextSection | undefined }) {
  const entries = content?.entries ?? [];
  const fresh = useFreshEntries(entries.map((entry) => entry.id));

  return (
    <div data-slot="notebook-section-text">
      {entries.length === 0 ? (
        <PanelEmpty>Nothing written here yet.</PanelEmpty>
      ) : (
        <ul className="lkap-notebook-entries flex flex-col gap-2">
          {entries.map((entry) => (
            <TextEntryRow
              key={entry.id}
              entry={entry}
              fresh={fresh.has(entry.id) && entry.author !== "caller"}
              editable={callerCanWrite}
              perform={perform}
              blockId={blockId}
              sectionId={section.id}
            />
          ))}
        </ul>
      )}
      {callerCanWrite && <AddNoteForm blockId={blockId} sectionId={section.id} perform={perform} />}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* checklist                                                                   */
/* -------------------------------------------------------------------------- */

function ChecklistItemRow({
  item,
  editable,
  blockId,
  sectionId,
  perform,
}: {
  item: ChecklistItem;
  editable: boolean;
  blockId: string;
  sectionId: string;
  perform: PanelProps["perform"];
}) {
  const { sending, error, send } = useCallerEdit(blockId, perform);
  const done = item.done ?? false;

  if (!editable) {
    return (
      <li className="flex items-start gap-2.5" data-slot="notebook-checklist-item" data-done={done}>
        <CheckGlyph done={done} />
        <div className="min-w-0 flex-1">
          <p className={cn("text-sm leading-snug", done && "text-muted-foreground line-through")}>{item.label}</p>
          {item.hint && <p className="text-muted-foreground mt-0.5 text-xs">{item.hint}</p>}
        </div>
      </li>
    );
  }

  return (
    <li className="flex flex-col gap-0.5" data-slot="notebook-checklist-item" data-done={done}>
      <label className="flex cursor-pointer items-start gap-2.5">
        <Checkbox
          className="mt-0.5"
          checked={done}
          disabled={sending}
          aria-label={item.label}
          onCheckedChange={(checked) => void send({ section_id: sectionId, item_id: item.id, done: checked === true })}
        />
        <span className="min-w-0 flex-1">
          <span className={cn("text-sm leading-snug", done && "text-muted-foreground line-through")}>{item.label}</span>
          {item.edited_by === "caller" && <CallerMark>changed by you</CallerMark>}
          {item.hint && <span className="text-muted-foreground mt-0.5 block text-xs">{item.hint}</span>}
        </span>
      </label>
      <ErrorLine error={error} />
    </li>
  );
}

export function ChecklistSectionView({
  blockId,
  section,
  content,
  callerCanWrite,
  perform,
}: SectionBaseProps & { content: NotebookChecklistSection | undefined }) {
  const items = content?.items ?? [];
  if (items.length === 0) return <PanelEmpty>Nothing on this list yet.</PanelEmpty>;
  return (
    <ul className="flex flex-col gap-1.5" data-slot="notebook-section-checklist">
      {items.map((item) => (
        <ChecklistItemRow key={item.id} item={item} editable={callerCanWrite} blockId={blockId} sectionId={section.id} perform={perform} />
      ))}
    </ul>
  );
}

/* -------------------------------------------------------------------------- */
/* details                                                                     */
/* -------------------------------------------------------------------------- */

function DetailsRowView({
  item,
  editable,
  blockId,
  sectionId,
  perform,
}: {
  item: DetailsItem;
  editable: boolean;
  blockId: string;
  sectionId: string;
  perform: PanelProps["perform"];
}) {
  const { sending, error, clearError, send } = useCallerEdit(blockId, perform);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(String(item.value ?? ""));
  const empty = item.value === null || item.value === undefined || item.value === "";

  async function save() {
    if (draft.length > MAX_CALLER_EDIT_CHARS) return;
    const result = await send({ section_id: sectionId, key: item.key, value: draft });
    if (result.ok) setEditing(false);
  }

  return (
    <div className="flex flex-col gap-0.5" data-slot="notebook-details-item" data-key={item.key}>
      <dt className="text-muted-foreground text-xs">{item.label}</dt>
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
                setDraft(String(item.value ?? ""));
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
                setDraft(String(item.value ?? ""));
                clearError();
              }}
            >
              Cancel
            </Button>
          </div>
          <ErrorLine error={error} />
        </div>
      ) : (
        <dd className="group flex items-center gap-1.5 text-sm">
          <span className={cn(empty && "text-muted-foreground italic")}>{empty ? "Not yet" : String(item.value)}</span>
          {item.edited_by === "caller" && <CallerMark>changed by you</CallerMark>}
          {editable && (
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

export function DetailsSectionView({
  blockId,
  section,
  content,
  callerCanWrite,
  perform,
}: SectionBaseProps & { content: NotebookDetailsSection | undefined }) {
  const items = content?.items ?? [];
  if (items.length === 0) return <PanelEmpty>Nothing filled in yet.</PanelEmpty>;
  return (
    <dl className="flex flex-col gap-2.5" data-slot="notebook-section-details">
      {items.map((item) => (
        <DetailsRowView key={item.key} item={item} editable={callerCanWrite} blockId={blockId} sectionId={section.id} perform={perform} />
      ))}
    </dl>
  );
}

/* -------------------------------------------------------------------------- */
/* ink (V6-12 placeholder)                                                     */
/* -------------------------------------------------------------------------- */

/**
 * (V6-12, D-V6-16, ask #93; the board itself is V6-14): a section whose config names a
 * board (`canvas_block_id`) renders it inside the section — never in the panel's own
 * top-level flow (`composite/index.tsx` hides it there, the same way a `layout` hides its
 * children). `<Block>` resolves the `canvas` spec to the real drawing board
 * (`blocks/canvas.tsx`, lazy); a section with no board yet keeps "Drawing board coming
 * soon."
 */
export function InkSectionView({
  canvasBlockId,
  callerCanDraw,
  panel,
}: {
  canvasBlockId: string | null;
  callerCanDraw: boolean;
  panel: PanelProps;
}) {
  if (canvasBlockId) {
    const canvasSpec = panelLayoutOf(panel.agent).blocks.find((spec) => spec.id === canvasBlockId && spec.type === "canvas");
    if (canvasSpec) return <Block spec={canvasSpec} {...panel} />;
  }
  return (
    <div data-slot="notebook-section-ink" className="border-border bg-muted/30 flex flex-col items-center gap-1 rounded-md border border-dashed px-4 py-6 text-center">
      <p className="text-muted-foreground text-sm">Drawing board coming soon.</p>
      {callerCanDraw && <p className="text-muted-foreground text-xs">Drawing is not available yet.</p>}
    </div>
  );
}
