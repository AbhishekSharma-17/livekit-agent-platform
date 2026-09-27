"use client";

import * as React from "react";
import Link from "next/link";
import { PlusIcon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { useCreateKb, useKnowledgeConnections } from "@/components/console/lib/api-hooks";
import { errorMessage } from "@/components/console/shared/error-banner";
import { Field } from "@/components/shared/field";
import { CapabilityBadge } from "@/components/shared/capability-badge";
import { EMBEDDER_CHOICES, embedderHelp, embedderLabel } from "@/components/console/knowledge/embedder-label";
import { VECTOR_STORE_CONNECTION_KINDS } from "@/components/console/settings/knowledge-connection-dialog";
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";
import { cn } from "@/lib/utils";

const DEFAULT_EMBEDDER_ID = "fastembed-embedding";
const PLATFORM = "__platform__";

/**
 * `create-kb-dialog.tsx` (docs/UI_UX_SPEC.md §7.7 item 4): "stays a dialog (2
 * fields) with embedder choice as radio cards" — Name, then the embedder.
 * V5-24 adds "Where is this knowledge stored?" (K §5.3): the platform's own
 * store (LanceDB/pgvector), or an existing knowledge connection — a vector
 * database of the workspace's own account. This is fixed once the knowledge
 * base exists (`KbCreate.connection_id`; `update_kb` refuses a change, 409),
 * so there is no edit affordance for it later — only the read-only line on
 * `kb-detail.tsx`.
 *
 * Description isn't a create-time field (`KbCreate.description` is optional
 * and there's no update-knowledge-base hook in WP-6's scope to edit it
 * afterwards); it can be added once WP-0 ships one.
 */
export function CreateKbDialog() {
  const uid = React.useId();
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [embedderId, setEmbedderId] = React.useState(DEFAULT_EMBEDDER_ID);
  const [connectionId, setConnectionId] = React.useState(PLATFORM);
  const createKb = useCreateKb();
  const { canWrite } = useWriteAccess();
  const connectionsQuery = useKnowledgeConnections();
  const storeConnections = (connectionsQuery.data?.items ?? []).filter((c) =>
    VECTOR_STORE_CONNECTION_KINDS.includes(c.kind),
  );

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (name.trim() === "") {
      toast.error("Name is required.");
      return;
    }
    try {
      const kb = await createKb.mutateAsync({
        name: name.trim(),
        embedder_id: embedderId,
        connection_id: connectionId === PLATFORM ? null : connectionId,
      });
      setName("");
      setEmbedderId(DEFAULT_EMBEDDER_ID);
      setConnectionId(PLATFORM);
      setOpen(false);
      toast.success(`"${kb.name}" created.`);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Dialog open={canWrite && open} onOpenChange={(next) => canWrite && setOpen(next)}>
      <DialogTrigger asChild>
        <Button type="button" disabled={!canWrite} title={canWrite ? undefined : writeAccessReason()}>
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

            <div className="flex flex-col gap-1.5">
              <span className="text-sm font-medium">Where is this knowledge stored?</span>
              <RadioGroup
                value={connectionId === PLATFORM ? PLATFORM : "connection"}
                onValueChange={(next) => setConnectionId(next === PLATFORM ? PLATFORM : (storeConnections[0]?.id ?? PLATFORM))}
                aria-label="Where is this knowledge stored?"
                className="gap-2"
              >
                <label htmlFor={`${uid}-store-platform`} className="flex items-start gap-2 text-sm">
                  <RadioGroupItem id={`${uid}-store-platform`} value={PLATFORM} className="mt-0.5" />
                  <span>
                    Platform default
                    <span className="block text-[0.8125rem] text-muted-foreground">No setup needed.</span>
                  </span>
                </label>
                <label htmlFor={`${uid}-store-connection`} className="flex items-start gap-2 text-sm">
                  <RadioGroupItem
                    id={`${uid}-store-connection`}
                    value="connection"
                    className="mt-0.5"
                    disabled={storeConnections.length === 0}
                  />
                  <span>
                    A knowledge connection
                    <span className="block text-[0.8125rem] text-muted-foreground">
                      Keep the vectors in your own Qdrant, Pinecone or Weaviate account. Fixed once created.
                    </span>
                  </span>
                </label>
              </RadioGroup>
              {connectionId !== PLATFORM ? (
                <Select value={connectionId} onValueChange={setConnectionId}>
                  <SelectTrigger id={`${uid}-connection`} className="w-full sm:w-72" aria-label="Knowledge connection">
                    <SelectValue placeholder="Choose a connection" />
                  </SelectTrigger>
                  <SelectContent>
                    {storeConnections.map((connection) => (
                      <SelectItem key={connection.id} value={connection.id}>
                        {connection.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : storeConnections.length === 0 ? (
                <p className="text-[0.8125rem] text-muted-foreground">
                  No knowledge connections yet.{" "}
                  <Link href="/console/settings?tab=knowledge-connections" className="font-medium text-foreground underline underline-offset-2">
                    Add one in Settings
                  </Link>
                  .
                </p>
              ) : null}
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
