"use client";

import * as React from "react";
import { useState } from "react";

import { friendlyError } from "@/components/console/lib/friendly-error";
import { busyLabelFor } from "@/components/shared/busy-label";
import { FormError } from "@/components/shared/field";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

export interface ConfirmDialogProps {
  /** The control that opens it. Omit when controlling `open` yourself. */
  trigger?: React.ReactNode;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Names the object: "Delete “Front desk”?". */
  title: string;
  /** Exactly what will happen. */
  description?: React.ReactNode;
  /** Optional list of what will be removed or changed. */
  children?: React.ReactNode;
  /** The verb: "Delete agent". */
  confirmLabel?: string;
  /** Gerund plus ellipsis; defaults from `confirmLabel` ("Deleting agent…"). */
  busyLabel?: string;
  cancelLabel?: string;
  /** Destructive (default): `role="alertdialog"` and the danger button. */
  destructive?: boolean;
  onConfirm: () => void | Promise<void>;
}

/**
 * Confirm dialog (docs/ui/DESIGN-SYSTEM.md sections 6.4 and 9): states
 * exactly what will happen, with Cancel and the verb. Destructive ones use
 * `role="alertdialog"` (outside clicks don't dismiss them) and the solid
 * danger button, which only ever appears inside a confirmation step. While
 * the action runs the button says the gerund ("Deleting…"); if it fails the
 * dialog stays open and says why in plain words.
 */
export function ConfirmDialog(props: ConfirmDialogProps) {
  return <ConfirmDialogImpl {...props} />;
}

export interface TypedConfirmDialogProps extends ConfirmDialogProps {
  /** The text the person must type, e.g. `DELETE` or the workspace name. */
  confirmText: string;
  /** Label for the typing field. Default: Type {confirmText} to confirm. */
  inputLabel?: React.ReactNode;
}

/**
 * Typed confirmation (section 9) for irreversible, data-destroying actions:
 * the danger button stays disabled until the person types `confirmText`.
 * List what will be removed as `children`.
 */
export function TypedConfirmDialog({ confirmText, inputLabel, ...props }: TypedConfirmDialogProps) {
  return <ConfirmDialogImpl {...props} confirmText={confirmText} inputLabel={inputLabel} />;
}

function ConfirmDialogImpl({
  trigger,
  open: openProp,
  onOpenChange,
  title,
  description,
  children,
  confirmLabel = "Confirm",
  busyLabel,
  cancelLabel = "Cancel",
  destructive = true,
  onConfirm,
  confirmText,
  inputLabel,
}: ConfirmDialogProps & { confirmText?: string; inputLabel?: React.ReactNode }) {
  const [openState, setOpenState] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const inputId = React.useId();
  const open = openProp ?? openState;

  const setOpen = (next: boolean) => {
    if (!next) {
      setError(null);
      setTyped("");
    }
    if (openProp === undefined) setOpenState(next);
    onOpenChange?.(next);
  };

  const typedOk = confirmText === undefined || typed.trim() === confirmText;

  return (
    <Dialog open={open} onOpenChange={(next) => (pending ? undefined : setOpen(next))}>
      {trigger ? <DialogTrigger asChild>{trigger}</DialogTrigger> : null}
      <DialogContent
        // Only set when destructive: an explicit role={undefined} would erase Radix's role="dialog".
        {...(destructive ? { role: "alertdialog" } : {})}
        className="sm:w-[min(calc(100vw-32px),440px)]"
        onInteractOutside={destructive ? (event) => event.preventDefault() : undefined}
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description ? <DialogDescription>{description}</DialogDescription> : null}
        </DialogHeader>
        {children || confirmText !== undefined || error ? (
          <div data-slot="confirm-body" className="flex flex-col gap-4 text-label text-text-secondary">
            {children}
            {confirmText !== undefined ? (
              <div className="flex flex-col gap-1.5">
                <label htmlFor={inputId} className="text-label font-medium text-foreground">
                  {inputLabel ?? (
                    <>
                      Type <span className="font-mono font-semibold">{confirmText}</span> to confirm
                    </>
                  )}
                </label>
                <Input
                  id={inputId}
                  value={typed}
                  autoComplete="off"
                  spellCheck={false}
                  onChange={(event) => setTyped(event.target.value)}
                />
              </div>
            ) : null}
            <FormError>{error}</FormError>
          </div>
        ) : null}
        <DialogFooter>
          <Button type="button" variant="secondary" disabled={pending} onClick={() => setOpen(false)}>
            {cancelLabel}
          </Button>
          <Button
            type="button"
            variant={destructive ? "danger" : "primary"}
            disabled={!typedOk}
            busy={pending}
            busyLabel={busyLabel ?? busyLabelFor(confirmLabel)}
            onClick={async () => {
              setPending(true);
              setError(null);
              try {
                await onConfirm();
                setPending(false);
                setOpen(false);
              } catch (err) {
                setPending(false);
                setError(friendlyError(err).message);
              }
            }}
          >
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
