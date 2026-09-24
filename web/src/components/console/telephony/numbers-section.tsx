"use client";

import * as React from "react";
import { toast } from "sonner";
import { HashIcon, PhoneIncomingIcon, PlusIcon, RefreshCwIcon, Trash2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
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
import { Skeleton } from "@/components/ui/skeleton";
import {
  CopyButton,
  EmptyState,
  Field,
  Icon,
  RelativeTime,
  ResponsiveTable,
  Section,
  StatusChip,
} from "@/components/shared";
import type { ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";
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
import { NativeSelect } from "./native-select";

/**
 * Numbers (V2-17, V4-05): the number → agent map. A number is either typed in
 * on a SIP trunk or hosted by LiveKit (bought in the LiveKit dashboard or with
 * `lk number purchase`, then mirrored here by "Refresh from LiveKit"). Picking
 * an inbound agent creates (or replaces) the number's own dispatch rule on
 * LiveKit; "Nobody" removes it. The console never buys or gives back a number.
 */
export function NumbersSection({ agents }: { agents: AgentOut[] }) {
  const { data, isLoading, isError, error, refetch } = usePhoneNumbers();
  const trunksQuery = useTrunks();
  const connections = useConnections().data?.items ?? [];
  const [creating, setCreating] = React.useState(false);
  const [gettingNumber, setGettingNumber] = React.useState(false);
  const numbers = data?.items ?? [];
  const trunks = trunksQuery.data?.items ?? [];
  // Telephony config needs `admin` server-side (`auth/roles.py::ROUTE_POLICY`).
  const { canWrite } = useWriteAccess("admin");
  const writeReason = writeAccessReason("admin");

  const columns: ResponsiveTableColumn<PhoneNumberOut>[] = [
    {
      id: "number",
      header: "Number",
      cell: (n) => <NumberCell number={n} />,
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

  const openGetNumber = () => setGettingNumber(true);

  return (
    <Section
      id="numbers"
      title="Phone numbers"
      description="Which agent answers each number: numbers on your SIP trunks and numbers hosted by LiveKit."
      aside={
        <div className="flex max-w-[calc(100vw-5rem)] flex-wrap items-center justify-end gap-2">
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={!canWrite}
            title={canWrite ? undefined : writeReason}
            onClick={() => setCreating(true)}
          >
            <Icon as={PlusIcon} size="sm" />
            Add number
          </Button>
          <RefreshFromLiveKit connections={connections} />
          <Button type="button" size="sm" variant="outline" onClick={openGetNumber}>
            <Icon as={PhoneIncomingIcon} size="sm" />
            Get a number
          </Button>
        </div>
      }
    >
      {isLoading ? (
        <div className="p-5">
          <Skeleton className="h-10 w-full" />
        </div>
      ) : isError ? (
        <div className="p-5">
          <ErrorBanner message={`Couldn't load numbers: ${errorMessage(error)}`} onRetry={() => refetch()} />
        </div>
      ) : numbers.length === 0 ? (
        <div className="flex flex-col gap-4 p-5">
          <EmptyState
            compact
            icon={HashIcon}
            title="No numbers yet"
            description="Add the numbers your carrier delivers to an inbound trunk, or use a number hosted by LiveKit."
          />
          <GetNumberSteps />
        </div>
      ) : (
        <ResponsiveTable<PhoneNumberOut>
          columns={columns}
          rows={numbers}
          label="Phone numbers"
          getRowKey={(n) => n.id}
          renderCard={(n) => (
            <div className="flex flex-col gap-2">
              <div className="flex items-start justify-between gap-2">
                <NumberCell number={n} />
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
      <NumberDialog open={creating} onOpenChange={setCreating} agents={agents} trunks={trunks} />
      <GetNumberDialog open={gettingNumber} onOpenChange={setGettingNumber} />
    </Section>
  );
}

function NumberCell({ number }: { number: PhoneNumberOut }) {
  const subtitle = [number.label, isHostedNumber(number) ? number.region : ""].filter(Boolean).join(" · ");
  return (
    <div className="min-w-0">
      <p className="font-mono text-sm font-medium">{number.e164}</p>
      {subtitle ? <p className="truncate text-xs text-muted-foreground">{subtitle}</p> : null}
      {isHostedNumber(number) && number.lk_synced_at ? (
        <p className="text-xs text-muted-foreground">
          Synced <RelativeTime iso={number.lk_synced_at} />
        </p>
      ) : null}
    </div>
  );
}

function SourceChip({ number, trunks }: { number: PhoneNumberOut; trunks: TrunkOut[] }) {
  if (isHostedNumber(number)) {
    return (
      <StatusChip tone="info" size="sm">
        LiveKit
      </StatusChip>
    );
  }
  return (
    <span className="text-sm text-muted-foreground">{trunks.find((t) => t.id === number.trunk_id)?.name ?? "—"}</span>
  );
}

function RoutingCell({ number }: { number: PhoneNumberOut }) {
  const state = attachStateOf(number);
  const meta = ATTACH_STATE_META[state];
  const update = useUpdateNumber();
  const { canWrite } = useWriteAccess("admin");
  const canReattach = isHostedNumber(number) && state === "detached" && Boolean(number.inbound_agent_id);
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span title={meta.hint}>
        <StatusChip tone={meta.tone} size="sm" dot={state === "routed"}>
          {meta.label}
        </StatusChip>
      </span>
      {canReattach ? (
        <Button
          type="button"
          size="xs"
          variant="outline"
          disabled={!canWrite || update.isPending}
          title={canWrite ? "Attach the number to its dispatch rule again" : writeAccessReason("admin")}
          aria-label={`Re-attach ${number.e164}`}
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
  );
}

function refreshSummary(results: NumbersRefreshOut[]): string {
  const sum = (key: "added" | "updated" | "released") => results.reduce((total, r) => total + (r[key] ?? 0), 0);
  return `${sum("added")} added, ${sum("updated")} updated, ${sum("released")} released`;
}

export function RefreshFromLiveKit({ connections }: { connections: ConnectionOut[] }) {
  const refresh = useRefreshNumbers();
  const { canWrite } = useWriteAccess("admin");
  const capable = connections.filter(sipEnabled);
  const [chosen, setChosen] = React.useState("");
  const connectionId = chosen || capable[0]?.id || "";
  const reason = !canWrite
    ? writeAccessReason("admin")
    : capable.length === 0
      ? "None of your connections reports SIP. Test a connection first."
      : "Read the numbers of your LiveKit project";

  return (
    <>
      {capable.length > 1 ? (
        <NativeSelect
          aria-label="Connection to refresh"
          value={connectionId}
          className="h-8 max-w-44"
          onChange={(e) => setChosen(e.target.value)}
        >
          {capable.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </NativeSelect>
      ) : null}
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={!canWrite || capable.length === 0 || refresh.isPending}
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
        <Icon as={RefreshCwIcon} size="sm" />
        Refresh from LiveKit
      </Button>
    </>
  );
}

/** The three steps to a LiveKit-hosted number; text only: the user buys, never the console. */
function GetNumberSteps() {
  return (
    <div className="flex flex-col gap-3 text-sm">
      <ol className="flex list-decimal flex-col gap-3 pl-5">
        <li>
          Buy a number in the LiveKit dashboard under <strong>Telephony → Phone numbers</strong>, or run this with the{" "}
          <code>lk</code> CLI:
          <div className="mt-1.5 flex max-w-full items-center gap-2 rounded-md border border-border bg-muted px-2 py-1">
            <code className="min-w-0 flex-1 truncate font-mono text-[0.8125rem]">{LK_PURCHASE_COMMAND}</code>
            <CopyButton value={LK_PURCHASE_COMMAND} label="Copy the purchase command" size="xs" />
          </div>
        </li>
        <li>
          Press <strong>Refresh from LiveKit</strong> here.
        </li>
        <li>Pick the inbound agent that answers the number.</li>
      </ol>
      <p className="text-xs text-muted-foreground">
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
          <Button type="button" onClick={() => onOpenChange(false)}>
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
  const { canWrite } = useWriteAccess("admin");
  const hosted = isHostedNumber(number);
  const trunk = trunks.find((t) => t.id === number.trunk_id);
  const routable = hosted ? attachStateOf(number) !== "released" : trunk?.direction === "inbound";
  // A hosted number only reaches the workers of its own LiveKit project.
  const choices = hosted ? agentsOnConnection(agents, connections, number.connection_id) : agents;
  const current = number.inbound_agent_id ?? "";
  const listed = choices.some((a) => a.id === current) ? choices : [...choices, ...agents.filter((a) => a.id === current)];
  const blocked = hosted
    ? "This number is no longer in your LiveKit project"
    : "Bind the number to an inbound trunk to route its calls";
  return (
    <NativeSelect
      aria-label={`Inbound agent for ${number.e164}`}
      value={current}
      disabled={!canWrite || !routable || update.isPending}
      title={!canWrite ? writeAccessReason("admin") : routable ? undefined : blocked}
      className="max-w-56"
      onChange={(e) =>
        update.mutate(
          { id: number.id, body: { inbound_agent_id: e.target.value || null } },
          {
            onSuccess: (saved) => {
              toast.success(saved.inbound_agent_id ? `${saved.e164} routed` : `${saved.e164} no longer routed`);
              for (const warning of saved.warnings ?? []) toast.warning(warning);
            },
            onError: (err) => toast.error(errorMessage(err)),
          },
        )
      }
    >
      <option value="">Nobody</option>
      {listed.map((agent) => (
        <option key={agent.id} value={agent.id}>
          {agent.name}
        </option>
      ))}
    </NativeSelect>
  );
}

function DeleteNumberButton({ number }: { number: PhoneNumberOut }) {
  const remove = useDeleteNumber();
  const { canWrite } = useWriteAccess("admin");
  const [confirming, setConfirming] = React.useState(false);
  const hosted = isHostedNumber(number);
  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        aria-label={`Delete ${number.e164}`}
        disabled={!canWrite || remove.isPending}
        title={canWrite ? undefined : writeAccessReason("admin")}
        onClick={() => setConfirming(true)}
      >
        <Icon as={Trash2Icon} size="sm" />
      </Button>
      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Remove {number.e164}?</DialogTitle>
            <DialogDescription>
              {hosted
                ? "It is detached from its agent and forgotten here. The number stays in your LiveKit project; manage or give it up in the LiveKit dashboard."
                : "Its dispatch rule is deleted and it stops reaching an agent. The trunk keeps the number in its list."}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90 dark:bg-destructive dark:hover:bg-destructive/90"
              disabled={remove.isPending}
              onClick={() =>
                remove.mutate(number.id, {
                  onSuccess: () => {
                    toast.success(`${number.e164} removed`);
                    setConfirming(false);
                  },
                  onError: (err) => toast.error(errorMessage(err)),
                })
              }
            >
              Remove number
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
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
            <Field label="Number" htmlFor="number-e164" required>
              <Input id="number-e164" value={e164} placeholder="+15551234567" onChange={(e) => setE164(e.target.value)} />
            </Field>
            <Field label="Trunk" htmlFor="number-trunk" optional>
              <NativeSelect id="number-trunk" value={trunkId} onChange={(e) => setTrunkId(e.target.value)}>
                <option value="">None</option>
                {trunks.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name} ({t.direction})
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <Field label="Inbound agent" htmlFor="number-agent" optional hint="Needs an inbound trunk">
              <NativeSelect
                id="number-agent"
                value={agentId}
                disabled={trunk?.direction !== "inbound"}
                onChange={(e) => setAgentId(e.target.value)}
              >
                <option value="">Nobody</option>
                {agents.map((agent) => (
                  <option key={agent.id} value={agent.id}>
                    {agent.name}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <Field label="Label" htmlFor="number-label" optional>
              <Input id="number-label" value={label} onChange={(e) => setLabel(e.target.value)} />
            </Field>
            {error ? (
              <p role="alert" className="text-sm text-danger-text">
                {error}
              </p>
            ) : null}
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={create.isPending || !e164.trim()}>
              Add number
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
