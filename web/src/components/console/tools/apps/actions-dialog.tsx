"use client";

import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { EmptyState } from "@/components/shared/empty-state";
import { StatusChip, type StatusTone } from "@/components/shared/status-chip";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import { useAgents, useMaterialiseAppActions, useToolProviderActions } from "@/components/console/lib/api-hooks";
import { appsErrorMessage } from "@/components/console/tools/apps/use-composio";
import type { ActionRisk } from "@/components/console/tools/apps/types";

const RISK_LABEL: Record<ActionRisk, string> = { read: "Read", write: "Writes", destructive: "Destructive" };
const RISK_TONE: Record<ActionRisk, StatusTone> = { read: "success", write: "info", destructive: "danger" };

export interface ActionsDialogProps {
  connectionId: string;
  toolkitSlug: string;
  toolkitName: string;
  /** Already-picked action slugs (`AppConnectionOut.picked_actions`) — shown checked and locked (D-V5-C7: picks only ever add). */
  pickedActions: string[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * The Actions picker (docs/v5/COMPOSIO.md §6): search, a Featured filter,
 * risk badges, checkboxes and "Add as tools" (+ "…and attach to agent").
 * Until V5-47 lands, a pick is stored on the connection (`AppActionsPickOut`)
 * rather than becoming a tool right away — the copy here says "Add as
 * tools", never promising a tool exists yet.
 */
export function ActionsDialog({ connectionId, toolkitSlug, toolkitName, pickedActions, open, onOpenChange }: ActionsDialogProps) {
  const [search, setSearch] = React.useState("");
  const [debouncedSearch, setDebouncedSearch] = React.useState("");
  const [featuredOnly, setFeaturedOnly] = React.useState(false);
  const [checked, setChecked] = React.useState<Set<string>>(new Set());
  const [confirmDestructive, setConfirmDestructive] = React.useState(false);
  const [attachAgentId, setAttachAgentId] = React.useState("");

  React.useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search.trim()), 300);
    return () => clearTimeout(timer);
  }, [search]);

  React.useEffect(() => {
    if (!open) {
      setChecked(new Set());
      setConfirmDestructive(false);
      setAttachAgentId("");
      setSearch("");
      setFeaturedOnly(false);
    }
  }, [open]);

  const actionsQuery = useToolProviderActions(open ? toolkitSlug : null, {
    query: debouncedSearch || undefined,
    important: featuredOnly || undefined,
    limit: 30,
  });
  const agentsQuery = useAgents();
  const materialise = useMaterialiseAppActions();

  const items = React.useMemo(() => actionsQuery.data?.pages.flatMap((page) => page.items) ?? [], [actionsQuery.data]);
  const pickedSet = React.useMemo(() => new Set(pickedActions.map((slug) => slug.toUpperCase())), [pickedActions]);

  function toggle(slug: string) {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });
  }

  const selected = items.filter((action) => checked.has(action.slug));
  const hasDestructive = selected.some((action) => action.risk === "destructive");
  const canSubmit = checked.size > 0 && (!hasDestructive || confirmDestructive);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    event.stopPropagation();
    if (!canSubmit) return;
    // `canSubmit` already requires `checked.size > 0`, so this is never empty;
    // `AppActionsPickIn.actions` is typed as a non-empty tuple (`min_length=1`).
    const picked = Array.from(checked) as [string, ...string[]];
    try {
      await materialise.mutateAsync({
        connection_id: connectionId,
        actions: picked,
        agent_id: attachAgentId || null,
        allow_destructive: hasDestructive,
      });
      toast.success(
        attachAgentId
          ? `Added ${checked.size} action(s) from ${toolkitName} and attached to the agent`
          : `Added ${checked.size} action(s) from ${toolkitName}`,
      );
      onOpenChange(false);
    } catch (error) {
      toast.error(`Couldn't add actions — ${appsErrorMessage(error)}`);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <form onSubmit={(event) => void submit(event)} className="flex min-h-0 flex-1 flex-col" noValidate>
          <DialogHeader>
            <DialogTitle>{toolkitName} actions</DialogTitle>
            <DialogDescription>Pick what agents may do with {toolkitName}. Destructive actions need a confirm.</DialogDescription>
          </DialogHeader>

          <DialogBody className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <Input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search actions"
                aria-label="Search actions"
                className="w-full sm:w-56"
              />
              <div className="flex items-center gap-2">
                <Checkbox id="actions-featured" checked={featuredOnly} onCheckedChange={(v) => setFeaturedOnly(v === true)} />
                <Label htmlFor="actions-featured" className="text-sm font-normal">
                  Featured only
                </Label>
              </div>
            </div>

            {actionsQuery.isLoading ? (
              <p className="text-[0.8125rem] text-muted-foreground">Loading actions…</p>
            ) : actionsQuery.isError ? (
              <ErrorBanner message={`Couldn't load actions — ${appsErrorMessage(actionsQuery.error)}`} onRetry={() => actionsQuery.refetch()} />
            ) : items.length === 0 ? (
              <EmptyState title="No actions match" description="Try a different search." compact />
            ) : (
              <ul className="flex max-h-80 flex-col gap-1.5 overflow-y-auto">
                {items.map((action) => {
                  const alreadyPicked = pickedSet.has(action.slug.toUpperCase());
                  const inputId = `action-${action.slug}`;
                  const risk: ActionRisk = action.risk ?? "write";
                  return (
                    <li key={action.slug} className="flex items-start gap-2 rounded-md border border-border p-2.5">
                      <Checkbox
                        id={inputId}
                        checked={alreadyPicked || checked.has(action.slug)}
                        disabled={alreadyPicked}
                        onCheckedChange={() => toggle(action.slug)}
                        className="mt-0.5"
                      />
                      <Label htmlFor={inputId} className="flex min-w-0 flex-1 cursor-pointer flex-col gap-0.5 font-normal">
                        <span className="flex flex-wrap items-center gap-1.5">
                          <span className="text-sm font-medium text-foreground">{action.name}</span>
                          <StatusChip tone={RISK_TONE[risk]} size="sm">
                            {RISK_LABEL[risk]}
                          </StatusChip>
                          {action.important ? (
                            <StatusChip tone="neutral" size="sm">
                              Featured
                            </StatusChip>
                          ) : null}
                          {alreadyPicked ? (
                            <StatusChip tone="success" size="sm">
                              Added
                            </StatusChip>
                          ) : null}
                        </span>
                        {action.description ? <span className="text-xs text-pretty text-muted-foreground">{action.description}</span> : null}
                      </Label>
                    </li>
                  );
                })}
              </ul>
            )}

            {actionsQuery.hasNextPage ? (
              <div>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={actionsQuery.isFetchingNextPage}
                  onClick={() => void actionsQuery.fetchNextPage()}
                >
                  {actionsQuery.isFetchingNextPage ? "Loading…" : "Load more"}
                </Button>
              </div>
            ) : null}

            {hasDestructive ? (
              <div className="flex items-start gap-2 rounded-md bg-danger-soft p-2.5">
                <Checkbox id="actions-destructive-confirm" checked={confirmDestructive} onCheckedChange={(v) => setConfirmDestructive(v === true)} className="mt-0.5" />
                <Label htmlFor="actions-destructive-confirm" className="text-[0.8125rem] font-normal text-danger-text">
                  I understand — one or more picked actions delete, remove or move money.
                </Label>
              </div>
            ) : null}

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="actions-attach-agent" className="text-sm font-medium text-foreground">
                Also attach to an agent (optional)
              </Label>
              <Select value={attachAgentId || "none"} onValueChange={(value) => setAttachAgentId(value === "none" ? "" : value)}>
                <SelectTrigger id="actions-attach-agent" className="w-full sm:w-72">
                  <SelectValue placeholder={agentsQuery.isLoading ? "Loading agents…" : "Don't attach yet"} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">Don&apos;t attach yet</SelectItem>
                  {(agentsQuery.data?.items ?? []).map((agent) => (
                    <SelectItem key={agent.id} value={agent.id}>
                      {agent.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </DialogBody>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!canSubmit || materialise.isPending}>
              {materialise.isPending ? "Adding…" : attachAgentId ? "Add as tools and attach to agent" : "Add as tools"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
