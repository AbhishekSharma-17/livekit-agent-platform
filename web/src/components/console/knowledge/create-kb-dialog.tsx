"use client";

import * as React from "react";
import { PlusIcon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { useCreateKb } from "@/components/console/lib/api-hooks";
import { errorMessage } from "@/components/console/shared/error-banner";
import { Field } from "@/components/shared/field";
import { CapabilityBadge } from "@/components/shared/capability-badge";
import { EMBEDDER_CHOICES, embedderHelp, embedderLabel } from "@/components/console/knowledge/embedder-label";
import { cn } from "@/lib/utils";

const DEFAULT_EMBEDDER_ID = "fastembed-embedding";

/**
 * `create-kb-dialog.tsx` (docs/UI_UX_SPEC.md §7.7 item 4): "stays a dialog (2
 * fields) with embedder choice as radio cards" — Name, then the embedder.
 * Description isn't a create-time field (`KbCreate.description` is optional
 * and there's no update-knowledge-base hook in WP-6's scope to edit it
 * afterwards); it can be added once WP-0 ships one.
 */
export function CreateKbDialog() {
  const uid = React.useId();
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [embedderId, setEmbedderId] = React.useState(DEFAULT_EMBEDDER_ID);
  const createKb = useCreateKb();

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (name.trim() === "") {
      toast.error("Name is required.");
      return;
    }
    try {
      const kb = await createKb.mutateAsync({ name: name.trim(), embedder_id: embedderId });
      setName("");
      setEmbedderId(DEFAULT_EMBEDDER_ID);
      setOpen(false);
      toast.success(`"${kb.name}" created.`);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button type="button">
          <PlusIcon className="size-4" /> New knowledge base
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>New knowledge base</DialogTitle>
            <DialogDescription>Documents are chunked and embedded on upload.</DialogDescription>
          </DialogHeader>
          <div className="space-y-5 py-2">
            <Field label="Name" htmlFor={`${uid}-name`} required>
              <Input
                id={`${uid}-name`}
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Policy handbook"
                autoFocus
              />
            </Field>

            <div className="flex flex-col gap-1.5">
              <span className="text-sm font-medium">Embedder</span>
              <RadioGroup
                value={embedderId}
                onValueChange={setEmbedderId}
                aria-label="Embedder"
                className="grid gap-2 sm:grid-cols-2"
              >
                {EMBEDDER_CHOICES.map((id) => {
                  const inputId = `${uid}-embedder-${id}`;
                  const selected = embedderId === id;
                  return (
                    <Label
                      key={id}
                      htmlFor={inputId}
                      className={cn(
                        "flex cursor-pointer flex-col gap-1.5 rounded-md border border-border p-3 text-sm font-normal",
                        selected && "border-primary bg-muted/50",
                      )}
                    >
                      <span className="flex items-center justify-between gap-2">
                        <span className="font-medium text-foreground">{embedderLabel(id)}</span>
                        <RadioGroupItem id={inputId} value={id} />
                      </span>
                      <span className="text-xs text-muted-foreground">{embedderHelp(id)}</span>
                      <CapabilityBadge
                        kind={id === "fastembed-embedding" ? "no-key" : "key-required"}
                        className="self-start"
                      />
                    </Label>
                  );
                })}
              </RadioGroup>
            </div>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={createKb.isPending}>
              {createKb.isPending ? "Creating…" : "Create"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
