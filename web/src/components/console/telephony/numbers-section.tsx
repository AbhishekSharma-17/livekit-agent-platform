"use client";

import * as React from "react";
import { toast } from "sonner";
import { HashIcon, PlusIcon, Trash2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, Field, Icon, ResponsiveTable, Section, StatusChip } from "@/components/shared";
import type { ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import type { AgentOut, PhoneNumberOut, TrunkOut } from "@/contracts/lkap-contracts";

import { useCreateNumber, useDeleteNumber, usePhoneNumbers, useTrunks, useUpdateNumber } from "./hooks";
import { E164_PATTERN, normalizeE164 } from "./model";
import { NativeSelect } from "./native-select";
/**
 * Numbers (V2-17): the number → agent map. Picking an inbound agent creates
 * (or replaces) the number's own dispatch rule on LiveKit; "Nobody" removes it.
 */
export function NumbersSection({ agents }: { agents: AgentOut[] }) {
  const { data, isLoading, isError, error, refetch } = usePhoneNumbers();
  const trunksQuery = useTrunks();
  const [creating, setCreating] = React.useState(false);
  const numbers = data?.items ?? [];
  const trunks = trunksQuery.data?.items ?? [];

  const columns: ResponsiveTableColumn<PhoneNumberOut>[] = [
    {
      id: "number",
      header: "Number",
      cell: (n) => (
        <div className="min-w-0">
          <p className="font-mono text-sm font-medium">{n.e164}</p>
          {n.label ? <p className="truncate text-xs text-muted-foreground">{n.label}</p> : null}
        </div>
      ),
    },
    {
      id: "trunk",
      header: "Trunk",
      cell: (n) => (
        <span className="text-sm text-muted-foreground">{trunks.find((t) => t.id === n.trunk_id)?.name ?? "—"}</span>
      ),
    },
    {
      id: "agent",
      header: "Inbound agent",
      interactive: true,
      cell: (n) => <InboundAgentPicker number={n} agents={agents} trunks={trunks} />,
    },
    {
      id: "routing",
      header: "Routing",
      cell: (n) =>
        n.dispatch_rule_id ? (
          <StatusChip tone="success" size="sm" dot>
            Routed
          </StatusChip>
        ) : (
          <StatusChip tone="neutral" size="sm">
            Not routed
          </StatusChip>
        ),
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
      description="Which agent answers each number."
      aside={
        <Button type="button" size="sm" variant="outline" onClick={() => setCreating(true)}>
          <Icon as={PlusIcon} size="sm" />
          Add number
        </Button>
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
        <EmptyState
          compact
          icon={HashIcon}
          title="No numbers yet"
          description="Add the numbers your carrier delivers to an inbound trunk, then pick the agent that answers."
          className="p-5"
        />
      ) : (
        <ResponsiveTable<PhoneNumberOut>
          columns={columns}
          rows={numbers}
          label="Phone numbers"
          getRowKey={(n) => n.id}
          renderCard={(n) => (
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-sm font-medium">{n.e164}</span>
                <DeleteNumberButton number={n} />
              </div>
              <InboundAgentPicker number={n} agents={agents} trunks={trunks} />
            </div>
          )}
        />
      )}
      <NumberDialog open={creating} onOpenChange={setCreating} agents={agents} trunks={trunks} />
    </Section>
  );
}

export function InboundAgentPicker({
  number,
  agents,
  trunks,
}: {
  number: PhoneNumberOut;
  agents: AgentOut[];
  trunks: TrunkOut[];
}) {
  const update = useUpdateNumber();
  const trunk = trunks.find((t) => t.id === number.trunk_id);
  const inbound = trunk?.direction === "inbound";
  return (
    <NativeSelect
      aria-label={`Inbound agent for ${number.e164}`}
      value={number.inbound_agent_id ?? ""}
      disabled={!inbound || update.isPending}
      title={inbound ? undefined : "Bind the number to an inbound trunk to route its calls"}
      className="max-w-56"
      onChange={(e) =>
        update.mutate(
          { id: number.id, body: { inbound_agent_id: e.target.value || null } },
          {
            onSuccess: (saved) =>
              toast.success(saved.inbound_agent_id ? `${saved.e164} routed` : `${saved.e164} no longer routed`),
            onError: (err) => toast.error(errorMessage(err)),
          },
        )
      }
    >
      <option value="">Nobody</option>
      {agents.map((agent) => (
        <option key={agent.id} value={agent.id}>
          {agent.name}
        </option>
      ))}
    </NativeSelect>
  );
}

function DeleteNumberButton({ number }: { number: PhoneNumberOut }) {
  const remove = useDeleteNumber();
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      aria-label={`Delete ${number.e164}`}
      disabled={remove.isPending}
      onClick={() =>
        remove.mutate(number.id, {
          onSuccess: () => toast.success(`${number.e164} removed`),
          onError: (err) => toast.error(errorMessage(err)),
        })
      }
    >
      <Icon as={Trash2Icon} size="sm" />
    </Button>
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
      <DialogContent>
        <form onSubmit={submit} className="flex flex-col gap-4">
          <DialogHeader>
            <DialogTitle>Add phone number</DialogTitle>
            <DialogDescription>A number you own at your carrier. It is added to the trunk you pick.</DialogDescription>
          </DialogHeader>
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
