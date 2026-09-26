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
  const reviewedActions = watch("config.tools.apps.reviewed_actions") ?? [];
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

  // Distinct *apps*, not connections — R-V5-13 lets one toolkit have several
  // accounts (several connection rows), and "at least one app must stay
  // allowed" (`allowToggleOff` below) means one app, not one account.
  const allowedCount = new Set(connections.filter((c) => isAllowed(c.toolkit)).map((c) => c.toolkit)).size;

  // Every account of the workspace, grouped by toolkit (R-V5-13: one app may
  // have several) — recomputed each render rather than memoized: `connections`
  // is a fresh `?? []` fallback array on every render anyway (react-query
  // only stabilizes `.data`, not this derived default), so a dependency-array
  // memo here would just re-run every time regardless.
  const appGroups: AppConnectionOut[][] = [];
  {
    const byToolkit = new Map<string, AppConnectionOut[]>();
    for (const connection of connections) {
      const list = byToolkit.get(connection.toolkit);
      if (list) list.push(connection);
      else byToolkit.set(connection.toolkit, [connection]);
    }
    appGroups.push(...byToolkit.values());
  }

  /** How many accounts this toolkit has among the workspace's connections (for the "(Work)" label on a single row in "actions" mode). */
  function accountCountFor(toolkit: string): number {
    return connections.filter((c) => c.toolkit === toolkit).length;
  }

  /** `AppsMode.accounts[toolkit]` if set, else just the app's default account — "empty = the default account" (R-V5-13). */
  function selectedAccountsFor(toolkit: string, accounts: AppConnectionOut[]): string[] {
    const configured = (watch("config.tools.apps.accounts") ?? {})[toolkit];
    if (configured && configured.length > 0) return configured;
    const defaultAccount = accounts.find((a) => a.is_default) ?? accounts[0];
    return defaultAccount ? [defaultAccount.id] : [];
  }

  /**
   * The per-app account chooser (R-V5-13 item 4, "server"/"router" modes):
   * writes `tools.apps.accounts[toolkit]`. The default account can never be
   * unchecked down to zero — that's exactly what an empty/absent entry
   * already means, so the two states collapse back to one (no entry) rather
   * than storing a redundant `[defaultId]`.
   */
  function toggleAccount(toolkit: string, accountId: string, checked: boolean, accounts: AppConnectionOut[]) {
    const defaultAccount = accounts.find((a) => a.is_default) ?? accounts[0];
    const current = selectedAccountsFor(toolkit, accounts);
    const nextSet = new Set(current);
    if (checked) nextSet.add(accountId);
    else nextSet.delete(accountId);
    if (defaultAccount && nextSet.size === 0) nextSet.add(defaultAccount.id);
    const nextArr = accounts.filter((a) => nextSet.has(a.id)).map((a) => a.id);
    const isJustDefault = defaultAccount ? nextArr.length === 1 && nextArr[0] === defaultAccount.id : nextArr.length === 0;
    const currentAccounts = getValues("config.tools.apps.accounts") ?? {};
    const nextAccounts = { ...currentAccounts };
    if (isJustDefault) delete nextAccounts[toolkit];
    else nextAccounts[toolkit] = nextArr;
    setValue("config.tools.apps.accounts", nextAccounts, { shouldDirty: true });
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

  /** A non-destructive action's checkbox: the only field it ever touches is `denied_actions` (unchanged). */
  function denyAction(slug: string, denied: boolean) {
    const current = getValues("config.tools.apps.denied_actions") ?? [];
    setValue(
      "config.tools.apps.denied_actions",
      denied ? Array.from(new Set([...current, slug])) : current.filter((existing) => existing !== slug),
      { shouldDirty: true },
    );
  }

  /**
   * A destructive action's checkbox (R-V5-9): the card never seeds
   * `denied_actions` on mount any more — the server computes the effective
   * deny list from `reviewed_actions` (`effective_denied_actions`,
   * `lkap_contracts.tool_providers`), so an unreviewed destructive action is
   * blocked whatever this card does, with no client-side write needed just
   * to open the tab.
   *
   * Ticking (reviewing and allowing): adds the slug to `reviewed_actions`
   * and removes it from `denied_actions` — the latter matters for an agent
   * saved under the old client-side seed (ask #47), where a since-allowed
   * destructive action could still carry a stale deny entry.
   *
   * Unticking a reviewed one (reviewing and denying): adds the slug to
   * `denied_actions`; `reviewed_actions` is left alone — it's already
   * reviewed, the decision is just "no".
   */
  function reviewAction(slug: string, allow: boolean) {
    const currentReviewed = getValues("config.tools.apps.reviewed_actions") ?? [];
    const currentDenied = getValues("config.tools.apps.denied_actions") ?? [];
    if (allow) {
      if (!currentReviewed.includes(slug)) {
        setValue("config.tools.apps.reviewed_actions", [...currentReviewed, slug], { shouldDirty: true });
      }
      if (currentDenied.includes(slug)) {
        setValue(
          "config.tools.apps.denied_actions",
          currentDenied.filter((existing) => existing !== slug),
          { shouldDirty: true },
        );
      }
      return;
    }
    if (!currentDenied.includes(slug)) {
      setValue("config.tools.apps.denied_actions", [...currentDenied, slug], { shouldDirty: true });
    }
  }

  const activeOption = MODE_OPTIONS.find((option) => option.value === mode) ?? MODE_OPTIONS[0];
  // One entry per *app*, not per account (`appGroups` groups the raw
  // `connections` list) — "Actions the agent may take" is scoped to a
  // toolkit's actions, so two accounts of the same app must not duplicate it.
  const allowedAppGroups = appGroups.filter((accounts) => isAllowed(accounts[0].toolkit));

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
              ) : mode === "actions" ? (
                <ul className="flex flex-col gap-2">
                  {connections.map((connection) => (
                    <AppRow
                      key={connection.id}
                      connection={connection}
                      showAccountLabel={accountCountFor(connection.toolkit) > 1}
                      onOpenActions={() => setActionsFor(connection)}
                      attachedTools={attachedToolsFor(connection.id)}
                      onToggleTool={setToolAttached}
                    />
                  ))}
                </ul>
              ) : (
                <ul className="flex flex-col gap-2">
                  {appGroups.map((accounts) => {
                    const toolkit = accounts[0].toolkit;
                    return (
                      <AppAllowRow
                        key={toolkit}
                        toolkit={toolkit}
                        accounts={accounts}
                        allowed={isAllowed(toolkit)}
                        allowToggleOff={!isAllowed(toolkit) || allowedCount > 1 || allowedToolkits.length === 0}
                        onToggleAllowed={(checked) => toggleAllowed(toolkit, checked)}
                        selectedAccountIds={selectedAccountsFor(toolkit, accounts)}
                        onToggleAccount={(accountId, checked) => toggleAccount(toolkit, accountId, checked, accounts)}
                      />
                    );
                  })}
                </ul>
              )}
            </SectionRow>
          ) : null}

          {(mode === "server" || mode === "router") && allowedAppGroups.length > 0 ? (
            <SectionRow className="flex flex-col gap-3">
              <p className="text-sm font-medium text-foreground">Actions the agent may take</p>
              <p className="text-[0.8125rem] text-muted-foreground">
                Destructive actions (delete, remove, send money) stay blocked until you review them.
              </p>
              <div className="flex flex-col gap-3" data-issue-path="tools.apps.denied_actions">
                {allowedAppGroups.map((accounts) => (
                  <AppActionsList
                    key={accounts[0].toolkit}
                    toolkit={accounts[0].toolkit}
                    toolkitName={accounts[0].toolkit_name ?? accounts[0].toolkit}
                    denied={deniedActions}
                    reviewed={reviewedActions}
                    onDeny={denyAction}
                    onReview={reviewAction}
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
          accountLabel={accountCountFor(actionsFor.toolkit) > 1 ? actionsFor.label : undefined}
        />
      ) : null}
    </Section>
  );
}

/** One connected account's row ("actions" mode): identity, status, its own Actions button and attached-action chips. */
function AppRow({
  connection,
  showAccountLabel,
  onOpenActions,
  attachedTools,
  onToggleTool,
}: {
  connection: AppConnectionOut;
  /** True once the app has more than one account (R-V5-13) — shows the account's label so the two rows are distinguishable. */
  showAccountLabel: boolean;
  onOpenActions: () => void;
  attachedTools: ToolOut[];
  onToggleTool: (id: string, attached: boolean) => void;
}) {
  const name = connection.toolkit_name ?? connection.toolkit;
  const needsReconnect = connection.needs_reconnect || connection.status !== "active";

  return (
    <li className="flex flex-col gap-2 rounded-md border border-border p-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <VendorMark vendor={name} />
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-foreground">
              {name}{" "}
              {showAccountLabel ? <span className="font-normal text-muted-foreground">({connection.label ?? name})</span> : null}
            </p>
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
        <Button type="button" variant="outline" size="sm" onClick={onOpenActions}>
          Actions
        </Button>
      </div>
      {attachedTools.length > 0 ? (
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

/**
 * One app's row in the "server"/"router" modes: the app-level "Let the
 * agent use this app" checkbox (`allowed_toolkits`), plus — once the app has
 * more than one account — a nested multi-select writing
 * `tools.apps.accounts[toolkit]` (R-V5-13 item 4). The default account is
 * preselected and can't be unchecked down to zero (an empty entry already
 * means "just the default", so that's a no-op, not a lockout).
 */
function AppAllowRow({
  toolkit,
  accounts,
  allowed,
  allowToggleOff,
  onToggleAllowed,
  selectedAccountIds,
  onToggleAccount,
}: {
  toolkit: string;
  accounts: AppConnectionOut[];
  allowed: boolean;
  /** False only for the last remaining allowed app among more than one connected app (unchecking it would silently mean "every app" again, per `AppsMode.allowed_toolkits`'s "empty = all" contract). */
  allowToggleOff: boolean;
  onToggleAllowed: (checked: boolean) => void;
  selectedAccountIds: string[];
  onToggleAccount: (accountId: string, checked: boolean) => void;
}) {
  const representative = accounts[0];
  const name = representative.toolkit_name ?? toolkit;
  const needsReconnect = accounts.some((account) => account.needs_reconnect || account.status !== "active");
  const inputId = `apps-allow-${toolkit}`;
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
      </div>
      {allowed && accounts.length > 1 ? (
        <fieldset className="m-0 flex flex-col gap-1.5 border-0 pl-7">
          <legend className="mb-0.5 text-xs font-medium text-muted-foreground">Which accounts</legend>
          {accounts.map((account) => {
            const accountInputId = `apps-account-${account.id}`;
            const checked = selectedAccountIds.includes(account.id);
            const lastOne = account.is_default && checked && selectedAccountIds.length === 1;
            return (
              <div key={account.id} className="flex items-center gap-1.5">
                <Checkbox
                  id={accountInputId}
                  checked={checked}
                  disabled={lastOne}
                  title={lastOne ? "At least the default account must stay picked" : undefined}
                  onCheckedChange={(v) => onToggleAccount(account.id, v === true)}
                />
                <Label htmlFor={accountInputId} className="flex items-center gap-1.5 text-[0.8125rem] font-normal">
                  {account.label ?? name}
                  {account.is_default ? (
                    <StatusChip tone="neutral" size="sm">
                      Default
                    </StatusChip>
                  ) : null}
                </Label>
              </div>
            );
          })}
        </fieldset>
      ) : null}
    </li>
  );
}

/** One connected app's action checkboxes for the "Actions the agent may take" list. */
function AppActionsList({
  toolkit,
  toolkitName,
  denied,
  reviewed,
  onDeny,
  onReview,
}: {
  toolkit: string;
  toolkitName: string;
  denied: string[];
  reviewed: string[];
  onDeny: (slug: string, denied: boolean) => void;
  onReview: (slug: string, allow: boolean) => void;
}) {
  const actionsQuery = useToolProviderActions(toolkit, { limit: 50 });
  const items = React.useMemo(() => actionsQuery.data?.pages.flatMap((page) => page.items) ?? [], [actionsQuery.data]);

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
          const destructive = isDestructive(action);
          // R-V5-9: an unreviewed destructive action always renders
          // unchecked (`denied_actions` is irrelevant until it's reviewed —
          // the server blocks it either way, `effective_denied_actions`);
          // a reviewed one reflects `denied_actions` like any other action.
          const isReviewed = !destructive || reviewed.includes(action.slug);
          const checked = isReviewed && !denied.includes(action.slug);
          const onCheckedChange = destructive
            ? (v: boolean) => onReview(action.slug, v)
            : (v: boolean) => onDeny(action.slug, !v);
          return (
            <li key={action.slug} className="flex items-center gap-2">
              <Checkbox id={inputId} checked={checked} onCheckedChange={(v) => onCheckedChange(v === true)} />
              <Label htmlFor={inputId} className="flex items-center gap-1.5 text-[0.8125rem] font-normal">
                {action.name}
                {destructive ? (
                  <StatusChip tone="danger" size="sm">
                    Destructive
                  </StatusChip>
                ) : null}
                {destructive && !isReviewed ? (
                  <StatusChip tone="warning" size="sm">
                    Blocked until you review it
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
