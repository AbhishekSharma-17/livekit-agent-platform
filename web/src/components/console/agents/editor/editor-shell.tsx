"use client";

import * as React from "react";
import Link from "next/link";
import { useFormContext, useWatch } from "react-hook-form";
import {
  ArrowLeftIcon,
  CheckIcon,
  PanelRightOpenIcon,
  PencilIcon,
  RefreshCwIcon,
  Trash2Icon,
  XIcon,
} from "lucide-react";

import { CopyButton } from "@/components/shared/copy-button";
import { Icon } from "@/components/shared/icon";
import { RowMenu } from "@/components/shared/row-menu";
import { LifecycleBadge } from "@/components/shared/status-chip";
import { Alert } from "@/components/ui/alert";
import { Button, IconButton } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { IfCan, ReadOnlyNote, readOnlyCopy } from "@/components/console/shared/permission";
import type { AgentOut, ValidationResult } from "@/contracts/lkap-contracts";
import { cn } from "@/lib/utils";

import { ConnectionChip, ModeChip } from "./header-chips";
import { SectionIssueList } from "./issue-list";
import { PublishControl, type SaveOutcome } from "./publish-popover";
import type { ResolvedEditorSlots } from "./registry";
import { SectionNav } from "./section-nav";
import { SummaryRail } from "./summary-rail";
import { TestCallMenu } from "./test-call-menu";
import type { EditorSectionDef } from "./types";
import type { SectionIssueSummary } from "./validation-map";

/**
 * Layout contract with the console shell (WP-1, `shell/console-shell.tsx`):
 * `main` pads 16 / 24 / 32 px (base / md / lg) and the top bar is sticky. The
 * spec's top bar is 56 px at every width (docs/ui/DESIGN-SYSTEM.md section 7.1);
 * the shell may override it with `--console-topbar-height`, and the gutter
 * bleed mirrors `main`'s padding.
 */
/** Full-bleed to the page gutters (`Page`: 16 px below 640, 20 px to 900, then 32 px). */
const BLEED = "-mx-4 px-4 sm:-mx-5 sm:px-5 min-[901px]:-mx-8 min-[901px]:px-8";
/**
 * < 1024 px only the actions row and the section bar stay pinned: the header
 * sticks at a negative offset (`--editor-header-collapse` = the height of the
 * title block above the actions) so the title and chips scroll away.
 */
const UNDER_TOPBAR =
  "top-[calc(var(--console-topbar-height,3.5rem)-var(--editor-header-collapse,0px))] lg:top-[var(--console-topbar-height,3.5rem)]";
/** Sticky offset for the nav and rail columns under the editor header (measured into `--editor-header-height`). */
const UNDER_HEADER = "lg:top-[calc(var(--console-topbar-height,3.5rem)+var(--editor-header-height,0px)+1.5rem)]";

export interface EditorShellProps {
  agent: AgentOut;
  sections: EditorSectionDef[];
  active: EditorSectionDef;
  onSelectSection: (id: string) => void;
  summary: Record<string, SectionIssueSummary>;
  slots: ResolvedEditorSlots;
  dirty: boolean;
  saving: boolean;
  /** Shows the quiet "Configuration looks good" line under the header. */
  looksGood: boolean;
  saveNow: () => Promise<SaveOutcome | null>;
  onValidated: (result: ValidationResult) => void;
  goToFirstIssue: () => void;
  /** Deletes the agent; throws on failure so the confirmation can say why. */
  onDelete: () => Promise<void>;
  /** Re-reads the agent from the server (the header's Refresh). */
  onRefresh: () => void;
  refreshing: boolean;
  contentRef: React.Ref<HTMLDivElement>;
  children: React.ReactNode;
}

/**
 * The agent editor frame (docs/UI_UX_SPEC.md §4.3; the detail archetype of
 * docs/ui/DESIGN-SYSTEM.md section 7.4): a sticky header with the back link,
 * the name plus its status pill and meta, then the actions: Refresh, the
 * danger-outline Delete, Test call, Publish and **Save, the one primary,
 * last**. People who can't write see a read-only note instead of Save,
 * Publish and Delete (decision D12). Below: section nav (200 px) · content
 * (≤ 720 px) · summary rail (280 px) at ≥ 1024 px; below that the nav becomes
 * a scrollable bar pinned under the header and the rail a "Summary" dialog.
 */
export function EditorShell({
  agent,
  sections,
  active,
  onSelectSection,
  summary,
  slots,
  dirty,
  saving,
  looksGood,
  saveNow,
  onValidated,
  goToFirstIssue,
  onDelete,
  onRefresh,
  refreshing,
  contentRef,
  children,
}: EditorShellProps) {
  const headerRef = React.useRef<HTMLDivElement>(null);
  const actionsRef = React.useRef<HTMLDivElement>(null);
  const [headerHeight, setHeaderHeight] = React.useState(0);
  const [collapse, setCollapse] = React.useState(0);
  const [summaryOpen, setSummaryOpen] = React.useState(false);
  const [confirmDelete, setConfirmDelete] = React.useState(false);

  React.useLayoutEffect(() => {
    const node = headerRef.current;
    if (!node) return;
    const measure = () => {
      setHeaderHeight(node.offsetHeight);
      // Offset of the actions row inside the header, minus a little breathing room.
      setCollapse(Math.max(0, (actionsRef.current?.offsetTop ?? 0) - 12));
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const ConnectionChipSlot = slots.connectionChip ?? ConnectionChip;
  const ModeChipSlot = slots.modeChip;
  const full = active.layout === "full";

  return (
    <div
      data-slot="agent-editor"
      style={{ "--editor-header-height": `${headerHeight}px` } as React.CSSProperties}
    >
      <div
        ref={headerRef}
        data-slot="agent-editor-header"
        className={cn("sticky z-20 border-b border-border bg-background", UNDER_TOPBAR, BLEED)}
        style={{ "--editor-header-collapse": `${collapse}px` } as React.CSSProperties}
      >
        <div className="flex flex-col gap-3 py-3 lg:flex-row lg:items-end lg:justify-between lg:gap-6">
          <div className="flex min-w-0 flex-col gap-1.5">
            <Link
              href="/console/agents"
              data-slot="page-back-link"
              className="-ml-2 inline-flex h-7 items-center gap-1.5 self-start rounded-sm px-2 text-label text-text-secondary transition-colors duration-(--duration-fast) hover:bg-muted hover:text-foreground"
            >
              <ArrowLeftIcon aria-hidden="true" className="size-[15px]" />
              Back to agents
            </Link>
            <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1">
              <AgentTitle />
              <LifecycleBadge state={agent.published ? "live" : "draft"} />
            </div>
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <span className="inline-flex min-w-0 items-center gap-0.5">
                <span className="truncate font-mono text-label text-text-secondary">/{agent.slug}</span>
                <CopyButton value={agent.slug} label="Copy slug" size="xs" />
              </span>
              <ConnectionChipSlot agent={agent} />
              {ModeChipSlot ? <ModeChipSlot agent={agent} /> : <ModeChip />}
              {dirty ? (
                <span className="inline-flex items-center gap-1.5 text-caption font-medium text-warning-text" role="status">
                  <span aria-hidden="true" className="size-1.5 rounded-pill bg-warning-solid" />
                  Unsaved changes
                </span>
              ) : null}
            </div>
          </div>
          <div ref={actionsRef} data-slot="page-actions" className="flex flex-wrap items-center gap-2 lg:shrink-0 lg:flex-nowrap">
            <IconButton
              type="button"
              label={refreshing ? "Refreshing agent" : "Refresh agent"}
              disabled={refreshing}
              onClick={onRefresh}
            >
              <RefreshCwIcon className={cn(refreshing && "animate-spin")} />
            </IconButton>
            {slots.headerActions.map((Action, index) => (
              <Action key={index} agent={agent} />
            ))}
            <TestCallMenu agent={agent} dirty={dirty} saveNow={saveNow} extraItems={slots.testCallItems} />
            <IfCan>
              <PublishControl
                agent={agent}
                dirty={dirty}
                saveNow={saveNow}
                onValidated={onValidated}
                goToFirstIssue={goToFirstIssue}
              />
            </IfCan>
            <IfCan>
              <RowMenu
                label="More agent actions"
                destructive={{ label: "Delete agent", icon: Trash2Icon, onSelect: () => setConfirmDelete(true) }}
              />
            </IfCan>
            <IfCan
              loading={
                <Button type="button" variant="primary" disabled>
                  Save
                </Button>
              }
              fallback={<ReadOnlyNote>{readOnlyCopy("builder")}</ReadOnlyNote>}
            >
              <Button type="submit" variant="primary" disabled={!dirty} busy={saving} busyLabel="Saving…">
                Save
              </Button>
            </IfCan>
          </div>
        </div>
        {looksGood ? (
          <Alert tone="success" className="mb-3">
            Configuration looks good
          </Alert>
        ) : null}
        <div className="flex items-center gap-2 pb-3 lg:hidden">
          <div className="-ml-4 min-w-0 flex-1 overflow-x-auto pl-4 md:-ml-6 md:pl-6">
            <SectionNav
              variant="bar"
              sections={sections}
              active={active.id}
              onSelect={onSelectSection}
              summary={summary}
            />
          </div>
          <Button type="button" variant="secondary" className="shrink-0" onClick={() => setSummaryOpen(true)}>
            <Icon as={PanelRightOpenIcon} size="md" />
            Summary
          </Button>
        </div>
      </div>

      <div
        className={cn(
          "grid gap-6 pt-6",
          full ? "lg:grid-cols-[200px_minmax(0,1fr)]" : "lg:grid-cols-[200px_minmax(0,1fr)_280px]",
        )}
      >
        <div className="hidden lg:block">
          <SectionNav
            variant="list"
            sections={sections}
            active={active.id}
            onSelect={onSelectSection}
            summary={summary}
            className={cn("lg:sticky", UNDER_HEADER)}
          />
        </div>
        <div className={cn("flex min-w-0 flex-col gap-4", !full && "max-w-[720px]")}>
          {active.ownsIssueList ? null : <SectionIssueList sectionId={active.id} />}
          <div
            ref={contentRef}
            id="agent-editor-section"
            role="region"
            // "… section": a section component inside often reuses the bare
            // label for its own `<section>` (axe `landmark-unique`).
            aria-label={`${active.label} section`}
            tabIndex={-1}
            data-section={active.id}
            className="min-w-0 outline-none"
          >
            {children}
          </div>
        </div>
        {full ? null : (
          <div className="hidden lg:block">
            <SummaryRail
              agent={agent}
              slots={slots}
              className={cn(
                "lg:sticky lg:max-h-[calc(100dvh-var(--console-topbar-height,3.5rem)-var(--editor-header-height,0px)-3rem)] lg:overflow-y-auto",
                UNDER_HEADER,
              )}
            />
          </div>
        )}
      </div>

      <Dialog open={summaryOpen} onOpenChange={setSummaryOpen}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>Summary</DialogTitle>
            <DialogDescription>What {agent.name} does, at a glance.</DialogDescription>
          </DialogHeader>
          <DialogBody className="p-4">
            <SummaryRail agent={agent} slots={slots} onNavigate={() => setSummaryOpen(false)} />
          </DialogBody>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={`Delete “${agent.name}”?`}
        description="This permanently deletes the agent and its private tools. Agents that have sessions can't be deleted — sessions are kept for the audit trail. Unpublish it instead."
        confirmLabel="Delete agent"
        onConfirm={onDelete}
      />
    </div>
  );
}

/**
 * The agent name as a real `h1`; the pencil turns it into an input with
 * Save / Cancel (Enter / Esc). The new name joins the form and is saved with it.
 */
export function AgentTitle() {
  const { setValue, formState } = useFormContext<AgentEditorForm>();
  const name = useWatch<AgentEditorForm, "name">({ name: "name" });
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState(name);
  const [error, setError] = React.useState<string | null>(null);
  const inputRef = React.useRef<HTMLInputElement>(null);
  const pencilRef = React.useRef<HTMLButtonElement>(null);
  const inputId = React.useId();
  const formError = formState.errors.name?.message;

  React.useEffect(() => {
    if (editing) inputRef.current?.select();
  }, [editing]);

  function start() {
    setDraft(name);
    setError(null);
    setEditing(true);
  }

  function finish(commit: boolean) {
    if (commit) {
      const next = draft.trim();
      if (!next) {
        setError("Name is required");
        inputRef.current?.focus();
        return;
      }
      if (next !== name) setValue("name", next, { shouldDirty: true, shouldValidate: true });
    }
    setEditing(false);
    setError(null);
    requestAnimationFrame(() => pencilRef.current?.focus());
  }

  if (editing) {
    const shown = error ?? formError;
    return (
      <div className="flex min-w-0 flex-col gap-1">
        <div className="flex min-w-0 items-center gap-1.5">
          <label htmlFor={inputId} className="sr-only">
            Agent name
          </label>
          <Input
            id={inputId}
            ref={inputRef}
            value={draft}
            onChange={(event) => {
              setDraft(event.target.value);
              if (error) setError(null);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                finish(true);
              } else if (event.key === "Escape") {
                event.preventDefault();
                finish(false);
              }
            }}
            aria-invalid={shown ? true : undefined}
            aria-describedby={shown ? `${inputId}-error` : undefined}
            className="max-w-md text-dialog font-semibold"
            data-issue-path="name"
            name="name"
          />
          <Button type="button" size="sm" variant="secondary" onClick={() => finish(true)}>
            <Icon as={CheckIcon} size="sm" />
            Save
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => finish(false)}>
            <Icon as={XIcon} size="sm" />
            Cancel
          </Button>
        </div>
        {shown ? (
          <p id={`${inputId}-error`} className="text-label text-destructive-text">
            {shown}
          </p>
        ) : null}
      </div>
    );
  }

  return (
    <div className="flex min-w-0 items-center gap-1">
      <h1 className="truncate text-page font-semibold tracking-[-0.018em] text-foreground">{name}</h1>
      <IfCan>
        <Button
          ref={pencilRef}
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label="Edit name"
          onClick={start}
          className="shrink-0"
        >
          <Icon as={PencilIcon} size="sm" />
        </Button>
      </IfCan>
      {formError ? <span className="text-label text-destructive-text">{formError}</span> : null}
    </div>
  );
}
