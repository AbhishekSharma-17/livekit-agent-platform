"use client";

import * as React from "react";
import Link from "next/link";
import { PhoneOutgoingIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Field, Icon, StatusChip } from "@/components/shared";
import { errorMessage } from "@/components/console/shared/error-banner";
import { useConnections } from "@/hooks/useConnections";
import type { AgentOut } from "@/contracts/lkap-contracts";

import { CallControls } from "./call-controls";
import { useCall, usePlaceCall, useTrunks } from "./hooks";
import { callStatusMeta, connectionForAgent, E164_PATTERN, normalizeE164, sipEnabled } from "./model";
import { NativeSelect } from "./native-select";
/*
 * "Call a number" on the agent editor's Test call split button (V2-17,
 * editor README "Slots"). The menu item lives inside the dropdown, which
 * unmounts when it closes, so the dialog is mounted separately through the
 * always-rendered `headerActions` slot and the two talk through this tiny
 * store (one editor per page).
 */
type Listener = () => void;
let openAgentId: string | null = null;
const listeners = new Set<Listener>();

function setOpenAgent(id: string | null) {
  openAgentId = id;
  for (const listener of listeners) listener();
}

function subscribe(listener: Listener) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function useOpenAgent(): string | null {
  return React.useSyncExternalStore(
    subscribe,
    () => openAgentId,
    () => null,
  );
}

/** Test call menu item. */
export function CallNumberMenuItem({ agent, dirty }: { agent: AgentOut; dirty: boolean }) {
  return (
    <DropdownMenuItem onSelect={() => setOpenAgent(agent.id)}>
      <Icon as={PhoneOutgoingIcon} size="sm" />
      Call a number…
      {dirty ? <span className="ml-auto text-xs text-muted-foreground">saved version</span> : null}
    </DropdownMenuItem>
  );
}

/** Renders nothing until the menu item opens it. */
export function CallNumberDialogHost({ agent }: { agent: AgentOut }) {
  const open = useOpenAgent() === agent.id;
  return <CallNumberDialog agent={agent} open={open} onOpenChange={(next) => setOpenAgent(next ? agent.id : null)} />;
}

export function CallNumberDialog({
  agent,
  open,
  onOpenChange,
}: {
  agent: AgentOut;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const connections = useConnections().data?.items ?? [];
  const trunks = useTrunks().data?.items ?? [];
  const place = usePlaceCall();
  const [to, setTo] = React.useState("");
  const [trunkId, setTrunkId] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [callId, setCallId] = React.useState<string | null>(null);
  const call = useCall(callId).data;

  const connection = connectionForAgent(agent, connections);
  const outbound = trunks.filter((t) => t.direction === "outbound" && t.connection_id === connection?.id);
  const chosenTrunk = trunkId || outbound[0]?.id || "";
  const blocker = !connection
    ? "The agent has no connection."
    : !sipEnabled(connection)
      ? `SIP is not enabled on ${connection.name}. Test the connection first.`
      : outbound.length === 0
        ? "Its connection has no outbound trunk."
        : null;

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const number = normalizeE164(to);
    if (!E164_PATTERN.test(number)) {
      setError("Enter the number in E.164 form, e.g. +15551234567.");
      return;
    }
    setError(null);
    place.mutate(
      { agent_id: agent.id, to_e164: number, trunk_id: chosenTrunk || null },
      { onSuccess: (placed) => setCallId(placed.id), onError: (err) => setError(errorMessage(err)) },
    );
  }

  function handleOpenChange(next: boolean) {
    if (!next) {
      setCallId(null);
      setError(null);
    }
    onOpenChange(next);
  }

  const meta = call ? callStatusMeta(call.status) : null;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <form onSubmit={submit} className="flex flex-col gap-4">
          <DialogHeader>
            <DialogTitle>Call a number</DialogTitle>
            <DialogDescription>
              {agent.name} calls this number from your outbound trunk, using its saved configuration.
            </DialogDescription>
          </DialogHeader>
          {blocker ? (
            <p role="status" className="text-sm text-muted-foreground">
              {blocker}{" "}
              <Link href="/console/telephony" className="underline underline-offset-4">
                Open Telephony
              </Link>
            </p>
          ) : (
            <>
              <Field label="Phone number" htmlFor="call-to" required>
                <Input
                  id="call-to"
                  type="tel"
                  value={to}
                  placeholder="+15551234567"
                  disabled={Boolean(callId)}
                  onChange={(e) => setTo(e.target.value)}
                />
              </Field>
              {outbound.length > 1 ? (
                <Field label="Trunk" htmlFor="call-trunk">
                  <NativeSelect id="call-trunk" value={chosenTrunk} onChange={(e) => setTrunkId(e.target.value)}>
                    {outbound.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name} ({t.numbers?.[0] ?? "no caller ID"})
                      </option>
                    ))}
                  </NativeSelect>
                </Field>
              ) : null}
            </>
          )}
          {call && meta ? (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border p-3">
              <div className="flex items-center gap-2" aria-live="polite">
                <StatusChip tone={meta.tone} dot>
                  {meta.label}
                </StatusChip>
                {call.hangup_reason && call.status !== "completed" ? (
                  <span className="text-xs text-muted-foreground">{call.hangup_reason}</span>
                ) : null}
              </div>
              <CallControls call={call} compact />
            </div>
          ) : null}
          {error ? (
            <p role="alert" className="text-sm text-danger-text">
              {error}
            </p>
          ) : null}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => handleOpenChange(false)}>
              Close
            </Button>
            {callId ? null : (
              <Button type="submit" disabled={Boolean(blocker) || place.isPending || !to.trim()}>
                <Icon as={PhoneOutgoingIcon} size="sm" />
                Call
              </Button>
            )}
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
