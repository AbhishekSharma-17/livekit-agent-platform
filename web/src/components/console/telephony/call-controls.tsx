"use client";

import * as React from "react";
import { toast } from "sonner";
import { GripIcon, PhoneForwardedIcon, PhoneOffIcon } from "lucide-react";

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
import { busyLabelFor } from "@/components/shared/busy-label";
import { Field, FormError } from "@/components/shared/field";
import { errorMessage } from "@/components/console/shared/error-banner";
import { useCan } from "@/components/console/shared/permission";
import type { CallOut } from "@/contracts/lkap-contracts";

import { useHangupCall, useSendDtmf, useTransferCall } from "./hooks";
import { DTMF_PATTERN, isLiveCall, isOpenCall, TRANSFER_TARGET_PATTERN } from "./model";

const KEYPAD = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "0", "#"];

/**
 * Live-call actions (V2-17): hang up (closes the room), cold transfer (SIP
 * REFER) and keypad (DTMF played by the agent). Transfer and keypad need an
 * answered call; hang up also cancels a call that is still ringing. `/v1/calls`
 * writes need `builder` (`auth/roles.py::ROUTE_POLICY`); a viewer sees the
 * call's status but no controls (decision D12).
 *
 * Hang up stays one click: it ends a live call, and a confirmation would keep
 * the caller on the line while the person reads it.
 */
export function CallControls({ call, compact = false }: { call: CallOut; compact?: boolean }) {
  const hangup = useHangupCall();
  const [dialog, setDialog] = React.useState<"transfer" | "keypad" | null>(null);
  const { can } = useCan("builder");
  if (!isOpenCall(call) || !can) return null;
  const live = isLiveCall(call);
  const size = compact ? "icon-md" : "sm";

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Button
        type="button"
        size={size}
        variant="secondary"
        disabled={!live}
        aria-label="Keypad"
        title="Send keypad tones"
        onClick={() => setDialog("keypad")}
      >
        <GripIcon aria-hidden="true" />
        {compact ? null : "Keypad"}
      </Button>
      <Button
        type="button"
        size={size}
        variant="secondary"
        disabled={!live}
        aria-label="Transfer"
        title="Cold transfer"
        onClick={() => setDialog("transfer")}
      >
        <PhoneForwardedIcon aria-hidden="true" />
        {compact ? null : "Transfer"}
      </Button>
      <Button
        type="button"
        size={size}
        variant="danger-outline"
        aria-label="Hang up"
        title="Hang up"
        busy={hangup.isPending}
        busyLabel="Hanging up…"
        onClick={() =>
          hangup.mutate(
            { id: call.id, body: undefined },
            { onSuccess: () => toast.success("Call ended"), onError: (err) => toast.error(errorMessage(err)) },
          )
        }
      >
        <PhoneOffIcon aria-hidden="true" />
        {compact ? null : "Hang up"}
      </Button>
      <TransferDialog call={call} open={dialog === "transfer"} onOpenChange={(open) => setDialog(open ? "transfer" : null)} />
      <KeypadDialog call={call} open={dialog === "keypad"} onOpenChange={(open) => setDialog(open ? "keypad" : null)} />
    </div>
  );
}

function TransferDialog({
  call,
  open,
  onOpenChange,
}: {
  call: CallOut;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const transfer = useTransferCall();
  const [to, setTo] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const target = to.trim();
    if (!TRANSFER_TARGET_PATTERN.test(target)) {
      setError("Use an E.164 number (+15551234567) or a tel:/sip: address.");
      return;
    }
    setError(null);
    transfer.mutate(
      { id: call.id, body: { to: target } },
      {
        onSuccess: () => {
          toast.success(`Transferred to ${target}`);
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
            <DialogTitle>Transfer call</DialogTitle>
            <DialogDescription>
              The caller is handed to the destination and the agent leaves. The trunk must allow SIP transfers.
            </DialogDescription>
          </DialogHeader>
          <Field label="Transfer to" htmlFor={`transfer-${call.id}`}>
            <Input
              id={`transfer-${call.id}`}
              value={to}
              placeholder="+15551234567"
              inputMode="tel"
              className="font-mono tabular-nums"
              onChange={(e) => setTo(e.target.value)}
            />
          </Field>
          <FormError>{error}</FormError>
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              busy={transfer.isPending}
              busyLabel={busyLabelFor("Transfer")}
              disabled={!to.trim()}
            >
              Transfer
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function KeypadDialog({
  call,
  open,
  onOpenChange,
}: {
  call: CallOut;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const dtmf = useSendDtmf();
  const [digits, setDigits] = React.useState("");

  function send() {
    if (!DTMF_PATTERN.test(digits)) return;
    dtmf.mutate(
      { id: call.id, body: { digits } },
      {
        onSuccess: () => {
          toast.success(`Sent ${digits}`);
          setDigits("");
        },
        onError: (err) => toast.error(errorMessage(err)),
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Keypad</DialogTitle>
          <DialogDescription>The agent plays these tones into the call.</DialogDescription>
        </DialogHeader>
        <output
          aria-live="polite"
          className="flex h-9 items-center rounded border border-border bg-card px-3 font-mono text-title tracking-widest tabular-nums"
        >
          {digits || " "}
        </output>
        <div className="grid grid-cols-3 gap-2" role="group" aria-label="Keys">
          {KEYPAD.map((key) => (
            <Button
              key={key}
              type="button"
              size="lg"
              variant="secondary"
              className="font-mono text-title"
              onClick={() => setDigits((d) => (d.length < 32 ? d + key : d))}
            >
              {key}
            </Button>
          ))}
        </div>
        <DialogFooter>
          <Button type="button" variant="secondary" onClick={() => setDigits("")} disabled={!digits}>
            Clear
          </Button>
          <Button type="button" variant="primary" onClick={send} busy={dtmf.isPending} busyLabel="Sending…" disabled={!digits}>
            Send
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
