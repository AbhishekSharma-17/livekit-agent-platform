"use client";

import * as React from "react";
import { toast } from "sonner";
import { MoreHorizontalIcon, PlusIcon, RefreshCwIcon, ServerIcon } from "lucide-react";

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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, Field, Icon, ResponsiveTable, Section, StatusChip } from "@/components/shared";
import type { ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";
import type { ConnectionOut, TrunkOut } from "@/contracts/lkap-contracts";

import { useCreateTrunk, useDeleteTrunk, useSyncTrunk, useTrunks } from "./hooks";
import { E164_PATTERN, type ProviderHint, sipEnabled, splitNumbers, type TrunkDirection } from "./model";
import { NativeSelect } from "./native-select";
const PROVIDER_LABEL: Record<ProviderHint, string> = { twilio: "Twilio", telnyx: "Telnyx", other: "Other" };

/**
 * Trunks (V2-17): the carrier side of every call. Inbound trunks accept calls
 * to their numbers; outbound trunks dial through the carrier's SIP address.
 * Every row mirrors a LiveKit trunk (`lk_trunk_id`); "Re-sync" re-creates it.
 */
export function TrunksSection({ connections }: { connections: ConnectionOut[] }) {
  const { data, isLoading, isError, error, refetch } = useTrunks();
  const [creating, setCreating] = React.useState(false);
  const trunks = data?.items ?? [];
  const connectionName = (id: string) => connections.find((c) => c.id === id)?.name ?? id.slice(0, 8);

  const columns: ResponsiveTableColumn<TrunkOut>[] = [
    {
      id: "name",
      header: "Name",
      cell: (trunk) => (
        <div className="min-w-0">
          <p className="truncate font-medium">{trunk.name}</p>
          <p className="truncate text-xs text-muted-foreground">
            {PROVIDER_LABEL[trunk.provider_hint ?? "other"]} · {connectionName(trunk.connection_id)}
          </p>
        </div>
      ),
    },
    {
      id: "direction",
      header: "Direction",
      cell: (trunk) => (
        <StatusChip tone={trunk.direction === "inbound" ? "info" : "neutral"} size="sm">
          {trunk.direction === "inbound" ? "Inbound" : "Outbound"}
        </StatusChip>
      ),
    },
    {
      id: "numbers",
      header: "Numbers",
      cell: (trunk) => (
        <span className="font-mono text-[0.8125rem] text-muted-foreground">
          {trunk.numbers?.length ? trunk.numbers.join(", ") : "Any"}
        </span>
      ),
    },
    {
      id: "livekit",
      header: "LiveKit",
      cell: (trunk) =>
        trunk.lk_trunk_id ? (
          <span className="font-mono text-xs text-muted-foreground">{trunk.lk_trunk_id}</span>
        ) : (
          <StatusChip tone="warning" size="sm">
            Not synced
          </StatusChip>
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

  // Telephony config needs `admin` server-side (`auth/roles.py::ROUTE_POLICY`).
  const { canWrite } = useWriteAccess("admin");
  const canCreate = canWrite && connections.some(sipEnabled);

  return (
    <Section
      id="trunks"
      title="SIP trunks"
      description="Connect a carrier (Twilio, Telnyx, any SIP provider) to a LiveKit connection."
      aside={
        <Button
          type="button"
          size="sm"
          onClick={() => setCreating(true)}
          disabled={!canCreate}
          title={canWrite ? undefined : writeAccessReason("admin")}
        >
          <Icon as={PlusIcon} size="sm" />
          Add trunk
        </Button>
      }
    >
      {isLoading ? (
        <div className="p-5">
          <Skeleton className="h-10 w-full" />
        </div>
      ) : isError ? (
        <div className="p-5">
          <ErrorBanner message={`Couldn't load trunks: ${errorMessage(error)}`} onRetry={() => refetch()} />
        </div>
      ) : trunks.length === 0 ? (
        <EmptyState
          compact
          icon={ServerIcon}
          title="No trunks yet"
          description={
            canCreate
              ? "Add an inbound trunk to receive calls, or an outbound trunk to place them."
              : "No connection has SIP enabled. Test a connection first; self-hosted servers need the LiveKit SIP service."
          }
          className="p-5"
        />
      ) : (
        <ResponsiveTable<TrunkOut>
          columns={columns}
          rows={trunks}
          label="SIP trunks"
          getRowKey={(trunk) => trunk.id}
          renderCard={(trunk) => (
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="font-medium">{trunk.name}</p>
                <p className="font-mono text-xs text-muted-foreground">{(trunk.numbers ?? []).join(", ") || "Any number"}</p>
              </div>
              <TrunkRowMenu trunk={trunk} />
            </div>
          )}
        />
      )}
      <TrunkDialog open={creating} onOpenChange={setCreating} connections={connections} />
    </Section>
  );
}

function TrunkRowMenu({ trunk }: { trunk: TrunkOut }) {
  const sync = useSyncTrunk();
  const remove = useDeleteTrunk();
  const [confirming, setConfirming] = React.useState(false);
  const { canWrite } = useWriteAccess("admin");

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button type="button" variant="ghost" size="icon" aria-label={`Actions for ${trunk.name}`}>
            <Icon as={MoreHorizontalIcon} size="md" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem
            disabled={!canWrite}
            onSelect={() =>
              sync.mutate(trunk.id, {
                onSuccess: () => toast.success(`${trunk.name} re-created on LiveKit`),
                onError: (err) => toast.error(errorMessage(err)),
              })
            }
          >
            <Icon as={RefreshCwIcon} size="sm" />
            Re-sync to LiveKit
          </DropdownMenuItem>
          <DropdownMenuItem variant="destructive" disabled={!canWrite} onSelect={() => setConfirming(true)}>
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete {trunk.name}?</DialogTitle>
            <DialogDescription>
              The trunk and its dispatch rules are removed from LiveKit. Numbers on it stop receiving calls.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={remove.isPending}
              onClick={() =>
                remove.mutate(trunk.id, {
                  onSuccess: () => {
                    setConfirming(false);
                    toast.success("Trunk deleted");
                  },
                  onError: (err) => toast.error(errorMessage(err)),
                })
              }
            >
              Delete trunk
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
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
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Direction" htmlFor="trunk-direction">
                <NativeSelect
                  id="trunk-direction"
                  value={direction}
                  onChange={(e) => setDirection(e.target.value as TrunkDirection)}
                >
                  <option value="inbound">Inbound (receive calls)</option>
                  <option value="outbound">Outbound (place calls)</option>
                </NativeSelect>
              </Field>
              <Field label="Connection" htmlFor="trunk-connection">
                <NativeSelect
                  id="trunk-connection"
                  value={chosenConnection}
                  onChange={(e) => setConnectionId(e.target.value)}
                >
                  {sipConnections.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </NativeSelect>
              </Field>
              <Field label="Name" htmlFor="trunk-name" required>
                <Input id="trunk-name" value={name} onChange={(e) => setName(e.target.value)} required />
              </Field>
              <Field label="Carrier" htmlFor="trunk-provider">
                <NativeSelect
                  id="trunk-provider"
                  value={provider}
                  onChange={(e) => setProvider(e.target.value as ProviderHint)}
                >
                  <option value="twilio">Twilio</option>
                  <option value="telnyx">Telnyx</option>
                  <option value="other">Other</option>
                </NativeSelect>
              </Field>
            </div>
            <Field
              label="Numbers"
              htmlFor="trunk-numbers"
              hint="E.164, comma separated"
              required={direction === "outbound"}
              optional={direction === "inbound"}
            >
              <Input
                id="trunk-numbers"
                value={numbers}
                placeholder="+15551234567"
                onChange={(e) => setNumbers(e.target.value)}
              />
            </Field>
            <Field
              label={direction === "outbound" ? "SIP address" : "Allowed source address"}
              htmlFor="trunk-address"
              hint={direction === "outbound" ? "e.g. example.pstn.twilio.com" : "IP, CIDR or host; empty accepts any"}
              required={direction === "outbound"}
              optional={direction === "inbound"}
            >
              <Input id="trunk-address" value={address} onChange={(e) => setAddress(e.target.value)} />
            </Field>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Username" htmlFor="trunk-username" optional>
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
            <Button type="submit" disabled={create.isPending || !name.trim() || !chosenConnection}>
              Create trunk
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
