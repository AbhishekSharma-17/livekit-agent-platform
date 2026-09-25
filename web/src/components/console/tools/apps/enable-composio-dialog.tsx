"use client";

import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
import { StatusChip } from "@/components/shared/status-chip";
import { Field } from "@/components/shared/field";
import { useCreateCredential, useEnableApps, useTestAppsKey, useUpdateCredential } from "@/components/console/lib/api-hooks";
import { appsErrorMessage } from "@/components/console/tools/apps/use-composio";
import { suggestedCredentialLabel } from "@/components/console/registry/provider-meta";
import type { AppKeyTestOut } from "@/contracts/lkap-contracts";

export type EnableComposioMode = "enable" | "rotate";

export interface EnableComposioDialogProps {
  mode: EnableComposioMode;
  /** Required for `mode: "rotate"` — the existing key's credential id. */
  credentialId?: string;
  trigger?: React.ReactNode;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  onDone?: () => void;
}

type TestState = { kind: "idle" } | { kind: "testing" } | { kind: "done"; result: AppKeyTestOut };

/** "Connected to <name> — N apps available", the count-only fallback, or the vendor's own message. */
export function testSummary(result: AppKeyTestOut): string {
  const name = result.project_name || result.account_name;
  const count = result.toolkits_count;
  if (name && count != null) return `Connected to ${name} — ${count} apps available`;
  if (name) return `Connected to ${name}`;
  if (count != null) return `Key works — ${count} apps available`;
  return result.message || "Key works";
}

/**
 * The **Enable Composio** flow (docs/v5/COMPOSIO.md §6, D-V5-C13): paste a
 * key, **Test key** against the pasted value (never the stored one — that is
 * `credential-test.tsx`'s job, reused for Validate), then **Save** once it
 * passes (or tick the override). The same component runs **Rotate** — same
 * fields, a new key on the existing credential id, no re-enable needed.
 *
 * The key never leaves local `useState`; it is cleared the moment the api
 * accepts it (or the dialog closes) and is never rendered again.
 */
export function EnableComposioDialog({
  mode,
  credentialId,
  trigger,
  open: openProp,
  onOpenChange,
  onDone,
}: EnableComposioDialogProps) {
  const [openState, setOpenState] = React.useState(false);
  const open = openProp ?? openState;

  const [apiKey, setApiKey] = React.useState("");
  const [test, setTest] = React.useState<TestState>({ kind: "idle" });
  const [override, setOverride] = React.useState(false);
  const [saved, setSaved] = React.useState(false);

  const testKey = useTestAppsKey();
  const createCredential = useCreateCredential();
  const updateCredential = useUpdateCredential();
  const enableApps = useEnableApps();
  const saving = createCredential.isPending || updateCredential.isPending || enableApps.isPending;

  function setOpen(next: boolean) {
    setOpenState(next);
    onOpenChange?.(next);
    if (!next) {
      // Drop the pasted key immediately — never kept past this dialog.
      setApiKey("");
      setTest({ kind: "idle" });
      setOverride(false);
      setSaved(false);
    }
  }

  async function runTest() {
    setTest({ kind: "testing" });
    try {
      const result = await testKey.mutateAsync(apiKey);
      setTest({ kind: "done", result });
    } catch (error) {
      setTest({
        kind: "done",
        result: { ok: false, account_name: null, project_name: null, toolkits_count: null, message: appsErrorMessage(error) },
      });
    }
  }

  const passed = test.kind === "done" && test.result.ok;
  const canSave = apiKey.trim().length > 0 && (passed || override);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    event.stopPropagation();
    if (saved) {
      setOpen(false);
      onDone?.();
      return;
    }
    if (!canSave) return;
    try {
      if (mode === "enable") {
        await createCredential.mutateAsync({
          provider_id: "composio",
          label: suggestedCredentialLabel({ vendor: "Composio" }),
          secrets: { api_key: apiKey },
        });
        await enableApps.mutateAsync();
      } else {
        if (!credentialId) throw new Error("Rotate needs the existing key's id.");
        await updateCredential.mutateAsync({ id: credentialId, body: { secrets: { api_key: apiKey } } });
      }
      // The api has the key now; the browser drops it.
      setApiKey("");
      toast.success(mode === "enable" ? "Composio enabled" : "Key rotated");
      setSaved(true);
    } catch (error) {
      toast.error(`Couldn't save — ${appsErrorMessage(error)}`);
    }
  }

  const title = saved ? "Key saved" : mode === "enable" ? "Enable Composio" : "Rotate the Composio key";

  const content = (
    <DialogContent size="md" aria-describedby="enable-composio-description">
      <form onSubmit={handleSubmit} className="flex min-h-0 flex-1 flex-col" noValidate>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription id="enable-composio-description">
            {mode === "enable"
              ? "Paste your Composio API key to browse and connect apps from this console."
              : "Paste the new key. Every connection and tool keeps working — nothing else changes."}
          </DialogDescription>
        </DialogHeader>

        <DialogBody>
          {saved ? (
            <div className="flex flex-col gap-2">
              <StatusChip tone="success" dot size="sm">
                {mode === "enable" ? "Composio enabled" : "Key rotated"}
              </StatusChip>
              <p className="text-[0.8125rem] text-muted-foreground">Stored securely; you won&apos;t see it again.</p>
            </div>
          ) : (
            <>
              <Field
                label="Composio API key"
                htmlFor="enable-composio-key"
                required
                hint="From your Composio project's settings. Stored securely; you won't see it again."
              >
                <Input
                  id="enable-composio-key"
                  type="password"
                  autoComplete="new-password"
                  value={apiKey}
                  onChange={(event) => {
                    setApiKey(event.target.value);
                    setTest({ kind: "idle" });
                  }}
                  placeholder="Paste your Composio API key"
                />
              </Field>

              <div className="flex flex-col gap-2">
                <div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={apiKey.trim().length === 0 || test.kind === "testing"}
                    onClick={() => void runTest()}
                  >
                    {test.kind === "testing" ? "Testing…" : "Test key"}
                  </Button>
                </div>
                <div role="status" aria-live="polite">
                  {test.kind === "testing" ? (
                    <span className="text-[0.8125rem] text-muted-foreground">Testing…</span>
                  ) : test.kind === "done" ? (
                    <StatusChip tone={test.result.ok ? "success" : "danger"} dot size="sm">
                      {test.result.ok ? testSummary(test.result) : test.result.message || "Test failed"}
                    </StatusChip>
                  ) : null}
                </div>
              </div>

              {!passed ? (
                <div className="flex items-center gap-2">
                  <Checkbox id="enable-composio-override" checked={override} onCheckedChange={(v) => setOverride(v === true)} />
                  <Label htmlFor="enable-composio-override" className="text-[0.8125rem] font-normal text-muted-foreground">
                    Save this key anyway, without a passing test
                  </Label>
                </div>
              ) : null}
            </>
          )}
        </DialogBody>

        <DialogFooter>
          {saved ? (
            <Button type="submit">Done</Button>
          ) : (
            <>
              <Button type="button" variant="outline" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={!canSave || saving}>
                {saving ? "Saving…" : mode === "enable" ? "Save key" : "Replace key"}
              </Button>
            </>
          )}
        </DialogFooter>
      </form>
    </DialogContent>
  );

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      {trigger ? <DialogTrigger asChild>{trigger}</DialogTrigger> : null}
      {content}
    </Dialog>
  );
}
