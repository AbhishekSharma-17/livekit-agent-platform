"use client";

import * as React from "react";
import Link from "next/link";
import { useFormContext, useWatch } from "react-hook-form";
import {
  CheckIcon,
  ChevronLeftIcon,
  CircleCheckIcon,
  MoreHorizontalIcon,
  PanelRightOpenIcon,
  PencilIcon,
  Trash2Icon,
  XIcon,
} from "lucide-react";

import { CopyButton } from "@/components/shared/copy-button";
import { Icon } from "@/components/shared/icon";
import { StatusChip } from "@/components/shared/status-chip";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";
import { GatedButton } from "@/components/shared/gated-button";
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
 * `main` pads 16 / 24 / 32 px (base / md / lg) and the top bar is sticky at
 * 56 px (< 1024) / 48 px (≥ 1024). The shell may override the top bar height
 * with `--console-topbar-height`; the gutter bleed mirrors `main`'s padding.
 */
const BLEED = "-mx-4 px-4 md:-mx-6 md:px-6 lg:-mx-8 lg:px-8";
/**
 * < 1024 px only the actions row and the section bar stay pinned: the header
 * sticks at a negative offset (`--editor-header-collapse` = the height of the
 * title block above the actions) so the title and chips scroll away.
 */
const UNDER_TOPBAR =
  "top-[calc(var(--console-topbar-height,3.5rem)-var(--editor-header-collapse,0px))] lg:top-[var(--console-topbar-height,3rem)]";
/** Sticky offset for the nav and rail columns under the editor header (measured into `--editor-header-height`). */
const UNDER_HEADER = "lg:top-[calc(var(--console-topbar-height,3rem)+var(--editor-header-height,0px)+1.5rem)]";

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
  onDelete: () => Promise<void>;
  contentRef: React.Ref<HTMLDivElement>;
  children: React.ReactNode;
}

/**
 * The agent editor frame (docs/UI_UX_SPEC.md §4.3): sticky header, then
 * section nav (200 px) · content (≤ 720 px) · summary rail (280 px) at
 * ≥ 1024 px; below that the nav becomes a scrollable segmented control
 * pinned under the header and the rail a "Summary" sheet.
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
  contentRef,
  children,
}: EditorShellProps) {
  const headerRef = React.useRef<HTMLDivElement>(null);
  const actionsRef = React.useRef<HTMLDivElement>(null);
  const [headerHeight, setHeaderHeight] = React.useState(0);
  const [collapse, setCollapse] = React.useState(0);
  const [summaryOpen, setSummaryOpen] = React.useState(false);
  const [confirmDelete, setConfirmDelete] = React.useState(false);
  const [deleting, setDeleting] = React.useState(false);
  const { canWrite } = useWriteAccess();

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
              className="inline-flex items-center gap-1 self-start rounded-xs text-xs font-medium text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            >
              <Icon as={ChevronLeftIcon} size="sm" />
              Agents
            </Link>
            <AgentTitle />
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <span className="inline-flex min-w-0 items-center gap-0.5">
                <span className="truncate font-mono text-[0.8125rem] text-muted-foreground">/{agent.slug}</span>
                <CopyButton value={agent.slug} label="Copy slug" size="xs" />
              </span>
              <StatusChip tone={agent.published ? "live" : "neutral"} size="sm">
                {agent.published ? "Live" : "Draft"}
              </StatusChip>
              <ConnectionChipSlot agent={agent} />
              {ModeChipSlot ? <ModeChipSlot agent={agent} /> : <ModeChip />}
              {dirty ? (
                <span className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground" role="status">
                  <span aria-hidden="true" className="size-1.5 rounded-full bg-warning" />
                  Unsaved changes
                </span>
              ) : null}
            </div>
          </div>
          <div ref={actionsRef} className="flex flex-wrap items-center gap-2">
            {slots.headerActions.map((Action, index) => (
              <Action key={index} agent={agent} />
            ))}
            <TestCallMenu agent={agent} dirty={dirty} saveNow={saveNow} extraItems={slots.testCallItems} />
            <PublishControl
              agent={agent}
              dirty={dirty}
              saveNow={saveNow}
              onValidated={onValidated}
              goToFirstIssue={goToFirstIssue}
            />
            <GatedButton type="submit" allowed={canWrite} reason={writeAccessReason()} disabled={!dirty || saving}>
              {saving ? "Saving…" : "Save"}
            </GatedButton>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button type="button" variant="ghost" size="icon" aria-label="More agent actions">
                  <Icon as={MoreHorizontalIcon} size="md" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem
                  variant="destructive"
                  disabled={!canWrite}
                  onSelect={() => setConfirmDelete(true)}
                >
                  <Icon as={Trash2Icon} size="md" />
                  Delete agent
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
        {looksGood ? (
          <p
            role="status"
            className="mb-3 flex items-center gap-1.5 rounded-sm bg-success-soft px-3 py-1.5 text-[0.8125rem] text-success-text"
          >
            <Icon as={CircleCheckIcon} size="sm" />
            Configuration looks good
          </p>
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
          <Button type="button" variant="outline" className="shrink-0" onClick={() => setSummaryOpen(true)}>
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
                "lg:sticky lg:max-h-[calc(100vh-var(--console-topbar-height,3rem)-var(--editor-header-height,0px)-3rem)] lg:overflow-y-auto",
                UNDER_HEADER,
              )}
            />
          </div>
        )}
      </div>

      <Sheet open={summaryOpen} onOpenChange={setSummaryOpen}>
        <SheetContent side="right" className="w-full gap-0 overflow-y-auto p-0 sm:max-w-sm">
          <SheetHeader className="border-b border-border">
            <SheetTitle>Summary</SheetTitle>
            <SheetDescription>What {agent.name} does, at a glance.</SheetDescription>
          </SheetHeader>
          <div className="p-4">
            <SummaryRail agent={agent} slots={slots} onNavigate={() => setSummaryOpen(false)} />
          </div>
        </SheetContent>
      </Sheet>

      <Dialog open={confirmDelete} onOpenChange={(open) => !deleting && setConfirmDelete(open)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete &ldquo;{agent.name}&rdquo;?</DialogTitle>
            <DialogDescription>
              This permanently deletes the agent and its private tools. Agents that have sessions can&apos;t be
              deleted — sessions are kept for the audit trail. Unpublish it instead.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setConfirmDelete(false)} disabled={deleting}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={deleting}
              onClick={async () => {
                setDeleting(true);
                try {
                  await onDelete();
                  setConfirmDelete(false);
                } finally {
                  setDeleting(false);
                }
              }}
            >
              {deleting ? "Deleting…" : "Delete agent"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
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
            className="h-9 max-w-md text-[1.0625rem] font-semibold"
            data-issue-path="name"
            name="name"
          />
          <Button type="button" size="sm" onClick={() => finish(true)}>
            <Icon as={CheckIcon} size="sm" />
            Save
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => finish(false)}>
            <Icon as={XIcon} size="sm" />
            Cancel
          </Button>
        </div>
        {shown ? (
          <p id={`${inputId}-error`} className="text-[0.8125rem] text-danger-text">
            {shown}
          </p>
        ) : null}
      </div>
    );
  }

  return (
    <div className="flex min-w-0 items-center gap-1">
      <h1 className="truncate text-[1.375rem] leading-7 font-semibold tracking-[-0.015em]">{name}</h1>
      <Button
        ref={pencilRef}
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label="Edit name"
        onClick={start}
        className="shrink-0 text-muted-foreground"
      >
        <Icon as={PencilIcon} size="sm" />
      </Button>
      {formError ? <span className="text-[0.8125rem] text-danger-text">{formError}</span> : null}
    </div>
  );
}
