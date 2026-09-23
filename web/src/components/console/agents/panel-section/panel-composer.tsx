"use client";

/**
 * The no-code panel composer — the agent editor's "Panel & capabilities"
 * section (V2-11; UI_UX_SPEC-V2-AMENDMENTS §2.3): pick the panel, then, for
 * the block panel (`composite`), add, remove, reorder and configure blocks
 * with a live preview.
 *
 * Form contract (editor README rule 2): the composer owns `config.panel` and
 * writes the `ui_panel_id` mirror in the same change; `buildAgentUpdate`
 * sends both. Block tools (asks #69) are the four `config.tools.builtin_disabled`
 * switches the worker only registers when a matching block exists.
 *
 * Reordering: drag a row by its handle, or focus the handle and use ↑/↓.
 */
import * as React from "react";
import { useFormContext, useWatch } from "react-hook-form";
import {
  ActivityIcon,
  ChevronDownIcon,
  ClipboardListIcon,
  FileTextIcon,
  GaugeIcon,
  GripVerticalIcon,
  ImagesIcon,
  ListChecksIcon,
  MessagesSquareIcon,
  PlusIcon,
  PuzzleIcon,
  QuoteIcon,
  StickyNoteIcon,
  Table2Icon,
  Trash2Icon,
  VideoIcon,
  type LucideIcon,
} from "lucide-react";

import { Field } from "@/components/shared/field";
import { Icon } from "@/components/shared/icon";
import { Section, SectionRow } from "@/components/shared/section";
import { StatusChip } from "@/components/shared/status-chip";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { usePacks } from "@/components/console/lib/api-hooks";
import { BLOCK_TOOLS } from "@/components/console/lib/constants";
import type { AgentEditorForm, PanelLayoutForm } from "@/components/console/lib/schemas";
import { PANEL_META, panelMeta } from "@/components/shared/panel-meta";
import type { AgentOut } from "@/contracts/lkap-contracts";
import { cn } from "@/lib/utils";
import { BLOCK_CATALOG, BLOCK_TYPES, blockTitle } from "@/panels/blocks/catalog";
import { COMPOSITE_PANEL_ID, LEGACY_GENERIC_PANEL_ID, type BlockType } from "@/panels/composite/layout";

import { BlockConfigForm } from "./block-config-form";
import { addBlock, blockToolStatus, moveBlock, removeBlock, switchPanel } from "./composer-model";
// The preview renders real panel blocks against fixtures; load it on demand so
// the editor's first load stays free of the session surface.
const ComposerPreview = React.lazy(() => import("./composer-preview"));
import { SessionCapabilities } from "./session-capabilities";

export const BLOCK_ICONS: Record<BlockType, LucideIcon> = {
  status: GaugeIcon,
  notes: StickyNoteIcon,
  checklist: ListChecksIcon,
  activity: ActivityIcon,
  form: ClipboardListIcon,
  table: Table2Icon,
  document: FileTextIcon,
  gallery: ImagesIcon,
  kb_citations: QuoteIcon,
  transcript: MessagesSquareIcon,
  video: VideoIcon,
  custom: PuzzleIcon,
};

/**
 * Panels the author can pick: every registered panel (`PANEL_META`, whose
 * keys `tests/registry.test.tsx` holds equal to `PANELS` — read here so the
 * editor doesn't bundle every panel component); the legacy one only while in use.
 */
export function panelChoices(current: string): string[] {
  const ids = Object.keys(PANEL_META).filter((id) => id !== LEGACY_GENERIC_PANEL_ID || id === current);
  if (current && !ids.includes(current)) ids.push(current);
  // Blocks first — it's the one the composer is for.
  return [COMPOSITE_PANEL_ID, ...ids.filter((id) => id !== COMPOSITE_PANEL_ID)];
}

function PanelChoice({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const name = React.useId();
  return (
    <div role="radiogroup" aria-label="Panel" className="grid gap-2 sm:grid-cols-2" data-issue-path="panel.panel_id">
      {panelChoices(value).map((id) => {
        const meta = panelMeta(id);
        return (
          <label
            key={id}
            className={cn(
              "relative flex cursor-pointer flex-col gap-1 rounded-lg border border-border bg-card p-4",
              "transition-colors duration-(--dur-2) hover:bg-accent",
              "has-[:checked]:border-brand-line has-[:checked]:bg-brand-soft",
              "has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring has-[:focus-visible]:ring-offset-2 has-[:focus-visible]:ring-offset-background",
            )}
          >
            <input
              type="radio"
              name={name}
              value={id}
              checked={value === id}
              onChange={() => onChange(id)}
              className="sr-only"
            />
            <span className="flex items-center gap-2 text-sm font-semibold text-foreground">
              {meta.label}
              {meta.legacy ? (
                <StatusChip tone="neutral" size="sm">
                  Classic
                </StatusChip>
              ) : null}
            </span>
            <span className="text-[0.8125rem] leading-[1.125rem] text-pretty text-muted-foreground">{meta.description}</span>
            {id !== COMPOSITE_PANEL_ID ? <span className="font-mono text-xs text-muted-foreground">{id}</span> : null}
          </label>
        );
      })}
    </div>
  );
}

function LayoutChoice({ value, onChange }: { value: "side" | "wide"; onChange: (layout: "side" | "wide") => void }) {
  const name = React.useId();
  const options = [
    { value: "side" as const, label: "Beside the call", hint: "A 400 px column next to the agent." },
    { value: "wide" as const, label: "Main column", hint: "The panel takes the page; the call becomes a rail." },
  ];
  return (
    <div role="radiogroup" aria-label="Layout" className="grid gap-2 sm:grid-cols-2">
      {options.map((option) => (
        <label
          key={option.value}
          className={cn(
            "flex cursor-pointer items-start gap-2.5 rounded-md border border-border px-3 py-2.5",
            "has-[:checked]:border-brand-line has-[:checked]:bg-brand-soft hover:bg-accent",
            "has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring",
          )}
        >
          <input
            type="radio"
            name={name}
            value={option.value}
            checked={value === option.value}
            onChange={() => onChange(option.value)}
            className="accent-primary mt-1"
          />
          <span className="flex flex-col">
            <span className="text-sm font-medium">{option.label}</span>
            <span className="text-[0.8125rem] text-muted-foreground">{option.hint}</span>
          </span>
        </label>
      ))}
    </div>
  );
}

function AddBlockPalette({ onAdd }: { onAdd: (type: BlockType) => void }) {
  const [open, setOpen] = React.useState(false);
  const regionId = React.useId();
  return (
    <div className="flex flex-col gap-3">
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="self-start"
        aria-expanded={open}
        aria-controls={regionId}
        onClick={() => setOpen((value) => !value)}
      >
        <Icon as={open ? ChevronDownIcon : PlusIcon} size="sm" />
        Add block
      </Button>
      {open ? (
        <ul id={regionId} aria-label="Block types" className="grid gap-2 sm:grid-cols-2" data-slot="block-palette">
          {BLOCK_TYPES.map((type) => {
            const entry = BLOCK_CATALOG[type];
            return (
              <li key={type}>
                <button
                  type="button"
                  onClick={() => {
                    onAdd(type);
                    setOpen(false);
                  }}
                  className="flex w-full items-start gap-3 rounded-md border border-border px-3 py-2.5 text-left transition-colors duration-(--dur-2) hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                >
                  <span aria-hidden="true" className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
                    <Icon as={BLOCK_ICONS[type]} size="sm" />
                  </span>
                  <span className="flex min-w-0 flex-col">
                    <span className="text-sm font-medium">{entry.label}</span>
                    <span className="text-[0.8125rem] leading-[1.125rem] text-muted-foreground">{entry.description}</span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}

let rowKeySeq = 0;
function newRowKey(): string {
  rowKeySeq += 1;
  return `row-${rowKeySeq}`;
}

/**
 * Row identity that survives reordering *and* editing a block's id: the list
 * keeps its own keys in step with the moves, removals and additions it makes.
 * Any other change of length (switching panels, a form reset) re-keys.
 */
function useRowKeys(count: number) {
  const [keys, setKeys] = React.useState<string[]>(() => Array.from({ length: count }, newRowKey));
  const current = keys.length === count ? keys : Array.from({ length: count }, newRowKey);
  if (current !== keys) setKeys(current);
  return {
    keys: current,
    move(from: number, to: number) {
      const next = [...current];
      const [moved] = next.splice(from, 1);
      next.splice(Math.max(0, Math.min(next.length, to)), 0, moved);
      setKeys(next);
    },
    remove(index: number) {
      setKeys(current.filter((_, i) => i !== index));
    },
    add() {
      const key = newRowKey();
      setKeys([...current, key]);
      return key;
    },
  };
}

function BlockList({
  panel,
  onChange,
  idErrors,
}: {
  panel: PanelLayoutForm;
  onChange: (next: PanelLayoutForm) => void;
  idErrors: Record<number, string>;
}) {
  const rows = useRowKeys(panel.blocks.length);
  const [expanded, setExpanded] = React.useState<string | null>(null);
  const [dragFrom, setDragFrom] = React.useState<number | null>(null);
  const [dropAt, setDropAt] = React.useState<number | null>(null);
  const [announcement, setAnnouncement] = React.useState("");
  const handles = React.useRef<Map<string, HTMLButtonElement>>(new Map());

  function move(from: number, to: number, refocus: boolean) {
    const next = moveBlock(panel, from, to);
    if (next === panel) return;
    const target = Math.max(0, Math.min(next.blocks.length - 1, to));
    const moved = next.blocks[target];
    const key = rows.keys[from];
    rows.move(from, target);
    onChange(next);
    setAnnouncement(`${blockTitle(moved) ?? BLOCK_CATALOG[moved.type].label} moved to position ${target + 1} of ${next.blocks.length}.`);
    if (refocus) requestAnimationFrame(() => handles.current.get(key)?.focus());
  }

  function add(type: BlockType) {
    const key = rows.add();
    onChange(addBlock(panel, type));
    setExpanded(key);
    setAnnouncement(`${BLOCK_CATALOG[type].label} block added at the end.`);
  }

  return (
    <>
      <p className="sr-only" aria-live="polite">
        {announcement}
      </p>
      {panel.blocks.length === 0 ? (
        <p className="text-sm text-muted-foreground">No blocks yet. Add one below; the panel shows them top to bottom.</p>
      ) : (
        <ol aria-label="Blocks" data-slot="block-list" className="flex flex-col gap-2">
          {panel.blocks.map((block, index) => {
            const key = rows.keys[index];
            const entry = BLOCK_CATALOG[block.type];
            const heading = blockTitle(block) ?? entry.label;
            // A row with an id error stays open so "Show field" can reach the input.
            const isOpen = expanded === key || Boolean(idErrors[index]);
            const detailsId = `block-details-${key}`;
            return (
              <li
                key={key}
                data-block-id={block.id}
                data-drop-target={dropAt === index && dragFrom !== null && dragFrom !== index ? "true" : undefined}
                onDragOver={(event) => {
                  if (dragFrom === null) return;
                  event.preventDefault();
                  setDropAt(index);
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  if (dragFrom !== null) move(dragFrom, index, false);
                  setDragFrom(null);
                  setDropAt(null);
                }}
                className={cn(
                  "rounded-md border border-border bg-card transition-colors",
                  dragFrom === index && "opacity-50",
                  "data-[drop-target=true]:border-brand-line",
                  idErrors[index] && "border-danger",
                )}
              >
                <div className="flex items-center gap-2 px-2 py-2">
                  <button
                    type="button"
                    draggable
                    ref={(node) => {
                      if (node) handles.current.set(key, node);
                      else handles.current.delete(key);
                    }}
                    aria-label={`Reorder ${heading}, position ${index + 1} of ${panel.blocks.length}. Use the up and down arrow keys.`}
                    onDragStart={(event) => {
                      event.dataTransfer.effectAllowed = "move";
                      event.dataTransfer.setData("text/plain", block.id);
                      setDragFrom(index);
                    }}
                    onDragEnd={() => {
                      setDragFrom(null);
                      setDropAt(null);
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "ArrowUp" || event.key === "ArrowDown") {
                        event.preventDefault();
                        move(index, index + (event.key === "ArrowUp" ? -1 : 1), true);
                      }
                    }}
                    className="flex size-7 shrink-0 cursor-grab items-center justify-center rounded-sm text-muted-foreground hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none active:cursor-grabbing"
                  >
                    <Icon as={GripVerticalIcon} size="sm" />
                  </button>
                  <span aria-hidden="true" className="flex size-7 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
                    <Icon as={BLOCK_ICONS[block.type]} size="sm" />
                  </span>
                  <button
                    type="button"
                    aria-expanded={isOpen}
                    aria-controls={detailsId}
                    onClick={() => setExpanded(isOpen ? null : key)}
                    className="flex min-w-0 flex-1 flex-col items-start rounded-sm px-1 text-left focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                  >
                    <span className="truncate text-sm font-medium">{heading}</span>
                    <span className="truncate text-xs text-muted-foreground">
                      {entry.label} · <span className="font-mono">{block.id}</span>
                    </span>
                  </button>
                  <Icon
                    as={ChevronDownIcon}
                    size="sm"
                    className={cn("text-muted-foreground transition-transform duration-(--dur-2)", isOpen && "rotate-180")}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`Remove ${heading}`}
                    onClick={() => {
                      rows.remove(index);
                      onChange(removeBlock(panel, index));
                      setAnnouncement(`${heading} removed.`);
                    }}
                  >
                    <Icon as={Trash2Icon} size="sm" />
                  </Button>
                </div>
                {idErrors[index] && !isOpen ? (
                  <p className="px-3 pb-2 text-[0.8125rem] text-danger-text">{idErrors[index]}</p>
                ) : null}
                {isOpen ? (
                  <div id={detailsId} className="border-t border-border px-3 py-3">
                    <BlockConfigForm panel={panel} index={index} onChange={onChange} idError={idErrors[index]} />
                  </div>
                ) : null}
              </li>
            );
          })}
        </ol>
      )}
      <AddBlockPalette onAdd={add} />
    </>
  );
}

function BlockToolsSection({ panel }: { panel: PanelLayoutForm }) {
  const { setValue } = useFormContext<AgentEditorForm>();
  const disabled = useWatch<AgentEditorForm, "config.tools.builtin_disabled">({ name: "config.tools.builtin_disabled" }) ?? [];
  const statuses = blockToolStatus(panel, disabled);
  const byName = new Map(BLOCK_TOOLS.map((tool) => [tool.name, tool]));

  function toggle(name: string, enabled: boolean) {
    setValue(
      "config.tools.builtin_disabled",
      enabled ? disabled.filter((existing) => existing !== name) : Array.from(new Set([...disabled, name])),
      { shouldDirty: true },
    );
  }

  return (
    <Section
      id="panel-block-tools"
      title="Block tools"
      description="What the agent may do with these blocks. A tool only exists when the panel has a block it can fill."
    >
      {statuses.map((status) => {
        const tool = byName.get(status.name);
        if (!tool) return null;
        const id = `block-tool-${status.name}`;
        return (
          <SectionRow key={status.name}>
            <Field inline label={tool.label} htmlFor={id} hint={status.reason ?? tool.help}>
              <Switch
                id={id}
                checked={status.available && status.enabled}
                disabled={!status.available}
                onCheckedChange={(checked) => toggle(status.name, checked)}
                data-issue-path={`tools.builtin_disabled`}
              />
            </Field>
          </SectionRow>
        );
      })}
    </Section>
  );
}

function CustomPanelSummary({ agent, panelId }: { agent: AgentOut; panelId: string }) {
  const packsQuery = usePacks();
  const manifest = packsQuery.data?.items.find((item) => item.manifest.id === agent.pack_id)?.manifest;
  const exposed = manifest && manifest.ui_panel_id === panelId ? (manifest.blocks ?? []) : [];
  return (
    <p className="text-[0.8125rem] text-muted-foreground" data-slot="custom-panel-summary">
      Custom panel: <span className="font-mono text-foreground">{panelId}</span> —{" "}
      {exposed.length > 0
        ? `blocks the panel exposes: ${exposed.map((block) => (block as { title?: string | null }).title || block.id).join(", ")}.`
        : "it draws its own layout, so there are no blocks to arrange here."}
    </p>
  );
}

export function PanelComposer({ agent }: { agent: AgentOut }) {
  const { setValue, formState } = useFormContext<AgentEditorForm>();
  const panel = useWatch<AgentEditorForm, "config.panel">({ name: "config.panel" });

  const update = React.useCallback(
    (next: PanelLayoutForm) => {
      setValue("config.panel", next, { shouldDirty: true, shouldValidate: formState.isSubmitted });
      setValue("ui_panel_id", next.panel_id, { shouldDirty: true });
    },
    [setValue, formState.isSubmitted],
  );

  if (!panel) return null;

  const blockErrors = (formState.errors.config?.panel?.blocks ?? []) as unknown as Array<{ id?: { message?: string } } | undefined>;
  const idErrors: Record<number, string> = {};
  panel.blocks.forEach((block, index) => {
    const message = blockErrors?.[index]?.id?.message;
    if (message) idErrors[index] = message;
    else if (panel.blocks.findIndex((other) => other.id === block.id) !== index) idErrors[index] = "Two blocks can't share an id.";
  });

  const isComposite = panel.panel_id === COMPOSITE_PANEL_ID;
  const isLegacy = panel.panel_id === LEGACY_GENERIC_PANEL_ID;

  return (
    <div className="flex flex-col gap-6" data-testid="panel-composer">
      <Section
        id="panel-choice"
        title="Panel"
        description="What callers see next to the call, and what the person reviewing the call sees afterwards."
      >
        <SectionRow className="flex flex-col gap-4">
          <PanelChoice value={panel.panel_id} onChange={(id) => update(switchPanel(panel, id))} />
          {isLegacy ? (
            <Alert variant="info">
              <AlertDescription>
                This agent still uses the classic session panel. Switch to the block panel to arrange it; it starts with
                the same status, notes, checklist and activity.
              </AlertDescription>
            </Alert>
          ) : null}
          {!isComposite && !isLegacy ? <CustomPanelSummary agent={agent} panelId={panel.panel_id} /> : null}
        </SectionRow>
      </Section>

      {isComposite ? (
        <>
          <Section id="panel-blocks" title="Blocks" description="Shown top to bottom. Drag a block by its handle to reorder it.">
            <SectionRow className="flex flex-col gap-4">
              <div className="flex flex-col gap-1.5">
                <p className="text-sm font-medium leading-5">Layout</p>
                <LayoutChoice value={panel.layout} onChange={(layout) => update({ ...panel, layout })} />
              </div>
            </SectionRow>
            <SectionRow className="flex flex-col gap-4" data-issue-path="panel.blocks">
              <BlockList panel={panel} onChange={update} idErrors={idErrors} />
            </SectionRow>
          </Section>

          <Section
            id="panel-preview"
            title="Preview"
            description="Sample data, laid out exactly as the session page draws your blocks."
          >
            <SectionRow>
              <React.Suspense
                fallback={<div data-testid="composer-preview-loading" className="h-64 animate-pulse rounded-lg bg-muted" />}
              >
                <ComposerPreview panel={panel} />
              </React.Suspense>
            </SectionRow>
          </Section>

          <BlockToolsSection panel={panel} />
        </>
      ) : null}

      <SessionCapabilities />
    </div>
  );
}
