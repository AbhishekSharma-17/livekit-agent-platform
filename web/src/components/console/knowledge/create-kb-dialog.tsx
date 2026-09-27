"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
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
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { KbCreate, KnowledgeConnectionPage } from "@/contracts/lkap-contracts";

const DEFAULT_EMBEDDER_ID = "fastembed-embedding";

/** Where the knowledge comes from: uploaded here (`kind: "managed"`) or a managed search service (`kind: "external"`). */
type Source = "upload" | "managed_search";

/** A Ragie partition name (mirrors the api's check): lower-case letters, digits, `_` and `-`. */
const PARTITION_PATTERN = /^[a-z0-9_-]{1,100}$/;

/**
 * V5-45: the Ragie connections of the workspace, for the "Managed search" choice. A local
 * query: `api-hooks.ts` has no knowledge-connection hook yet (docs/v5/_asks.md #234).
 */
function useManagedSearchConnections(enabled: boolean) {
  return useQuery({
    queryKey: ["knowledge-connections"],
    queryFn: () => api.get<KnowledgeConnectionPage>("knowledge-connections"),
    enabled,
    select: (page) => page.items.filter((item) => item.kind === "ragie"),
  });
}

function ChoiceCard({
  id,
  value,
  selected,
  title,
  hint,
  children,
}: {
  id: string;
  value: string;
  selected: boolean;
  title: string;
  hint?: string;
  children?: React.ReactNode;
}) {
  return (
    <Label
      htmlFor={id}
      className={cn(
        "flex cursor-pointer flex-col gap-1.5 rounded-md border border-border p-3 text-sm font-normal",
        selected && "border-primary bg-muted/50",
      )}
    >
      <span className="flex items-center justify-between gap-2">
        <span className="font-medium text-foreground">{title}</span>
        <RadioGroupItem id={id} value={value} />
      </span>
      <span className="text-xs text-muted-foreground">{hint}</span>
      {children}
    </Label>
  );
}

/**
 * `create-kb-dialog.tsx` (docs/UI_UX_SPEC.md §7.7 item 4): "stays a dialog (2
 * fields) with embedder choice as radio cards" — Name, then the embedder.
 * Description isn't a create-time field (`KbCreate.description` is optional
 * and there's no update-knowledge-base hook in WP-6's scope to edit it
 * afterwards); it can be added once WP-0 ships one.
 *
 * V5-45: a second source, "Managed search (Ragie)": the documents stay in Ragie and this
 * knowledge base reads one Ragie partition (`kind: "external"`, `connection_id`,
 * `external_ref`). Nothing is uploaded, so the embedder choice is hidden for it.
 */
export function CreateKbDialog() {
  const uid = React.useId();
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [embedderId, setEmbedderId] = React.useState(DEFAULT_EMBEDDER_ID);
  const [source, setSource] = React.useState<Source>("upload");
  const [connectionId, setConnectionId] = React.useState("");
  const [partition, setPartition] = React.useState("");
  const createKb = useCreateKb();
  const { canWrite } = useWriteAccess();
  const connections = useManagedSearchConnections(open && source === "managed_search");
  const ragie = React.useMemo(() => connections.data ?? [], [connections.data]);

  // One Ragie connection (the usual case) is picked for the user.
  React.useEffect(() => {
    if (source === "managed_search" && connectionId === "" && ragie.length === 1) {
      setConnectionId(ragie[0].id);
    }
  }, [source, connectionId, ragie]);

  function reset() {
    setName("");
    setEmbedderId(DEFAULT_EMBEDDER_ID);
    setSource("upload");
    setConnectionId("");
    setPartition("");
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (name.trim() === "") {
      toast.error("Name is required.");
      return;
    }
    let body: KbCreate = { name: name.trim(), embedder_id: embedderId };
    if (source === "managed_search") {
      if (connectionId === "") {
        toast.error("Pick the Ragie connection to search.");
        return;
      }
      const ref = partition.trim();
      if (!PARTITION_PATTERN.test(ref)) {
        toast.error("Enter the Ragie partition: lower-case letters, digits, _ and -.");
        return;
      }
      body = { name: name.trim(), kind: "external", connection_id: connectionId, external_ref: ref };
    }
    try {
      const kb = await createKb.mutateAsync(body);
      reset();
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
            <DialogDescription>
              {source === "upload"
                ? "Documents are chunked and embedded on upload."
                : "The documents stay in Ragie; agents search them there."}
            </DialogDescription>
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
              <span className="text-sm font-medium" id={`${uid}-source-label`}>
                Where are the documents?
              </span>
              <RadioGroup
                value={source}
                onValueChange={(value) => setSource(value as Source)}
                aria-labelledby={`${uid}-source-label`}
                className="grid gap-2 sm:grid-cols-2"
              >
                <ChoiceCard
                  id={`${uid}-source-upload`}
                  value="upload"
                  selected={source === "upload"}
                  title="Uploaded here"
                  hint="You add files to this knowledge base and the platform searches them."
                />
                <ChoiceCard
                  id={`${uid}-source-managed`}
                  value="managed_search"
                  selected={source === "managed_search"}
                  title="Managed search (Ragie)"
                  hint="Your documents stay in Ragie. Nothing is uploaded here."
                />
              </RadioGroup>
            </div>

            {source === "upload" ? (
              <div className="flex flex-col gap-1.5">
                <span className="text-sm font-medium">Embedder</span>
                <RadioGroup
                  value={embedderId}
                  onValueChange={setEmbedderId}
                  aria-label="Embedder"
                  className="grid gap-2 sm:grid-cols-2"
                >
                  {EMBEDDER_CHOICES.map((id) => (
                    <ChoiceCard
                      key={id}
                      id={`${uid}-embedder-${id}`}
                      value={id}
                      selected={embedderId === id}
                      title={embedderLabel(id)}
                      hint={embedderHelp(id)}
                    >
                      <CapabilityBadge
                        kind={id === "fastembed-embedding" ? "no-key" : "key-required"}
                        className="self-start"
                      />
                    </ChoiceCard>
                  ))}
                </RadioGroup>
              </div>
            ) : (
              <>
                <div className="flex flex-col gap-1.5">
                  <span className="text-sm font-medium" id={`${uid}-connection-label`}>
                    Ragie connection
                  </span>
                  {connections.isLoading ? (
                    <p className="text-xs text-muted-foreground">Loading connections…</p>
                  ) : ragie.length === 0 ? (
                    <p className="text-xs text-muted-foreground" role="status">
                      No Ragie connection yet. An admin adds your Ragie key, then a Ragie connection
                      under knowledge connections.
                    </p>
                  ) : (
                    <RadioGroup
                      value={connectionId}
                      onValueChange={setConnectionId}
                      aria-labelledby={`${uid}-connection-label`}
                      className="grid gap-2"
                    >
                      {ragie.map((connection) => (
                        <ChoiceCard
                          key={connection.id}
                          id={`${uid}-connection-${connection.id}`}
                          value={connection.id}
                          selected={connectionId === connection.id}
                          title={connection.name}
                          hint={
                            connection.status === "ok"
                              ? "Tested and working."
                              : connection.status === "error"
                                ? "The last test failed; check the key."
                                : "Not tested yet."
                          }
                        />
                      ))}
                    </RadioGroup>
                  )}
                </div>
                <Field
                  label="Ragie partition"
                  htmlFor={`${uid}-partition`}
                  required
                  hint="The partition to search, exactly as in Ragie: lower-case letters, digits, _ and -. The connection test lists them."
                >
                  <Input
                    id={`${uid}-partition`}
                    value={partition}
                    onChange={(e) => setPartition(e.target.value)}
                    placeholder="policies"
                    autoComplete="off"
                    spellCheck={false}
                  />
                </Field>
              </>
            )}
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
