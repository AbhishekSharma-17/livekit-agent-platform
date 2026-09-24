"use client";

import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Field } from "@/components/shared/field";
import { StatusChip } from "@/components/shared/status-chip";
import { Textarea } from "@/components/ui/textarea";
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
import { useDryRunTool } from "@/components/console/lib/api-hooks";
import { errorMessage } from "@/components/console/shared/error-banner";
import type { ToolDryRunResult } from "@/contracts/lkap-contracts";

export function DryRunDialog({ toolId, trigger }: { toolId: string; trigger: React.ReactNode }) {
  const argsId = React.useId();
  const [open, setOpen] = React.useState(false);
  const [argsJson, setArgsJson] = React.useState("{}");
  const [result, setResult] = React.useState<ToolDryRunResult | null>(null);
  const dryRun = useDryRunTool();

  async function handleRun() {
    let args: Record<string, unknown>;
    try {
      args = JSON.parse(argsJson) as Record<string, unknown>;
    } catch {
      toast.error("Arguments must be valid JSON.");
      return;
    }
    try {
      const outcome = await dryRun.mutateAsync({ id: toolId, body: { arguments: args } });
      setResult(outcome);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setResult(null);
      }}
    >
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>Dry run</DialogTitle>
          <DialogDescription>Calls the tool with these arguments and shows the raw result.</DialogDescription>
        </DialogHeader>

        <DialogBody className="gap-3">
          <Field label="Arguments (JSON)" htmlFor={argsId}>
            <Textarea
              id={argsId}
              className="min-h-24 font-mono text-xs"
              value={argsJson}
              onChange={(e) => setArgsJson(e.target.value)}
            />
          </Field>
          {result ? (
            <div className="rounded-lg border border-border bg-muted/40 p-3 text-xs">
              <div className="mb-1.5 flex items-center gap-2">
                <StatusChip tone={result.ok ? "success" : "danger"}>{result.ok ? "OK" : "Failed"}</StatusChip>
                <span className="text-muted-foreground">
                  {result.status_code ?? "—"} · {result.duration_ms}ms
                </span>
              </div>
              <pre tabIndex={0} className="max-h-72 overflow-auto font-mono break-all whitespace-pre-wrap outline-none focus-visible:ring-2 focus-visible:ring-ring">{result.result}</pre>
            </div>
          ) : null}
        </DialogBody>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => setOpen(false)}>
            Close
          </Button>
          <Button type="button" onClick={handleRun} disabled={dryRun.isPending}>
            {dryRun.isPending ? "Running…" : "Run"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
