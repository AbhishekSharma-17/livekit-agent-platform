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
import { Field, Icon } from "@/components/shared";
import { errorMessage } from "@/components/console/shared/error-banner";
import type { CallOut } from "@/contracts/lkap-contracts";

import { useHangupCall, useSendDtmf, useTransferCall } from "./hooks";
import { isLiveCall, isOpenCall } from "./model";
import { DTMF_PATTERN, TRANSFER_TARGET_PATTERN } from "./types";

const KEYPAD = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "0", "#"];

/**
 * Live-call actions (V2-17): hang up (closes the room), cold transfer (SIP
 * REFER) and keypad (DTMF played by the agent). Transfer and keypad need an
 * answered call; hang up also cancels a call that is still ringing.
 */
export function CallControls({ call, compact = false }: { call: CallOut; compact?: boolean }) {
  const hangup = useHangupCall();
  const [dialog, setDialog] = React.useState<"transfer" | "keypad" | null>(null);
  if (!isOpenCall(call)) return null;
  const live = isLiveCall(call);
  const size = compact ? "icon" : "sm";

  return (
    <div className="flex items-center gap-1.5">
      <Button
        type="button"
        size={size}
        variant="outline"
        disabled={!live}
        aria-label="Keypad"
        title="Send keypad tones"
        onClick={() => setDialog("keypad")}
      >
        <Icon as={GripIcon} size="sm" />
        {compact ? null : "Keypad"}
      </Button>
      <Button
        type="button"
        size={size}
        variant="outline"
        disabled={!live}
        aria-label="Transfer"
        title="Cold transfer"
        onClick={() => setDialog("transfer")}
      >
        <Icon as={PhoneForwardedIcon} size="sm" />
        {compact ? null : "Transfer"}
      </Button>
      <Button
        type="button"
        size={size}
        variant="destructive"
        aria-label="Hang up"
        title="Hang up"
        disabled={hangup.isPending}
        onClick={() =>
          hangup.mutate(
            { id: call.id, body: undefined },
            { onSuccess: () => toast.success("Call ended"), onError: (err) => toast.error(errorMessage(err)) },
          )
        }
      >
        <Icon as={PhoneOffIcon} size="sm" />
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
          <Field label="Transfer to" htmlFor={`transfer-${call.id}`} required>
            <Input
              id={`transfer-${call.id}`}
              value={to}
              placeholder="+15551234567"
              onChange={(e) => setTo(e.target.value)}
            />
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
            <Button type="submit" disabled={transfer.isPending || !to.trim()}>
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
      <DialogContent className="sm:max-w-xs">
        <DialogHeader>
          <DialogTitle>Keypad</DialogTitle>
          <DialogDescription>The agent plays these tones into the call.</DialogDescription>
        </DialogHeader>
        <output aria-live="polite" className="h-9 rounded-sm border border-border px-3 py-1.5 font-mono text-lg tracking-widest">
          {digits || " "}
        </output>
        <div className="grid grid-cols-3 gap-2" role="group" aria-label="Keys">
          {KEYPAD.map((key) => (
            <Button
              key={key}
              type="button"
              variant="outline"
              className="h-11 font-mono text-lg"
              onClick={() => setDigits((d) => (d.length < 32 ? d + key : d))}
            >
              {key}
            </Button>
          ))}
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => setDigits("")} disabled={!digits}>
            Clear
          </Button>
          <Button type="button" onClick={send} disabled={!digits || dtmf.isPending}>
            Send
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
