"use client";

import * as React from "react";
import { toast } from "sonner";
import { PlusIcon, SplitIcon, Trash2Icon } from "lucide-react";

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
import { EmptyState, Field, Icon, ResponsiveTable, Section, StatusChip } from "@/components/shared";
import type { ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";
import type { AgentOut, DispatchRuleOut } from "@/contracts/lkap-contracts";

import { useCreateDispatchRule, useDeleteDispatchRule, useDispatchRules, useTrunks } from "./hooks";
import { E164_PATTERN, splitNumbers } from "./model";
import { NativeSelect } from "./native-select";
/**
 * Dispatch rules (V2-17): LiveKit's inbound routing. Each call gets its own
 * room (`<prefix>_<caller>_<random>`) and the agent's worker is dispatched with
 * `channel=sip_in`. Rules a number owns are listed but managed from Numbers.
 */
export function RulesSection({ agents }: { agents: AgentOut[] }) {
  const { data, isLoading, isError, error, refetch } = useDispatchRules();
  const trunks = useTrunks().data?.items ?? [];
  const [creating, setCreating] = React.useState(false);
  const rules = data?.items ?? [];
  const agentName = (id: string) => agents.find((a) => a.id === id)?.name ?? "Unknown agent";
  const inboundTrunks = trunks.filter((t) => t.direction === "inbound");
  // Telephony config needs `admin` server-side (`auth/roles.py::ROUTE_POLICY`).
  const { canWrite } = useWriteAccess("admin");
  const writeReason = writeAccessReason("admin");

  const columns: ResponsiveTableColumn<DispatchRuleOut>[] = [
    {
      id: "agent",
      header: "Agent",
      cell: (rule) => <span className="font-medium">{agentName(rule.agent_id)}</span>,
    },
    {
      id: "trunk",
      header: "Trunk",
      cell: (rule) => (
        <span className="text-sm text-muted-foreground">
          {trunks.find((t) => t.id === rule.trunk_id)?.name ?? "—"}
        </span>
      ),
    },
    {
      id: "numbers",
      header: "Called numbers",
      cell: (rule) => (
        <span className="font-mono text-[0.8125rem] text-muted-foreground">
          {rule.numbers?.length ? rule.numbers.join(", ") : "Every number on the trunk"}
        </span>
      ),
    },
    {
      id: "kind",
      header: "Source",
      cell: (rule) =>
        rule.managed_by_number ? (
          <StatusChip tone="info" size="sm">
            From number
          </StatusChip>
        ) : (
          <StatusChip tone="neutral" size="sm">
            {rule.has_pin ? "Manual · PIN" : "Manual"}
          </StatusChip>
        ),
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "end",
      interactive: true,
      cell: (rule) => <DeleteRuleButton rule={rule} />,
    },
  ];

  return (
    <Section
      id="dispatch-rules"
      title="Dispatch rules"
      description="Route calls on an inbound trunk to an agent. A catch-all rule answers every number on the trunk."
      aside={
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={!canWrite || inboundTrunks.length === 0}
          title={canWrite ? undefined : writeReason}
          onClick={() => setCreating(true)}
        >
          <Icon as={PlusIcon} size="sm" />
          Add rule
        </Button>
      }
    >
      {isLoading ? (
        <div className="p-5">
          <Skeleton className="h-10 w-full" />
        </div>
      ) : isError ? (
        <div className="p-5">
          <ErrorBanner message={`Couldn't load dispatch rules: ${errorMessage(error)}`} onRetry={() => refetch()} />
        </div>
      ) : rules.length === 0 ? (
        <EmptyState
          compact
          icon={SplitIcon}
          title="No dispatch rules"
          description="Routing a number to an agent creates one; add a catch-all rule here."
          className="p-5"
        />
      ) : (
        <ResponsiveTable<DispatchRuleOut>
          columns={columns}
          rows={rules}
          label="Dispatch rules"
          getRowKey={(rule) => rule.id}
          renderCard={(rule) => (
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="font-medium">{agentName(rule.agent_id)}</p>
                <p className="font-mono text-xs text-muted-foreground">{(rule.numbers ?? []).join(", ") || "Every number"}</p>
              </div>
              <DeleteRuleButton rule={rule} />
            </div>
          )}
        />
      )}
      <RuleDialog open={creating} onOpenChange={setCreating} agents={agents} />
    </Section>
  );
}

function DeleteRuleButton({ rule }: { rule: DispatchRuleOut }) {
  const remove = useDeleteDispatchRule();
  const { canWrite } = useWriteAccess("admin");
  const label = rule.managed_by_number ? `Stop routing ${rule.managed_by_number}` : "Delete rule";
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      aria-label={label}
      title={canWrite ? label : writeAccessReason("admin")}
      disabled={!canWrite || remove.isPending}
      onClick={() =>
        remove.mutate(rule.id, {
          onSuccess: () => toast.success("Dispatch rule deleted"),
          onError: (err) => toast.error(errorMessage(err)),
        })
      }
    >
      <Icon as={Trash2Icon} size="sm" />
    </Button>
  );
}

function RuleDialog({
  open,
  onOpenChange,
  agents,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  agents: AgentOut[];
}) {
  const create = useCreateDispatchRule();
  const trunks = (useTrunks().data?.items ?? []).filter((t) => t.direction === "inbound");
  const [trunkId, setTrunkId] = React.useState("");
  const [agentId, setAgentId] = React.useState("");
  const [numbers, setNumbers] = React.useState("");
  const [prefix, setPrefix] = React.useState("call-");
  const [pin, setPin] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const chosenTrunk = trunkId || trunks[0]?.id || "";
  const chosenAgent = agentId || agents[0]?.id || "";

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const parsed = splitNumbers(numbers);
    const bad = parsed.find((n) => !E164_PATTERN.test(n));
    if (bad) {
      setError(`${bad} is not an E.164 number.`);
      return;
    }
    if (pin && !/^[0-9]{4,12}$/.test(pin)) {
      setError("The PIN is 4 to 12 digits.");
      return;
    }
    setError(null);
    create.mutate(
      { trunk_id: chosenTrunk, agent_id: chosenAgent, numbers: parsed, room_prefix: prefix, pin: pin || null },
      {
        onSuccess: () => {
          toast.success("Dispatch rule created on LiveKit");
          setNumbers("");
          setPin("");
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
            <DialogTitle>Add dispatch rule</DialogTitle>
            <DialogDescription>Calls on the trunk (optionally only to some numbers) reach this agent.</DialogDescription>
          </DialogHeader>
          <DialogBody className="gap-4">
            <Field label="Inbound trunk" htmlFor="rule-trunk" required>
              <NativeSelect id="rule-trunk" value={chosenTrunk} onChange={(e) => setTrunkId(e.target.value)}>
                {trunks.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <Field label="Agent" htmlFor="rule-agent" required>
              <NativeSelect id="rule-agent" value={chosenAgent} onChange={(e) => setAgentId(e.target.value)}>
                {agents.map((agent) => (
                  <option key={agent.id} value={agent.id}>
                    {agent.name}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <Field label="Called numbers" htmlFor="rule-numbers" optional hint="Empty = every number on the trunk">
              <Input id="rule-numbers" value={numbers} onChange={(e) => setNumbers(e.target.value)} />
            </Field>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Room prefix" htmlFor="rule-prefix">
                <Input id="rule-prefix" value={prefix} onChange={(e) => setPrefix(e.target.value)} />
              </Field>
              <Field label="PIN" htmlFor="rule-pin" optional hint="Callers must enter it first">
                <Input id="rule-pin" inputMode="numeric" value={pin} onChange={(e) => setPin(e.target.value)} />
              </Field>
            </div>
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
            <Button type="submit" disabled={create.isPending || !chosenTrunk || !chosenAgent}>
              Create rule
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
