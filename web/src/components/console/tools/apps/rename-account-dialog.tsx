"use client";

import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Field } from "@/components/shared/field";
import { useUpdateConnection } from "@/components/console/lib/api-hooks";
import { appsErrorMessage } from "@/components/console/tools/apps/use-composio";
import { ApiError } from "@/lib/api";

/** `ConnectionRenameIn.label` (R-V5-13): "≤ 40" in docs/v5/_asks.md #85. */
const MAX_ACCOUNT_LABEL = 40;

/**
 * `PATCH .../connections/{id}` only ever raises a plain `conflict` (409) for
 * two different reasons (`update_connection`, `api/src/lkap_api/tool_providers/service.py`):
 * a duplicate label, or "not active yet" on a default it refuses. This
 * dialog only ever sends `label`, so a 409 here is always the duplicate —
 * the card's required wording ("Errors: 409 duplicate label →") rather than
 * the api's own message (`another account of this app is already called
 * '<label>'; pick another name`, which is accurate but not the agreed copy).
 */
function renameErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 409) {
    return "Another account of this app already uses that name";
  }
  return appsErrorMessage(error);
}

/**
 * Rename one account (R-V5-13, docs/v5/PLAN-V5.md V5-54 card): a single name
 * field, opened from `ConnectionRow`'s "Rename" button — on the app card
 * directly (one account) or inside its accounts dialog (several).
 */
export function RenameAccountDialog({
  connectionId,
  currentLabel,
  toolkitName,
  trigger,
}: {
  connectionId: string;
  currentLabel: string;
  toolkitName: string;
  trigger: React.ReactNode;
}) {
  const [open, setOpen] = React.useState(false);
  const [label, setLabel] = React.useState(currentLabel);
  const rename = useUpdateConnection();

  function setOpenState(next: boolean) {
    setOpen(next);
    if (next) setLabel(currentLabel);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    event.stopPropagation();
    const trimmed = label.trim();
    if (!trimmed || trimmed === currentLabel) {
      setOpen(false);
      return;
    }
    try {
      await rename.mutateAsync({ id: connectionId, body: { label: trimmed } });
      toast.success(`Renamed to ${trimmed}`);
      setOpen(false);
    } catch (error) {
      toast.error(`Couldn't rename — ${renameErrorMessage(error)}`);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpenState}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent size="sm">
        <form onSubmit={(event) => void submit(event)} noValidate>
          <DialogHeader>
            <DialogTitle>Rename this {toolkitName} account</DialogTitle>
            <DialogDescription>Only your team sees this name — it doesn&apos;t change anything at {toolkitName}.</DialogDescription>
          </DialogHeader>
          <DialogBody>
            <Field label="Name" htmlFor="rename-account-label" required>
              <Input
                id="rename-account-label"
                value={label}
                maxLength={MAX_ACCOUNT_LABEL}
                onChange={(event) => setLabel(event.target.value)}
                autoFocus
              />
            </Field>
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!label.trim() || rename.isPending}>
              {rename.isPending ? "Renaming…" : "Rename"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
