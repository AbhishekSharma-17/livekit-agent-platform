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
import { useAppsStatus, useToolProviderActions, useToolProviderConnections } from "@/components/console/lib/api-hooks";
import { ActionsDialog } from "@/components/console/tools/apps/actions-dialog";
import type { AgentEditorForm } from "@/components/console/lib/schemas";
import type { AppActionOut, AppConnectionOut } from "@/contracts/lkap-contracts";

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
  const { control, watch, setValue } = useFormContext<AgentEditorForm>();
  const queryClient = useQueryClient();

  const mode = (watch("config.tools.apps.mode") ?? "off") as Mode;
  const allowedToolkits = watch("config.tools.apps.allowed_toolkits") ?? [];
  const deniedActions = watch("config.tools.apps.denied_actions") ?? [];

  const statusQuery = useAppsStatus();
  const connectionsQuery = useToolProviderConnections();
  const connections = connectionsQuery.data?.items ?? [];
  const enabled = statusQuery.data?.enabled ?? false;

  const [actionsFor, setActionsFor] = React.useState<AppConnectionOut | null>(null);

  function closeActionsDialog(open: boolean) {
    if (open) return;
    setActionsFor(null);
    // The picker's "Add as tools" attaches straight to `agentId` server-side
    // (`MaterialiseIn.agent_id`) — this agent's own tools list needs a
    // refetch to show the new `provider` tool with its App chip.
    void queryClient.invalidateQueries({ queryKey: ["tools"] });
  }

  function isAllowed(toolkit: string): boolean {
    return allowedToolkits.length === 0 || allowedToolkits.includes(toolkit);
  }

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
    setValue(
      "config.tools.apps.denied_actions",
      denied ? Array.from(new Set([...deniedActions, slug])) : deniedActions.filter((existing) => existing !== slug),
      { shouldDirty: true },
    );
  }

  /**
   * Seeds a fresh destructive action into the deny list once per mount, so
   * it starts unchecked (D-V5-C7). `shouldDirty: false`: there is no saved
   * "the builder reviewed and explicitly allowed this one" marker separate
   * from "not denied", so re-opening an already-configured agent cannot tell
   * those apart from "never reviewed" — seeding quietly (no unsaved-changes
   * prompt from merely opening the tab) is the smaller risk than either
   * flagging the whole tab dirty on load or leaving a destructive action
   * checked by default. Filed as docs/v5/_asks.md — a real fix needs a
   * ruling on where "reviewed" should live.
   */
  const seenDestructive = React.useRef<Set<string>>(new Set());
  function seedDestructive(slugs: string[]) {
    const toAdd = slugs.filter((slug) => !seenDestructive.current.has(slug));
    if (toAdd.length === 0) return;
    for (const slug of toAdd) seenDestructive.current.add(slug);
    const missing = toAdd.filter((slug) => !deniedActions.includes(slug));
    if (missing.length === 0) return;
    setValue("config.tools.apps.denied_actions", [...deniedActions, ...missing], { shouldDirty: false });
  }

  const activeOption = MODE_OPTIONS.find((option) => option.value === mode) ?? MODE_OPTIONS[0];
  const allowedConnections = connections.filter((connection) => isAllowed(connection.toolkit));

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
      ) : !statusQuery.isLoading && !enabled ? (
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
                      onToggleAllowed={(checked) => toggleAllowed(connection.toolkit, checked)}
                      onOpenActions={() => setActionsFor(connection)}
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
        />
      ) : null}
    </Section>
  );
}

/** One connected app's row: identity, status, and either an Actions button or an allow checkbox. */
function AppRow({
  connection,
  mode,
  allowed,
  onToggleAllowed,
  onOpenActions,
}: {
  connection: AppConnectionOut;
  mode: Mode;
  allowed: boolean;
  onToggleAllowed: (checked: boolean) => void;
  onOpenActions: () => void;
}) {
  const name = connection.toolkit_name ?? connection.toolkit;
  const needsReconnect = connection.needs_reconnect || connection.status !== "active";
  const inputId = `apps-allow-${connection.id}`;

  return (
    <li className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border p-2.5">
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
          <Checkbox id={inputId} checked={allowed} onCheckedChange={(v) => onToggleAllowed(v === true)} />
          <Label htmlFor={inputId} className="text-[0.8125rem] font-normal text-muted-foreground">
            Let the agent use this app
          </Label>
        </div>
      )}
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
    </div>
  );
}
