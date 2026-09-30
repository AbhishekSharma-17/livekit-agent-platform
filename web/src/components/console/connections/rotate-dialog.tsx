"use client";

import * as React from "react";
import { toast } from "sonner";
import { KeyRoundIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
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
import { Input } from "@/components/ui/input";
import { Field, FormError } from "@/components/shared/field";
import { SecretInput } from "@/components/shared/password-input";
import { errorMessage } from "@/components/console/shared/error-banner";
import { useRotateConnection } from "@/hooks/useConnections";
import type { ConnectionOut } from "@/contracts/lkap-contracts";

/**
 * "Rotate keys" (UI_UX_SPEC-V2-AMENDMENTS §2.1): replaces the api key/secret,
 * bumping `credentials_version` (a supervised pool drains and restarts on
 * the new secret). Never opened by anything but an explicit click — the
 * per-package constraint against rotating the user's live default
 * connection through the UI during a capture only binds a screenshot
 * script, which never opens this dialog. The secret is write-only
 * (docs/ui/DESIGN-SYSTEM.md section 6.2) and dropped as soon as the dialog
 * closes; a failed save keeps what was typed and says why.
 */
export function RotateDialog({ connection }: { connection: ConnectionOut }) {
  const [open, setOpen] = React.useState(false);
  const [apiKey, setApiKey] = React.useState("");
  const [apiSecret, setApiSecret] = React.useState("");
  const [fieldErrors, setFieldErrors] = React.useState<{ key?: string; secret?: string }>({});
  const [error, setError] = React.useState<string | null>(null);
  const rotate = useRotateConnection(connection.id);

  function reset() {
    setApiKey("");
    setApiSecret("");
    setFieldErrors({});
    setError(null);
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const next = {
      key: apiKey.trim() === "" ? "Enter the new API key." : undefined,
      secret: apiSecret.trim() === "" ? "Enter the new API secret." : undefined,
    };
    setFieldErrors(next);
    if (next.key || next.secret) return;
    setError(null);
    try {
      await rotate.mutateAsync({ api_key: apiKey.trim(), api_secret: apiSecret });
      toast.success(`${connection.name}: keys rotated. Test the connection to check them.`);
      setOpen(false);
      reset();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button type="button" id="rotate">
          <KeyRoundIcon aria-hidden="true" />
          Rotate keys
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={handleSubmit} className="flex min-h-0 flex-1 flex-col" noValidate>
          <DialogHeader>
            <DialogTitle>Rotate keys</DialogTitle>
            <DialogDescription>
              Replaces the stored API key and secret for “{connection.name}”. A supervised pool restarts on the new
              secret, and the connection reads “Not checked yet” until its next test.
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            <Field label="New API key" htmlFor="rotate-key" error={fieldErrors.key}>
              <Input
                id="rotate-key"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                autoComplete="off"
                spellCheck={false}
                className="font-mono"
              />
            </Field>
            <Field label="New API secret" htmlFor="rotate-secret" error={fieldErrors.secret}>
              <SecretInput id="rotate-secret" value={apiSecret} onChange={(event) => setApiSecret(event.target.value)} />
            </Field>
            <FormError>{error}</FormError>
          </DialogBody>
          <DialogFooter>
            <Button type="button" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" busy={rotate.isPending} busyLabel="Rotating…">
              Rotate keys
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
