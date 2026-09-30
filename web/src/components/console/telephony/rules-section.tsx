"use client";

import * as React from "react";
import { toast } from "sonner";
import { PlusIcon, SplitIcon, Trash2Icon } from "lucide-react";

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
import { EmptyState } from "@/components/shared/empty-state";
import { Field, FieldRow, FormError } from "@/components/shared/field";
import { Highlight, ListNoMatches, ListSearchField, useListSearch } from "@/components/shared/list-search";
import { SkeletonRows } from "@/components/shared/loading-state";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { Section, SectionRow } from "@/components/shared/section";
import { Tag } from "@/components/shared/tag";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { IfCan, useCan } from "@/components/console/shared/permission";
import type { AgentOut, DispatchRuleOut } from "@/contracts/lkap-contracts";

import { useCreateDispatchRule, useDeleteDispatchRule, useDispatchRules, useTrunks } from "./hooks";
import { E164_PATTERN, splitNumbers } from "./model";

function sourceLabel(rule: DispatchRuleOut): string {
  return rule.managed_by_number ? "From number" : rule.has_pin ? "Manual · PIN" : "Manual";
}

/**
 * Dispatch rules (V2-17): LiveKit's inbound routing. Each call gets its own
 * room (`<prefix>_<caller>_<random>`) and the agent's worker is dispatched with
 * `channel=sip_in`. Rules a number owns are listed but managed from Numbers;
 * a LiveKit-hosted number's rule has no trunk (V4-05).
 */
export function RulesSection({ agents }: { agents: AgentOut[] }) {
  const { data, isLoading, isError, error, refetch } = useDispatchRules();
  const trunksData = useTrunks().data;
  const trunks = React.useMemo(() => trunksData?.items ?? [], [trunksData]);
  const [creating, setCreating] = React.useState(false);
  const rules = React.useMemo(() => data?.items ?? [], [data]);
  const agentName = (id: string) => agents.find((a) => a.id === id)?.name ?? "Unknown agent";
  // A LiveKit-hosted number's rule has no trunk (V4-05): name the number instead.
  const trunkLabel = (rule: DispatchRuleOut) =>
    rule.trunk_id
      ? (trunks.find((t) => t.id === rule.trunk_id)?.name ?? "—")
      : rule.managed_by_number
        ? `LiveKit number ${rule.managed_by_number}`
        : "LiveKit number";
  const inboundTrunks = trunks.filter((t) => t.direction === "inbound");
  const search = useListSearch("telephony-rules", rules, (rule) => [
    agentName(rule.agent_id),
    trunkLabel(rule),
    sourceLabel(rule),
    ...(rule.numbers ?? []),
  ]);
  const query = search.query;

  const columns: ResponsiveTableColumn<DispatchRuleOut>[] = [
    {
      id: "agent",
      header: "Agent",
      cell: (rule) => (
        <span className="font-medium text-foreground">
          <Highlight text={agentName(rule.agent_id)} query={query} />
        </span>
      ),
    },
    {
      id: "trunk",
      header: "Trunk",
      cell: (rule) => (
        <span className="text-label text-text-secondary">
          <Highlight text={trunkLabel(rule)} query={query} />
        </span>
      ),
    },
    {
      id: "numbers",
      header: "Called numbers",
      cell: (rule) => (
        <span className="font-mono text-label text-text-secondary tabular-nums">
          {rule.numbers?.length ? <Highlight text={rule.numbers.join(", ")} query={query} /> : "Every number on the trunk"}
        </span>
      ),
    },
    {
      id: "kind",
      header: "Source",
      cell: (rule) => <Tag>{sourceLabel(rule)}</Tag>,
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
        // Telephony config needs `admin` server-side (`auth/roles.py::ROUTE_POLICY`).
        <IfCan min="admin">
          <Button
            type="button"
            size="sm"
            variant="secondary"
            disabled={inboundTrunks.length === 0}
            onClick={() => setCreating(true)}
          >
            <PlusIcon aria-hidden="true" />
            Add rule
          </Button>
        </IfCan>
      }
    >
      {isLoading ? (
        <SectionRow>
          <SkeletonRows label="Loading dispatch rules" rows={2} rowClassName="h-12" />
        </SectionRow>
      ) : isError ? (
        <SectionRow>
          <ErrorBanner error={error} context={{ action: "load dispatch rules" }} onRetry={() => void refetch()} />
        </SectionRow>
      ) : rules.length === 0 ? (
        <SectionRow>
          <EmptyState
            variant="plain"
            icon={SplitIcon}
            title="No dispatch rules"
            description={
              inboundTrunks.length === 0
                ? "Routing a number to an agent creates one; a catch-all rule needs an inbound trunk first."
                : "Routing a number to an agent creates one; add a catch-all rule here."
            }
          />
        </SectionRow>
      ) : (
        <>
          {search.showSearch ? (
            <SectionRow>
              <ListSearchField search={search} label="Search dispatch rules" total={rules.length} className="mb-0" />
            </SectionRow>
          ) : null}
          {search.noMatches ? (
            <SectionRow>
              <ListNoMatches search={search} items="dispatch rules" />
            </SectionRow>
          ) : (
            <ResponsiveTable<DispatchRuleOut>
              columns={columns}
              rows={search.filtered}
              label="Dispatch rules"
              getRowKey={(rule) => rule.id}
              renderCard={(rule) => (
                <div className="flex items-start justify-between gap-2">
                  <div className="flex min-w-0 flex-col gap-1">
                    <p className="font-medium text-foreground">
                      <Highlight text={agentName(rule.agent_id)} query={query} />
                    </p>
                    <p className="text-caption text-text-secondary">{trunkLabel(rule)}</p>
                    <p className="font-mono text-caption break-all text-text-secondary tabular-nums">
                      {(rule.numbers ?? []).join(", ") || "Every number"}
                    </p>
                  </div>
                  <DeleteRuleButton rule={rule} />
                </div>
              )}
            />
          )}
        </>
      )}
      <RuleDialog open={creating} onOpenChange={setCreating} agents={agents} />
    </Section>
  );
}

function DeleteRuleButton({ rule }: { rule: DispatchRuleOut }) {
  const remove = useDeleteDispatchRule();
  const [confirming, setConfirming] = React.useState(false);
  const { can } = useCan("admin");
  if (!can) return null;
  const number = rule.managed_by_number;
  const label = number ? `Stop routing ${number}` : "Delete rule";
  return (
    <>
      <IconButton label={label} disabled={remove.isPending} onClick={() => setConfirming(true)}>
        <Trash2Icon />
      </IconButton>
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={number ? `Stop routing ${number}?` : "Delete this dispatch rule?"}
        description={
          number
            ? "Its dispatch rule is deleted in LiveKit and calls to the number stop reaching an agent."
            : "The rule is deleted in LiveKit. Calls it matched stop reaching an agent unless another rule matches them."
        }
        confirmLabel={number ? "Stop routing" : "Delete rule"}
        onConfirm={async () => {
          await remove.mutateAsync(rule.id);
          toast.success("Dispatch rule deleted");
        }}
      />
    </>
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
            <Field label="Inbound trunk" htmlFor="rule-trunk">
              <SimpleSelect
                id="rule-trunk"
                value={chosenTrunk}
                onValueChange={setTrunkId}
                options={trunks.map((t) => ({ value: t.id, label: t.name }))}
              />
            </Field>
            <Field label="Agent" htmlFor="rule-agent">
              <SimpleSelect
                id="rule-agent"
                value={chosenAgent}
                onValueChange={setAgentId}
                options={agents.map((agent) => ({ value: agent.id, label: agent.name }))}
              />
            </Field>
            <Field label="Called numbers" htmlFor="rule-numbers" optional hint="Empty = every number on the trunk">
              <Input
                id="rule-numbers"
                value={numbers}
                inputMode="tel"
                className="font-mono tabular-nums"
                onChange={(e) => setNumbers(e.target.value)}
              />
            </Field>
            <FieldRow>
              <Field label="Room prefix" htmlFor="rule-prefix">
                <Input id="rule-prefix" value={prefix} onChange={(e) => setPrefix(e.target.value)} />
              </Field>
              <Field label="PIN" htmlFor="rule-pin" optional hint="Callers must enter it first">
                <Input
                  id="rule-pin"
                  inputMode="numeric"
                  className="tabular-nums"
                  value={pin}
                  onChange={(e) => setPin(e.target.value)}
                />
              </Field>
            </FieldRow>
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
              busyLabel={busyLabelFor("Create rule")}
              disabled={!chosenTrunk || !chosenAgent}
            >
              Create rule
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
