"use client";

import * as React from "react";
import { toast } from "sonner";
import { RefreshCwIcon, ServerIcon, Trash2Icon } from "lucide-react";

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
import { SimpleSelect } from "@/components/ui/select";
import { EmptyState } from "@/components/shared/empty-state";
import { Field, FieldRow, FormError } from "@/components/shared/field";
import { Highlight, ListNoMatches, ListSearchField, useListSearch } from "@/components/shared/list-search";
import { SkeletonRows } from "@/components/shared/loading-state";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { RowMenu } from "@/components/shared/row-menu";
import { Section, SectionRow } from "@/components/shared/section";
import { StatusPill } from "@/components/shared/status-chip";
import { lifecycleStatus } from "@/components/shared/status-map";
import { Tag } from "@/components/shared/tag";
import { VendorMark } from "@/components/shared/vendor-mark";
import { busyLabelFor } from "@/components/shared/busy-label";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { useCan } from "@/components/console/shared/permission";
import type { ConnectionOut, TrunkOut } from "@/contracts/lkap-contracts";

import { useCreateTrunk, useDeleteTrunk, useSyncTrunk, useTrunks } from "./hooks";
import { E164_PATTERN, type ProviderHint, sipEnabled, splitNumbers, type TrunkDirection } from "./model";

const PROVIDER_LABEL: Record<ProviderHint, string> = { twilio: "Twilio", telnyx: "Telnyx", other: "Other" };

/** The carrier's mark beside its name (decorative); any other SIP provider gets a plain server tile. */
function CarrierMark({ hint }: { hint: ProviderHint }) {
  if (hint !== "other") return <VendorMark vendor={PROVIDER_LABEL[hint]} className="mt-0.5" />;
  return (
    <span aria-hidden="true" className="mt-0.5 inline-flex size-6 shrink-0 items-center justify-center rounded-sm bg-muted-strong text-text-secondary">
      <ServerIcon className="size-3.5" />
    </span>
  );
}

function directionLabel(trunk: TrunkOut): string {
  return trunk.direction === "inbound" ? "Inbound" : "Outbound";
}

/**
 * Trunks (V2-17): the carrier side of every call. Inbound trunks accept calls
 * to their numbers; outbound trunks dial through the carrier's SIP address.
 * Every row mirrors a LiveKit trunk (`lk_trunk_id`); "Re-sync" re-creates it.
 * "Add trunk" is the page header's primary action (`TelephonyPage`).
 */
export function TrunksSection({ connections }: { connections: ConnectionOut[] }) {
  const { data, isLoading, isError, error, refetch } = useTrunks();
  const trunks = React.useMemo(() => data?.items ?? [], [data]);
  const connectionName = (id: string) => connections.find((c) => c.id === id)?.name ?? "Unknown connection";
  const search = useListSearch("telephony-trunks", trunks, (trunk) => [
    trunk.name,
    PROVIDER_LABEL[trunk.provider_hint ?? "other"],
    connectionName(trunk.connection_id),
    directionLabel(trunk),
    ...(trunk.numbers ?? []),
  ]);
  const query = search.query;

  const columns: ResponsiveTableColumn<TrunkOut>[] = [
    {
      id: "name",
      header: "Name",
      cell: (trunk) => (
        <div className="flex min-w-0 items-start gap-2.5">
          <CarrierMark hint={trunk.provider_hint ?? "other"} />
          <div className="min-w-0">
            <p className="truncate font-medium text-foreground">
              <Highlight text={trunk.name} query={query} />
            </p>
            <p className="truncate text-caption text-text-secondary">
              {PROVIDER_LABEL[trunk.provider_hint ?? "other"]} · {connectionName(trunk.connection_id)}
            </p>
          </div>
        </div>
      ),
    },
    {
      id: "direction",
      header: "Direction",
      cell: (trunk) => <Tag>{directionLabel(trunk)}</Tag>,
    },
    {
      id: "numbers",
      header: "Numbers",
      cell: (trunk) => (
        <span className="font-mono text-label text-text-secondary tabular-nums">
          {trunk.numbers?.length ? <Highlight text={trunk.numbers.join(", ")} query={query} /> : "Any"}
        </span>
      ),
    },
    {
      id: "livekit",
      header: "LiveKit",
      cell: (trunk) =>
        trunk.lk_trunk_id ? (
          <span className="font-mono text-caption text-text-secondary">{trunk.lk_trunk_id}</span>
        ) : (
          <StatusPill tone={lifecycleStatus("needs_review").tone} size="sm">
            Not synced
          </StatusPill>
        ),
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "end",
      interactive: true,
      cell: (trunk) => <TrunkRowMenu trunk={trunk} />,
    },
  ];

  return (
    <Section
      id="trunks"
      title="SIP trunks"
      description="Connect a carrier (Twilio, Telnyx, any SIP provider) to a LiveKit connection."
    >
      {isLoading ? (
        <SectionRow>
          <SkeletonRows label="Loading trunks" rows={2} rowClassName="h-14" />
        </SectionRow>
      ) : isError ? (
        <SectionRow>
          <ErrorBanner error={error} context={{ action: "load trunks" }} onRetry={() => void refetch()} />
        </SectionRow>
      ) : trunks.length === 0 ? (
        <SectionRow>
          <EmptyState
            variant="plain"
            icon={ServerIcon}
            title="No trunks yet"
            description={
              connections.some(sipEnabled)
                ? "Add an inbound trunk to receive calls, or an outbound trunk to place them."
                : "No connection has SIP enabled. Test a connection first; self-hosted servers need the LiveKit SIP service."
            }
          />
        </SectionRow>
      ) : (
        <>
          {search.showSearch ? (
            <SectionRow>
              <ListSearchField search={search} label="Search trunks" total={trunks.length} className="mb-0" />
            </SectionRow>
          ) : null}
          {search.noMatches ? (
            <SectionRow>
              <ListNoMatches search={search} items="trunks" />
            </SectionRow>
          ) : (
            <ResponsiveTable<TrunkOut>
              columns={columns}
              rows={search.filtered}
              label="SIP trunks"
              getRowKey={(trunk) => trunk.id}
              renderCard={(trunk) => (
                <div className="flex items-start justify-between gap-3">
                  <div className="flex min-w-0 flex-col gap-1">
                    <p className="font-medium text-foreground">
                      <Highlight text={trunk.name} query={query} />
                    </p>
                    <p className="font-mono text-caption break-all text-text-secondary tabular-nums">
                      {(trunk.numbers ?? []).join(", ") || "Any number"}
                    </p>
                    <Tag>{directionLabel(trunk)}</Tag>
                  </div>
                  <TrunkRowMenu trunk={trunk} />
                </div>
              )}
            />
          )}
        </>
      )}
    </Section>
  );
}

function TrunkRowMenu({ trunk }: { trunk: TrunkOut }) {
  const sync = useSyncTrunk();
  const remove = useDeleteTrunk();
  const [confirming, setConfirming] = React.useState(false);
  // Row actions a person can't use are not rendered (decision D12).
  const { can } = useCan("admin");
  if (!can) return null;

  return (
    <>
      <RowMenu
        label={`Actions for ${trunk.name}`}
        actions={[
          {
            label: sync.isPending ? "Re-syncing to LiveKit…" : "Re-sync to LiveKit",
            icon: RefreshCwIcon,
            disabled: sync.isPending,
            onSelect: () =>
              sync.mutate(trunk.id, {
                onSuccess: () => toast.success(`${trunk.name} re-created on LiveKit`),
                onError: (err) => toast.error(errorMessage(err)),
              }),
          },
        ]}
        destructive={{ label: "Delete", icon: Trash2Icon, onSelect: () => setConfirming(true) }}
      />
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={`Delete ${trunk.name}?`}
        description="The trunk and its dispatch rules are removed from LiveKit. Numbers on it stop receiving calls."
        confirmLabel="Delete trunk"
        onConfirm={async () => {
          await remove.mutateAsync(trunk.id);
          toast.success("Trunk deleted");
        }}
      />
    </>
  );
}

export function TrunkDialog({
  open,
  onOpenChange,
  connections,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  connections: ConnectionOut[];
}) {
  const create = useCreateTrunk();
  const sipConnections = connections.filter(sipEnabled);
  const [direction, setDirection] = React.useState<TrunkDirection>("inbound");
  const [connectionId, setConnectionId] = React.useState("");
  const [name, setName] = React.useState("");
  const [numbers, setNumbers] = React.useState("");
  const [provider, setProvider] = React.useState<ProviderHint>("twilio");
  const [address, setAddress] = React.useState("");
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);

  const chosenConnection = connectionId || sipConnections[0]?.id || "";
  const parsed = splitNumbers(numbers);
  const badNumber = parsed.find((n) => !E164_PATTERN.test(n));

  function reset() {
    setName("");
    setNumbers("");
    setAddress("");
    setUsername("");
    setPassword("");
    setError(null);
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (badNumber) {
      setError(`${badNumber} is not an E.164 number (e.g. +15551234567).`);
      return;
    }
    setError(null);
    create.mutate(
      {
        connection_id: chosenConnection || null,
        direction,
        name: name.trim(),
        numbers: parsed,
        provider_hint: provider,
        address: address.trim() || null,
        auth_username: username.trim() || null,
        auth_password: password || null,
      },
      {
        onSuccess: (trunk) => {
          toast.success(`${trunk.name} created on LiveKit`);
          reset();
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
            <DialogTitle>Add SIP trunk</DialogTitle>
            <DialogDescription>
              {direction === "inbound"
                ? "Point your carrier's SIP trunk at the LiveKit project's SIP URI, then list the numbers it delivers."
                : "Calls leave through your carrier's SIP address; the first number is the caller ID."}
            </DialogDescription>
          </DialogHeader>
          <DialogBody className="gap-4">
            <FieldRow>
              <Field label="Direction" htmlFor="trunk-direction">
                <SimpleSelect
                  id="trunk-direction"
                  value={direction}
                  onValueChange={(next) => setDirection(next as TrunkDirection)}
                  options={[
                    { value: "inbound", label: "Inbound (receive calls)" },
                    { value: "outbound", label: "Outbound (place calls)" },
                  ]}
                />
              </Field>
              <Field label="Connection" htmlFor="trunk-connection">
                <SimpleSelect
                  id="trunk-connection"
                  value={chosenConnection}
                  onValueChange={setConnectionId}
                  options={sipConnections.map((c) => ({ value: c.id, label: c.name }))}
                />
              </Field>
              <Field label="Name" htmlFor="trunk-name">
                <Input id="trunk-name" value={name} onChange={(e) => setName(e.target.value)} required />
              </Field>
              <Field label="Carrier" htmlFor="trunk-provider">
                <SimpleSelect
                  id="trunk-provider"
                  value={provider}
                  onValueChange={(next) => setProvider(next as ProviderHint)}
                  options={(Object.keys(PROVIDER_LABEL) as ProviderHint[]).map((hint) => ({
                    value: hint,
                    label: PROVIDER_LABEL[hint],
                  }))}
                />
              </Field>
            </FieldRow>
            <Field label="Numbers" htmlFor="trunk-numbers" hint="E.164, comma separated" optional={direction === "inbound"}>
              <Input
                id="trunk-numbers"
                value={numbers}
                placeholder="+15551234567"
                inputMode="tel"
                className="font-mono tabular-nums"
                onChange={(e) => setNumbers(e.target.value)}
              />
            </Field>
            <Field
              label={direction === "outbound" ? "SIP address" : "Allowed source address"}
              htmlFor="trunk-address"
              hint={
                direction !== "outbound"
                  ? "IP, CIDR or host; empty accepts any"
                  : provider === "telnyx"
                    ? "Telnyx: sip.telnyx.com"
                    : "e.g. example.pstn.twilio.com"
              }
              optional={direction === "inbound"}
            >
              <Input id="trunk-address" value={address} onChange={(e) => setAddress(e.target.value)} />
            </Field>
            <FieldRow>
              <Field
                label="Username"
                htmlFor="trunk-username"
                optional
                hint={
                  direction === "outbound" && provider === "telnyx"
                    ? "Also sent as the X-Telnyx-Username header"
                    : undefined
                }
              >
                <Input
                  id="trunk-username"
                  autoComplete="off"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
              </Field>
              <Field label="Password" htmlFor="trunk-password" optional hint="Stored encrypted; never shown again">
                <Input
                  id="trunk-password"
                  type="password"
                  autoComplete="new-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
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
              busyLabel={busyLabelFor("Create trunk")}
              disabled={!name.trim() || !chosenConnection}
            >
              Create trunk
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
