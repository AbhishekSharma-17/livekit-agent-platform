"use client";

import * as React from "react";
import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import { Controller, useFormContext } from "react-hook-form";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Section, SectionRow } from "@/components/shared/section";
import { EmptyState } from "@/components/console/shared/empty-state";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { StatusChip } from "@/components/shared/status-chip";
import { VendorMark } from "@/components/shared/vendor-mark";
import { useAppsStatus, useToolProviderActions, useToolProviderConnections, useTools } from "@/components/console/lib/api-hooks";
import { ActionsDialog } from "@/components/console/tools/apps/actions-dialog";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import type { AppActionOut, AppActionsPickOut, AppConnectionOut, ToolOut } from "@/contracts/lkap-contracts";

type Mode = "off" | "actions" | "server" | "router";

const MODE_OPTIONS: { value: Mode; label: string; helper: string; latency: boolean }[] = [
  { value: "off", label: "Off", helper: "This agent doesn't use any connected apps.", latency: false },
  {
    value: "actions",
    label: "Use picked actions (recommended)",
    helper: "The agent can only run the specific actions you add below.",
    latency: false,
  },
  {
    value: "server",
    label: "Let the agent use an app server",
    helper: "The agent can use every action of the apps you allow below.",
    latency: true,
  },
  {
    value: "router",
    label: "Let the agent find tools itself",
    helper: "The agent looks up and runs the right tool on its own, from the apps you allow below.",
    latency: true,
  },
];

/** Actions whose slug reads as delete/remove/send-money-shaped default to unchecked (D-V5-C7). */
function isDestructive(action: Pick<AppActionOut, "risk">): boolean {
  return action.risk === "destructive";
}

/** Narrows to `ProviderToolDefinition` the way V5-47's own code does (docs/v5/_asks.md #20: `kind` is optional on the generated union). */
function isProviderTool(tool: ToolOut): tool is ToolOut & { definition: { tool_slug: string; connection_id: string } } {
  return "tool_slug" in tool.definition;
}

/**
 * The **Connected apps** card (docs/v5/COMPOSIO.md §6, the V5-48 half): the
 * mode picker, the connected-app list (actions per app in "actions" mode, an
 * allow checkbox in the two dynamic modes), and — for the dynamic modes —
 * the flat "Actions the agent may take" list with destructive actions
 * unchecked by default. Renders nothing exotic when Apps isn't set up yet:
 * an empty state pointing at Tools → Apps, per D-V5-C13's "one source of
 * truth" (this card never re-implements Enable Composio).
 */
export function ConnectedAppsCard({ agentId }: { agentId: string }) {
  const { control, watch, setValue, getValues } = useFormContext<AgentEditorForm>();
  const queryClient = useQueryClient();

  const mode = (watch("config.tools.apps.mode") ?? "off") as Mode;
  const allowedToolkits = watch("config.tools.apps.allowed_toolkits") ?? [];
  const deniedActions = watch("config.tools.apps.denied_actions") ?? [];
  const toolIds = watch("config.tools.tool_ids") ?? [];

  const statusQuery = useAppsStatus();
  const connectionsQuery = useToolProviderConnections();
  // Materialised actions are *shared* tools (`agent_id: null`), attached only
  // through `tool_ids` (`attach_tools`, `api/src/lkap_api/tool_providers/materialise.py`)
  // — `useTools(agentId)` (agent-owned only) would never include them, so
  // this card reads the workspace-wide list instead, exactly like
  // `tools-tab.tsx`'s "Attach a shared tool" section does.
  const allToolsQuery = useTools();
  const connections = connectionsQuery.data?.items ?? [];
  const enabled = statusQuery.data?.enabled ?? false;

  const [actionsFor, setActionsFor] = React.useState<AppConnectionOut | null>(null);

  function closeActionsDialog(open: boolean) {
    if (!open) setActionsFor(null);
  }

  /**
   * The picker attaches straight to `agentId` server-side
   * (`POST .../materialise` → `attach_tools`, which appends to the agent's
   * *stored* `config.tools.tool_ids` as its own save). Merging the result
   * into this form's `tool_ids` — rather than only refetching the tools list
   * — matters: `buildAgentUpdate` sends `{...stored.tools, ...edited.tools}`
   * and arrays replace wholesale, so without this merge the *next* Save from
   * this open editor would post the `tool_ids` captured at load, silently
   * un-attaching the action just added. `shouldDirty: false` — this reflects
   * what the server already did, not a new edit the "unsaved changes" guard
   * should warn about.
   */
  function handleActionsAdded(result: AppActionsPickOut) {
    const addedIds = [...(result.tools_created ?? []), ...(result.tools_existing ?? [])];
    if (addedIds.length > 0) {
      const current = getValues("config.tools.tool_ids") ?? [];
      const merged = Array.from(new Set([...current, ...addedIds]));
      if (merged.length !== current.length) {
        setValue("config.tools.tool_ids", merged, { shouldDirty: false });
      }
    }
    void queryClient.invalidateQueries({ queryKey: ["tools"] });
  }

  function setToolAttached(id: string, attached: boolean) {
    const current = getValues("config.tools.tool_ids") ?? [];
    setValue(
      "config.tools.tool_ids",
      attached ? Array.from(new Set([...current, id])) : current.filter((existing) => existing !== id),
      { shouldDirty: true },
    );
  }

  function isAllowed(toolkit: string): boolean {
    return allowedToolkits.length === 0 || allowedToolkits.includes(toolkit);
  }

  const allowedCount = connections.filter((c) => isAllowed(c.toolkit)).length;

  function toggleAllowed(toolkit: string, checked: boolean) {
    if (checked) {
      if (allowedToolkits.length === 0) return; // already every app
      setValue(
        "config.tools.apps.allowed_toolkits",
        Array.from(new Set([...allowedToolkits, toolkit])),
        { shouldDirty: true },
      );
      return;
    }
    const base = allowedToolkits.length === 0 ? connections.map((c) => c.toolkit) : allowedToolkits;
    setValue(
      "config.tools.apps.allowed_toolkits",
      base.filter((slug) => slug !== toolkit),
      { shouldDirty: true },
    );
  }

  function denyAction(slug: string, denied: boolean) {
    const current = getValues("config.tools.apps.denied_actions") ?? [];
    setValue(
      "config.tools.apps.denied_actions",
      denied ? Array.from(new Set([...current, slug])) : current.filter((existing) => existing !== slug),
      { shouldDirty: true },
    );
  }

  /**
   * Seeds a fresh destructive action into the deny list once per mount, so
   * it starts unchecked (D-V5-C7). Reads the *current* form value with
   * `getValues` rather than the render-time `deniedActions` closure: several
   * `AppActionsList` instances (one per allowed app) can each seed in the
   * same commit, and a stale closure would let the second call's `setValue`
   * clobber the first's addition.
   *
   * `shouldDirty: false`: there is no saved "the builder reviewed and
   * explicitly allowed this one" marker separate from "not denied", so
   * re-opening an already-configured agent cannot tell those apart from
   * "never reviewed" — seeding quietly (no unsaved-changes prompt from
   * merely opening the tab) is the smaller risk than either flagging the
   * whole tab dirty on load or leaving a destructive action checked by
   * default. Filed as docs/v5/_asks.md #27 — a real fix needs a ruling on
   * where "reviewed" should live.
   */
  const seenDestructive = React.useRef<Set<string>>(new Set());
  function seedDestructive(slugs: string[]) {
    const toAdd = slugs.filter((slug) => !seenDestructive.current.has(slug));
    if (toAdd.length === 0) return;
    for (const slug of toAdd) seenDestructive.current.add(slug);
    const current = getValues("config.tools.apps.denied_actions") ?? [];
    const missing = toAdd.filter((slug) => !current.includes(slug));
    if (missing.length === 0) return;
    setValue("config.tools.apps.denied_actions", [...current, ...missing], { shouldDirty: false });
  }

  const activeOption = MODE_OPTIONS.find((option) => option.value === mode) ?? MODE_OPTIONS[0];
  const allowedConnections = connections.filter((connection) => isAllowed(connection.toolkit));

  // The pure onboarding empty state only applies while `mode` is still
  // "off" — an agent already set to a dynamic mode whose workspace later
  // turned Apps off needs the picker to stay reachable (it's how a builder
  // fixes the `tools.apps.mode` validator error apps_issues raises in that
  // exact state; hiding the picker would hide the only way back to "Off").
  const showOnboarding = !statusQuery.isLoading && !enabled && mode === "off";

  function attachedToolsFor(connectionId: string): ToolOut[] {
    return (allToolsQuery.data?.items ?? []).filter(
      (tool) => isProviderTool(tool) && tool.definition.connection_id === connectionId && toolIds.includes(tool.id),
    );
  }

  return (
    <Section
      id="tools-apps"
      title="Connected apps"
      description="Let this agent use apps connected in Tools → Apps — Gmail, Slack, calendars and more."
    >
      {statusQuery.isError ? (
        <SectionRow>
          <ErrorBanner message={errorMessage(statusQuery.error)} onRetry={() => statusQuery.refetch()} />
        </SectionRow>
      ) : showOnboarding ? (
        <SectionRow>
          <EmptyState
            compact
            title="Apps aren't set up yet"
            description="Turn on Apps in Tools → Apps, then come back to give this agent some actions."
            action={
              <Button asChild variant="outline" size="sm">
                <Link href="/console/tools?tab=apps">Go to Apps</Link>
              </Button>
            }
          />
        </SectionRow>
      ) : (
        <>
          {!enabled ? (
            <SectionRow className="bg-warning-soft">
              <p className="text-[0.8125rem] text-warning-text">
                Apps are turned off for this workspace — this agent can&rsquo;t use them until they&rsquo;re back on.{" "}
                <Link href="/console/tools?tab=apps" className="underline underline-offset-2">
                  Go to Apps
                </Link>{" "}
                to turn them on, or set this to Off below.
              </p>
            </SectionRow>
          ) : null}

          <SectionRow className="flex flex-col gap-2">
            <Label htmlFor="apps-mode" className="text-sm font-medium text-foreground">
              How this agent uses apps
            </Label>
            <Controller
              control={control}
              name="config.tools.apps.mode"
              render={({ field }) => (
                <Select value={field.value ?? "off"} onValueChange={field.onChange}>
                  <SelectTrigger id="apps-mode" className="w-full sm:w-96" data-issue-path="tools.apps.mode">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {MODE_OPTIONS.map((option) => (
                      <SelectItem key={option.value} value={option.value}>
                        {option.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            />
            <p className="text-[0.8125rem] text-muted-foreground">{activeOption.helper}</p>
            {activeOption.latency ? (
              <p className="text-[0.8125rem] text-warning-text">
                The agent looks tools up during the call; expect slower replies.
              </p>
            ) : null}
          </SectionRow>

          {mode !== "off" ? (
            <SectionRow className="flex flex-col gap-3">
              <p className="text-sm font-medium text-foreground">
                {mode === "actions" ? "Connected apps" : "Apps this agent may use"}
              </p>
              {connectionsQuery.isLoading ? (
                <p className="text-[0.8125rem] text-muted-foreground">Loading connected apps…</p>
              ) : connections.length === 0 ? (
                <EmptyState
                  compact
                  title="No apps connected yet"
                  description="Connect one in Tools → Apps."
                  action={
                    <Button asChild variant="outline" size="sm">
                      <Link href="/console/tools?tab=apps">Go to Apps</Link>
                    </Button>
                  }
                />
              ) : (
                <ul className="flex flex-col gap-2">
                  {connections.map((connection) => (
                    <AppRow
                      key={connection.id}
                      connection={connection}
                      mode={mode}
                      allowed={isAllowed(connection.toolkit)}
                      allowToggleOff={!isAllowed(connection.toolkit) || allowedCount > 1 || allowedToolkits.length === 0}
                      onToggleAllowed={(checked) => toggleAllowed(connection.toolkit, checked)}
                      onOpenActions={() => setActionsFor(connection)}
                      attachedTools={mode === "actions" ? attachedToolsFor(connection.id) : []}
                      onToggleTool={setToolAttached}
                    />
                  ))}
                </ul>
              )}
            </SectionRow>
          ) : null}

          {(mode === "server" || mode === "router") && allowedConnections.length > 0 ? (
            <SectionRow className="flex flex-col gap-3">
              <p className="text-sm font-medium text-foreground">Actions the agent may take</p>
              <p className="text-[0.8125rem] text-muted-foreground">
                Destructive actions (delete, remove, send money) start unchecked.
              </p>
              <div className="flex flex-col gap-3" data-issue-path="tools.apps.denied_actions">
                {allowedConnections.map((connection) => (
                  <AppActionsList
                    key={connection.id}
                    toolkit={connection.toolkit}
                    toolkitName={connection.toolkit_name ?? connection.toolkit}
                    denied={deniedActions}
                    onDeny={denyAction}
                    onSeedDestructive={seedDestructive}
                  />
                ))}
              </div>
            </SectionRow>
          ) : null}
        </>
      )}

      {actionsFor ? (
        <ActionsDialog
          connectionId={actionsFor.id}
          toolkitSlug={actionsFor.toolkit}
          toolkitName={actionsFor.toolkit_name ?? actionsFor.toolkit}
          pickedActions={actionsFor.picked_actions ?? []}
          open={Boolean(actionsFor)}
          onOpenChange={closeActionsDialog}
          presetAgentId={agentId}
          onAdded={handleActionsAdded}
        />
      ) : null}
    </Section>
  );
}

/** One connected app's row: identity, status, and either an Actions button (+ attached actions) or an allow checkbox. */
function AppRow({
  connection,
  mode,
  allowed,
  allowToggleOff,
  onToggleAllowed,
  onOpenActions,
  attachedTools,
  onToggleTool,
}: {
  connection: AppConnectionOut;
  mode: Mode;
  allowed: boolean;
  /** False only for the last remaining allowed app among more than one connected app (unchecking it would silently mean "every app" again, per `AppsMode.allowed_toolkits`'s "empty = all" contract). */
  allowToggleOff: boolean;
  onToggleAllowed: (checked: boolean) => void;
  onOpenActions: () => void;
  attachedTools: ToolOut[];
  onToggleTool: (id: string, attached: boolean) => void;
}) {
  const name = connection.toolkit_name ?? connection.toolkit;
  const needsReconnect = connection.needs_reconnect || connection.status !== "active";
  const inputId = `apps-allow-${connection.id}`;
  const uncheckDisabled = allowed && !allowToggleOff;

  return (
    <li className="flex flex-col gap-2 rounded-md border border-border p-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <VendorMark vendor={name} />
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-foreground">{name}</p>
            {needsReconnect ? (
              <Link href="/console/tools?tab=apps" className="text-xs text-warning-text underline underline-offset-2">
                Needs reconnect
              </Link>
            ) : (
              <StatusChip tone="success" size="sm">
                Connected
              </StatusChip>
            )}
          </div>
        </div>
        {mode === "actions" ? (
          <Button type="button" variant="outline" size="sm" onClick={onOpenActions}>
            Actions
          </Button>
        ) : (
          <div className="flex items-center gap-1.5">
            <Checkbox
              id={inputId}
              checked={allowed}
              disabled={uncheckDisabled}
              onCheckedChange={(v) => onToggleAllowed(v === true)}
            />
            <Label
              htmlFor={inputId}
              className="text-[0.8125rem] font-normal text-muted-foreground"
              title={uncheckDisabled ? "At least one connected app must stay allowed" : undefined}
            >
              Let the agent use this app
            </Label>
          </div>
        )}
      </div>
      {mode === "actions" && attachedTools.length > 0 ? (
        <ul className="flex flex-wrap gap-1.5 pl-7">
          {attachedTools.map((tool) => (
            <li key={tool.id} className="inline-flex items-center gap-1 rounded-full bg-secondary py-0.5 pr-1 pl-2.5 font-mono text-xs text-secondary-foreground">
              {tool.name}
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                className="size-4"
                aria-label={`Remove ${tool.name} from this agent`}
                onClick={() => onToggleTool(tool.id, false)}
              >
                ×
              </Button>
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

/** One connected app's action checkboxes for the "Actions the agent may take" list. */
function AppActionsList({
  toolkit,
  toolkitName,
  denied,
  onDeny,
  onSeedDestructive,
}: {
  toolkit: string;
  toolkitName: string;
  denied: string[];
  onDeny: (slug: string, denied: boolean) => void;
  onSeedDestructive: (slugs: string[]) => void;
}) {
  const actionsQuery = useToolProviderActions(toolkit, { limit: 50 });
  const items = React.useMemo(() => actionsQuery.data?.pages.flatMap((page) => page.items) ?? [], [actionsQuery.data]);

  React.useEffect(() => {
    const destructiveSlugs = items.filter(isDestructive).map((action) => action.slug);
    if (destructiveSlugs.length > 0) onSeedDestructive(destructiveSlugs);
    // `onSeedDestructive` is stable enough for this effect's purpose (it only
    // acts once per slug, via its own ref-guarded set upstream).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items]);

  if (actionsQuery.isLoading) {
    return <p className="text-[0.8125rem] text-muted-foreground">Loading {toolkitName}&rsquo;s actions…</p>;
  }
  if (actionsQuery.isError) {
    return <ErrorBanner message={`Couldn't load ${toolkitName}'s actions — ${errorMessage(actionsQuery.error)}`} onRetry={() => actionsQuery.refetch()} />;
  }
  if (items.length === 0) return null;

  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-xs font-medium text-muted-foreground">{toolkitName}</p>
      <ul className="flex flex-col gap-1">
        {items.map((action) => {
          const inputId = `apps-action-${toolkit}-${action.slug}`;
          const checked = !denied.includes(action.slug);
          return (
            <li key={action.slug} className="flex items-center gap-2">
              <Checkbox id={inputId} checked={checked} onCheckedChange={(v) => onDeny(action.slug, v !== true)} />
              <Label htmlFor={inputId} className="flex items-center gap-1.5 text-[0.8125rem] font-normal">
                {action.name}
                {isDestructive(action) ? (
                  <StatusChip tone="danger" size="sm">
                    Destructive
                  </StatusChip>
                ) : null}
              </Label>
            </li>
          );
        })}
      </ul>
      {actionsQuery.hasNextPage ? (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="self-start"
          disabled={actionsQuery.isFetchingNextPage}
          onClick={() => void actionsQuery.fetchNextPage()}
        >
          {actionsQuery.isFetchingNextPage ? "Loading…" : `Load more ${toolkitName} actions`}
        </Button>
      ) : null}
    </div>
  );
}
