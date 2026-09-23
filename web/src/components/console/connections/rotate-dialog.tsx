"use client";

import * as React from "react";
import { toast } from "sonner";

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
import { Field } from "@/components/shared/field";
import { errorMessage } from "@/components/console/shared/error-banner";
import { useRotateConnection } from "@/hooks/useConnections";
import type { ConnectionOut } from "@/contracts/lkap-contracts";

/**
 * "Rotate keys" (UI_UX_SPEC-V2-AMENDMENTS §2.1): replaces the api key/secret,
 * bumping `credentials_version` (a supervised pool drains and restarts on
 * the new secret). Never opened by anything but an explicit click — the
 * per-package constraint against rotating the user's live default
 * connection through the UI during a capture only binds a screenshot
 * script, which never opens this dialog.
 */
export function RotateDialog({ connection }: { connection: ConnectionOut }) {
  const [open, setOpen] = React.useState(false);
  const [apiKey, setApiKey] = React.useState("");
  const [apiSecret, setApiSecret] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const rotate = useRotateConnection(connection.id);

  function reset() {
    setApiKey("");
    setApiSecret("");
    setError(null);
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (apiKey.trim() === "" || apiSecret.trim() === "") {
      setError("Both the key and the secret are required.");
      return;
    }
    try {
      await rotate.mutateAsync({ api_key: apiKey.trim(), api_secret: apiSecret });
      toast.success(`${connection.name}: credentials rotated. Status is unverified until the next test.`);
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
        <Button type="button" variant="outline" id="rotate">
          Rotate keys
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Rotate credentials</DialogTitle>
            <DialogDescription>
              Replaces the stored api key/secret for &quot;{connection.name}&quot;. A supervised pool restarts on the
              new secret; the connection returns to &quot;unverified&quot; until the next test.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-4 py-4">
            <Field label="New API key" htmlFor="rotate-key" required>
              <Input id="rotate-key" value={apiKey} onChange={(event) => setApiKey(event.target.value)} autoComplete="off" className="font-mono text-[0.8125rem]" />
            </Field>
            <Field label="New API secret" htmlFor="rotate-secret" required error={error ?? undefined}>
              <Input
                id="rotate-secret"
                type="password"
                value={apiSecret}
                onChange={(event) => setApiSecret(event.target.value)}
                autoComplete="new-password"
                className="font-mono text-[0.8125rem]"
              />
            </Field>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={rotate.isPending}>
              {rotate.isPending ? "Rotating…" : "Rotate"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
