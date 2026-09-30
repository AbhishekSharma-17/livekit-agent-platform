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
import { SimpleSelect } from "@/components/ui/select";
import { Alert } from "@/components/ui/alert";
import { Field, FormError } from "@/components/shared/field";
import { StatusPill } from "@/components/shared/status-chip";
import { errorMessage } from "@/components/console/shared/error-banner";
import { useConnections } from "@/hooks/useConnections";
import type { AgentOut } from "@/contracts/lkap-contracts";

import { CallControls } from "./call-controls";
import { useCall, usePlaceCall, useTrunks } from "./hooks";
import { callStatusMeta, connectionForAgent, E164_PATTERN, normalizeE164, sipEnabled } from "./model";
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
      <PhoneOutgoingIcon aria-hidden="true" />
      Call a number…
      {dirty ? <span className="ml-auto text-caption text-text-tertiary">saved version</span> : null}
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
            <Alert tone="warning">
              {blocker}{" "}
              <Link href="/console/telephony" className="font-medium underline underline-offset-4">
                Open Telephony
              </Link>
            </Alert>
          ) : (
            <>
              <Field label="Phone number" htmlFor="call-to">
                <Input
                  id="call-to"
                  type="tel"
                  value={to}
                  placeholder="+15551234567"
                  className="font-mono tabular-nums"
                  disabled={Boolean(callId)}
                  onChange={(e) => setTo(e.target.value)}
                />
              </Field>
              {outbound.length > 1 ? (
                <Field label="Trunk" htmlFor="call-trunk">
                  <SimpleSelect
                    id="call-trunk"
                    value={chosenTrunk}
                    onValueChange={setTrunkId}
                    options={outbound.map((t) => ({ value: t.id, label: `${t.name} (${t.numbers?.[0] ?? "no caller ID"})` }))}
                  />
                </Field>
              ) : null}
            </>
          )}
          {call && meta ? (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded border border-border bg-muted p-3">
              <div className="flex flex-wrap items-center gap-2" aria-live="polite">
                <StatusPill tone={meta.tone}>{meta.label}</StatusPill>
                {call.hangup_reason && call.status !== "completed" ? (
                  <span className="text-caption text-text-secondary">{call.hangup_reason}</span>
                ) : null}
              </div>
              <CallControls call={call} compact />
            </div>
          ) : null}
          <FormError>{error}</FormError>
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => handleOpenChange(false)}>
              Close
            </Button>
            {callId ? null : (
              <Button
                type="submit"
                variant="primary"
                busy={place.isPending}
                busyLabel="Calling…"
                disabled={Boolean(blocker) || !to.trim()}
              >
                <PhoneOutgoingIcon aria-hidden="true" />
                Call
              </Button>
            )}
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
