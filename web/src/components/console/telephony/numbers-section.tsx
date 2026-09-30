"use client";

import * as React from "react";
import { toast } from "sonner";
import { HashIcon, PhoneIncomingIcon, PlusIcon, RefreshCwIcon, Trash2Icon } from "lucide-react";

import { Button, IconButton } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { SimpleSelect } from "@/components/ui/select";
import { busyLabelFor } from "@/components/shared/busy-label";
import { CopyButton } from "@/components/shared/copy-button";
import { EmptyState } from "@/components/shared/empty-state";
import { Field, FormError } from "@/components/shared/field";
import { Highlight, ListNoMatches, ListSearchField, useListSearch } from "@/components/shared/list-search";
import { SkeletonRows } from "@/components/shared/loading-state";
import { RelativeTime } from "@/components/shared/relative-time";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { Section, SectionRow } from "@/components/shared/section";
import { StatusPill } from "@/components/shared/status-chip";
import { Tag } from "@/components/shared/tag";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { IfCan, useCan } from "@/components/console/shared/permission";
import { useConnections } from "@/hooks/useConnections";
import type {
  AgentOut,
  ConnectionOut,
  NumbersRefreshOut,
  PhoneNumberOut,
  TrunkOut,
} from "@/contracts/lkap-contracts";

import {
  useCreateNumber,
  useDeleteNumber,
  usePhoneNumbers,
  useRefreshNumbers,
  useTrunks,
  useUpdateNumber,
} from "./hooks";
import {
  ATTACH_STATE_META,
  E164_PATTERN,
  LK_PURCHASE_COMMAND,
  agentsOnConnection,
  attachStateOf,
  isHostedNumber,
  normalizeE164,
  sipEnabled,
} from "./model";

/**
 * Numbers (V2-17, V4-05): the number → agent map. A number is either typed in
 * on a SIP trunk or hosted by LiveKit (bought in the LiveKit dashboard or with
 * `lk number purchase`, then mirrored here by "Refresh from LiveKit"). Picking
 * an inbound agent creates (or replaces) the number's own dispatch rule on
 * LiveKit; "Nobody" removes it. The console never buys or gives back a number.
 *
 * Telephony config needs `admin` server-side (`auth/roles.py::ROUTE_POLICY`):
 * below that the write controls are not rendered and each number's agent reads
 * as text (decision D12).
 */
export function NumbersSection({ agents }: { agents: AgentOut[] }) {
  const { data, isLoading, isError, error, refetch } = usePhoneNumbers();
  const trunksData = useTrunks().data;
  const connectionsData = useConnections().data;
  const [creating, setCreating] = React.useState(false);
  const [gettingNumber, setGettingNumber] = React.useState(false);
  const numbers = React.useMemo(() => data?.items ?? [], [data]);
  const trunks = React.useMemo(() => trunksData?.items ?? [], [trunksData]);
  const connections = React.useMemo(() => connectionsData?.items ?? [], [connectionsData]);
  const agentName = (id: string | null | undefined) => (id ? (agents.find((a) => a.id === id)?.name ?? "") : "");
  const search = useListSearch("telephony-numbers", numbers, (n) => [
    n.e164,
    n.label,
    n.region,
    sourceText(n, trunks),
    agentName(n.inbound_agent_id),
    ATTACH_STATE_META[attachStateOf(n)].label,
  ]);
  const query = search.query;

  const columns: ResponsiveTableColumn<PhoneNumberOut>[] = [
    {
      id: "number",
      header: "Number",
      cell: (n) => <NumberCell number={n} query={query} />,
    },
    {
      id: "source",
      header: "Source",
      cell: (n) => <SourceChip number={n} trunks={trunks} />,
    },
    {
      id: "agent",
      header: "Inbound agent",
      interactive: true,
      cell: (n) => <InboundAgentPicker number={n} agents={agents} trunks={trunks} connections={connections} />,
    },
    {
      id: "routing",
      header: "Routing",
      interactive: true,
      cell: (n) => <RoutingCell number={n} />,
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "end",
      interactive: true,
      cell: (n) => <DeleteNumberButton number={n} />,
    },
  ];

  return (
    <Section
      id="numbers"
      title="Phone numbers"
      description="Which agent answers each number: numbers on your SIP trunks and numbers hosted by LiveKit."
      aside={
        // The section's aside slot doesn't shrink; cap the row at the phone content width so it wraps at 390 px.
        <div className="flex max-w-[calc(100vw-5rem)] flex-wrap items-center justify-end gap-2">
          <Button type="button" size="sm" variant="ghost" onClick={() => setGettingNumber(true)}>
            <PhoneIncomingIcon aria-hidden="true" />
            Get a number
          </Button>
          <IfCan min="admin">
            <RefreshFromLiveKit connections={connections} />
            <Button type="button" size="sm" variant="secondary" onClick={() => setCreating(true)}>
              <PlusIcon aria-hidden="true" />
              Add number
            </Button>
          </IfCan>
        </div>
      }
    >
      {isLoading ? (
        <SectionRow>
          <SkeletonRows label="Loading phone numbers" rows={2} rowClassName="h-14" />
        </SectionRow>
      ) : isError ? (
        <SectionRow>
          <ErrorBanner error={error} context={{ action: "load phone numbers" }} onRetry={() => void refetch()} />
        </SectionRow>
      ) : numbers.length === 0 ? (
        <SectionRow className="flex flex-col gap-4">
          <EmptyState
            variant="plain"
            icon={HashIcon}
            title="No numbers yet"
            description="Add the numbers your carrier delivers to an inbound trunk, or use a number hosted by LiveKit."
            className="py-6"
          />
          <GetNumberSteps />
        </SectionRow>
      ) : (
        <>
          {search.showSearch ? (
            <SectionRow>
              <ListSearchField search={search} label="Search phone numbers" total={numbers.length} className="mb-0" />
            </SectionRow>
          ) : null}
          {search.noMatches ? (
            <SectionRow>
              <ListNoMatches search={search} items="numbers" />
            </SectionRow>
          ) : (
            <ResponsiveTable<PhoneNumberOut>
              columns={columns}
              rows={search.filtered}
              label="Phone numbers"
              getRowKey={(n) => n.id}
              renderCard={(n) => (
                <div className="flex flex-col gap-2">
                  <div className="flex items-start justify-between gap-2">
                    <NumberCell number={n} query={query} />
                    <DeleteNumberButton number={n} />
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <SourceChip number={n} trunks={trunks} />
                    <RoutingCell number={n} />
                  </div>
                  <InboundAgentPicker number={n} agents={agents} trunks={trunks} connections={connections} />
                </div>
              )}
            />
          )}
        </>
      )}
      <NumberDialog open={creating} onOpenChange={setCreating} agents={agents} trunks={trunks} />
      <GetNumberDialog open={gettingNumber} onOpenChange={setGettingNumber} />
    </Section>
  );
}

function sourceText(number: PhoneNumberOut, trunks: TrunkOut[]): string {
  if (isHostedNumber(number)) return "LiveKit";
  return trunks.find((t) => t.id === number.trunk_id)?.name ?? "";
}

function NumberCell({ number, query }: { number: PhoneNumberOut; query: string }) {
  const subtitle = [number.label, isHostedNumber(number) ? number.region : ""].filter(Boolean).join(" · ");
  return (
    <div className="min-w-0">
      <p className="font-mono text-label font-medium text-foreground tabular-nums">
        <Highlight text={number.e164} query={query} />
      </p>
      {subtitle ? (
        <p className="truncate text-caption text-text-secondary">
          <Highlight text={subtitle} query={query} />
        </p>
      ) : null}
      {isHostedNumber(number) && number.lk_synced_at ? (
        <p className="text-caption text-text-tertiary">
          Synced <RelativeTime iso={number.lk_synced_at} />
        </p>
      ) : null}
    </div>
  );
}

function SourceChip({ number, trunks }: { number: PhoneNumberOut; trunks: TrunkOut[] }) {
  if (isHostedNumber(number)) return <Tag>LiveKit</Tag>;
  return <span className="text-label text-text-secondary">{sourceText(number, trunks) || "—"}</span>;
}

function RoutingCell({ number }: { number: PhoneNumberOut }) {
  const state = attachStateOf(number);
  const meta = ATTACH_STATE_META[state];
  const update = useUpdateNumber();
  const { can } = useCan("admin");
  const canReattach = can && isHostedNumber(number) && state === "detached" && Boolean(number.inbound_agent_id);
  return (
    <div className="flex flex-col items-start gap-1">
      <div className="flex flex-wrap items-center gap-2">
        <StatusPill tone={meta.tone} size="sm">
          {meta.label}
        </StatusPill>
        {canReattach ? (
          <Button
            type="button"
            size="sm"
            variant="secondary"
            aria-label={`Re-attach ${number.e164}`}
            busy={update.isPending}
            busyLabel="Re-attaching…"
            onClick={() =>
              update.mutate(
                { id: number.id, body: { inbound_agent_id: number.inbound_agent_id } },
                {
                  onSuccess: (saved) => {
                    toast.success(`${saved.e164} re-attached`);
                    for (const warning of saved.warnings ?? []) toast.warning(warning);
                  },
                  onError: (err) => toast.error(errorMessage(err)),
                },
              )
            }
          >
            Re-attach
          </Button>
        ) : null}
      </div>
      {state === "detached" || state === "offline" ? (
        <p className="max-w-[32ch] text-caption text-text-secondary">{meta.hint}</p>
      ) : null}
    </div>
  );
}

function refreshSummary(results: NumbersRefreshOut[]): string {
  const sum = (key: "added" | "updated" | "released") => results.reduce((total, r) => total + (r[key] ?? 0), 0);
  return `${sum("added")} added, ${sum("updated")} updated, ${sum("released")} released`;
}

/** "Refresh from LiveKit" plus, with several SIP connections, the connection to read. Admins only (the caller gates it). */
export function RefreshFromLiveKit({ connections }: { connections: ConnectionOut[] }) {
  const refresh = useRefreshNumbers();
  const capable = connections.filter(sipEnabled);
  const [chosen, setChosen] = React.useState("");
  const connectionId = chosen || capable[0]?.id || "";
  const reason =
    capable.length === 0
      ? "None of your connections reports SIP. Test a connection first."
      : "Read the numbers of your LiveKit project";

  return (
    <>
      {capable.length > 1 ? (
        <SimpleSelect
          aria-label="Connection to refresh"
          size="sm"
          value={connectionId}
          className="w-44 max-w-full"
          onValueChange={setChosen}
          options={capable.map((c) => ({ value: c.id, label: c.name }))}
        />
      ) : null}
      <Button
        type="button"
        size="sm"
        variant="secondary"
        disabled={capable.length === 0 || refresh.isPending}
        aria-busy={refresh.isPending || undefined}
        title={reason}
        onClick={() =>
          refresh.mutate(
            { connection_id: connectionId || null },
            {
              onSuccess: (results) => {
                toast.success(`Refreshed from LiveKit: ${refreshSummary(results)}`);
                for (const result of results) {
                  for (const e164 of result.conflicts ?? []) {
                    toast.warning(`${e164} is already registered as a trunk number and was skipped`);
                  }
                }
              },
              onError: (err) => toast.error(errorMessage(err)),
            },
          )
        }
      >
        <RefreshCwIcon aria-hidden="true" className={refresh.isPending ? "animate-spin" : undefined} />
        Refresh from LiveKit
      </Button>
    </>
  );
}

/** The three steps to a LiveKit-hosted number; text only: the user buys, never the console. */
function GetNumberSteps() {
  return (
    <div className="flex flex-col gap-3 text-label">
      <ol className="flex list-decimal flex-col gap-3 pl-5">
        <li>
          Buy a number in the LiveKit dashboard under <strong>Telephony → Phone numbers</strong>, or run this with the{" "}
          <code>lk</code> CLI:
          <div className="mt-1.5 flex max-w-full items-center gap-2 rounded border border-border bg-muted px-2 py-1">
            <code className="min-w-0 flex-1 truncate font-mono text-label">{LK_PURCHASE_COMMAND}</code>
            <CopyButton value={LK_PURCHASE_COMMAND} label="Copy the purchase command" size="sm" />
          </div>
        </li>
        <li>
          An admin presses <strong>Refresh from LiveKit</strong> here.
        </li>
        <li>Pick the inbound agent that answers the number.</li>
      </ol>
      <p className="text-caption text-text-secondary">
        US numbers only, inbound only; the Build plan includes one number and 50 inbound minutes.
      </p>
    </div>
  );
}

export function GetNumberDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>Get a LiveKit phone number</DialogTitle>
          <DialogDescription>
            A number hosted by LiveKit needs no SIP trunk. You buy it in your LiveKit account; this console only reads
            it and routes it.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <GetNumberSteps />
        </DialogBody>
        <DialogFooter>
          <Button type="button" variant="primary" onClick={() => onOpenChange(false)}>
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function InboundAgentPicker({
  number,
  agents,
  trunks,
  connections = [],
}: {
  number: PhoneNumberOut;
  agents: AgentOut[];
  trunks: TrunkOut[];
  connections?: ConnectionOut[];
}) {
  const update = useUpdateNumber();
  const { can } = useCan("admin");
  const hosted = isHostedNumber(number);
  const trunk = trunks.find((t) => t.id === number.trunk_id);
  const routable = hosted ? attachStateOf(number) !== "released" : trunk?.direction === "inbound";
  // A hosted number only reaches the workers of its own LiveKit project.
  const choices = hosted ? agentsOnConnection(agents, connections, number.connection_id) : agents;
  const current = number.inbound_agent_id ?? "";
  const listed = choices.some((a) => a.id === current) ? choices : [...choices, ...agents.filter((a) => a.id === current)];
  const currentName = agents.find((a) => a.id === current)?.name ?? (current ? "Unknown agent" : "Nobody");

  // Read-only views instead of a disabled control (docs/ui/DESIGN-SYSTEM.md section 8.5).
  if (!can || !routable) {
    const blocked = !routable
      ? hosted
        ? "No longer in your LiveKit project"
        : "Needs an inbound trunk to route calls"
      : null;
    return (
      <div className="min-w-0" data-slot="inbound-agent">
        <p className="truncate text-label text-foreground">{currentName}</p>
        {blocked ? <p className="text-caption text-text-secondary">{blocked}</p> : null}
      </div>
    );
  }

  return (
    <SimpleSelect
      aria-label={`Inbound agent for ${number.e164}`}
      value={current}
      disabled={update.isPending}
      className="w-56 max-w-full"
      onValueChange={(next) =>
        update.mutate(
          { id: number.id, body: { inbound_agent_id: next || null } },
          {
            onSuccess: (saved) => {
              toast.success(saved.inbound_agent_id ? `${saved.e164} routed` : `${saved.e164} no longer routed`);
              for (const warning of saved.warnings ?? []) toast.warning(warning);
            },
            onError: (err) => toast.error(errorMessage(err)),
          },
        )
      }
      options={[{ value: "", label: "Nobody" }, ...listed.map((agent) => ({ value: agent.id, label: agent.name }))]}
    />
  );
}

function DeleteNumberButton({ number }: { number: PhoneNumberOut }) {
  const remove = useDeleteNumber();
  const [confirming, setConfirming] = React.useState(false);
  // Row actions a person can't use are not rendered (decision D12).
  const { can } = useCan("admin");
  if (!can) return null;
  const hosted = isHostedNumber(number);
  return (
    <>
      <IconButton label={`Delete ${number.e164}`} disabled={remove.isPending} onClick={() => setConfirming(true)}>
        <Trash2Icon />
      </IconButton>
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={`Remove ${number.e164}?`}
        description={
          hosted
            ? "It is detached from its agent and forgotten here. The number stays in your LiveKit project; manage or give it up in the LiveKit dashboard."
            : "Its dispatch rule is deleted and it stops reaching an agent. The trunk keeps the number in its list."
        }
        confirmLabel="Remove number"
        onConfirm={async () => {
          await remove.mutateAsync(number.id);
          toast.success(`${number.e164} removed`);
        }}
      />
    </>
  );
}

function NumberDialog({
  open,
  onOpenChange,
  agents,
  trunks,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  agents: AgentOut[];
  trunks: TrunkOut[];
}) {
  const create = useCreateNumber();
  const [e164, setE164] = React.useState("");
  const [trunkId, setTrunkId] = React.useState("");
  const [agentId, setAgentId] = React.useState("");
  const [label, setLabel] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const trunk = trunks.find((t) => t.id === trunkId);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const value = normalizeE164(e164);
    if (!E164_PATTERN.test(value)) {
      setError("Enter the number in E.164 form, e.g. +15551234567.");
      return;
    }
    setError(null);
    create.mutate(
      {
        e164: value,
        trunk_id: trunkId || null,
        inbound_agent_id: trunk?.direction === "inbound" && agentId ? agentId : null,
        label: label.trim(),
      },
      {
        onSuccess: () => {
          toast.success(`${value} added`);
          setE164("");
          setLabel("");
          setAgentId("");
          onOpenChange(false);
        },
        onError: (err) => setError(errorMessage(err)),
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="md">
        <form onSubmit={submit} className="flex min-h-0 flex-1 flex-col">
          <DialogHeader>
            <DialogTitle>Add phone number</DialogTitle>
            <DialogDescription>
              A number you own at your carrier. It is added to the trunk you pick. Numbers hosted by LiveKit come in
              with Refresh from LiveKit instead.
            </DialogDescription>
          </DialogHeader>
          <DialogBody className="gap-4">
            <Field label="Number" htmlFor="number-e164">
              <Input
                id="number-e164"
                value={e164}
                placeholder="+15551234567"
                inputMode="tel"
                className="font-mono tabular-nums"
                onChange={(e) => setE164(e.target.value)}
              />
            </Field>
            <Field label="Trunk" htmlFor="number-trunk" optional>
              <SimpleSelect
                id="number-trunk"
                value={trunkId}
                onValueChange={setTrunkId}
                options={[
                  { value: "", label: "None" },
                  ...trunks.map((t) => ({ value: t.id, label: `${t.name} (${t.direction})` })),
                ]}
              />
            </Field>
            <Field label="Inbound agent" htmlFor="number-agent" optional hint="Needs an inbound trunk">
              <SimpleSelect
                id="number-agent"
                value={agentId}
                disabled={trunk?.direction !== "inbound"}
                onValueChange={setAgentId}
                options={[{ value: "", label: "Nobody" }, ...agents.map((agent) => ({ value: agent.id, label: agent.name }))]}
              />
            </Field>
            <Field label="Label" htmlFor="number-label" optional>
              <Input id="number-label" value={label} onChange={(e) => setLabel(e.target.value)} />
            </Field>
            <FormError>{error}</FormError>
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              busy={create.isPending}
              busyLabel={busyLabelFor("Add number")}
              disabled={!e164.trim()}
            >
              Add number
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
