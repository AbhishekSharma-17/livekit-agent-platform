"use client";

import * as React from "react";
import Link from "next/link";
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
import { useCreateKb, useKnowledgeConnections } from "@/components/console/lib/api-hooks";
import { errorMessage } from "@/components/console/shared/error-banner";
import { Field } from "@/components/shared/field";
import { CapabilityBadge } from "@/components/shared/capability-badge";
import { EMBEDDER_CHOICES, embedderHelp, embedderLabel } from "@/components/console/knowledge/embedder-label";
import {
  MANAGED_SEARCH_CONNECTION_KINDS,
  VECTOR_STORE_CONNECTION_KINDS,
} from "@/components/console/settings/knowledge-connection-dialog";
import { useWriteAccess, writeAccessReason } from "@/components/console/lib/roles";
import { cn } from "@/lib/utils";
import type { KbCreate, KnowledgeConnectionOut } from "@/contracts/lkap-contracts";

const DEFAULT_EMBEDDER_ID = "fastembed-embedding";

/** Where a knowledge base's documents live. `connection` and `managed_search` both name a `KnowledgeConnectionOut`; `platform` needs none. */
type Storage = "platform" | "connection" | "managed_search";

/** A Ragie partition name (mirrors the api's check): lower-case letters, digits, `_` and `-`. */
const PARTITION_PATTERN = /^[a-z0-9_-]{1,100}$/;

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
        "flex cursor-pointer flex-col gap-1.5 rounded border border-border p-3 text-body font-normal",
        selected && "border-brand bg-muted/50",
      )}
    >
      <span className="flex items-center justify-between gap-2">
        <span className="font-medium text-foreground">{title}</span>
        <RadioGroupItem id={id} value={value} />
      </span>
      <span className="text-caption text-text-secondary">{hint}</span>
      {children}
    </Label>
  );
}

/**
 * `create-kb-dialog.tsx` (docs/UI_UX_SPEC.md §7.7 item 4): "stays a dialog (2
 * fields) with embedder choice as radio cards" — Name, then the embedder.
 *
 * "Where is this knowledge stored?" (V5-24, K §5.3; V5-45 added the third
 * choice) offers three storage kinds, one radio group:
 * - **Platform default** — the platform's own store (LanceDB/pgvector); no
 *   connection needed.
 * - **A knowledge connection** — a vector database of the workspace's own
 *   account (Qdrant/Pinecone/Weaviate, `KbCreate.connection_id`).
 * - **Managed search (Ragie)** — the documents stay in Ragie and this
 *   knowledge base reads one Ragie partition (`kind: "external"`,
 *   `connection_id`, `external_ref`); nothing is uploaded, so the embedder
 *   choice is hidden for it.
 *
 * All three are fixed once the knowledge base exists (`update_kb` refuses a
 * change, 409), so there is no edit affordance for them later — only the
 * read-only line on `kb-detail.tsx`. The connections list is fetched lazily
 * (only once a connection-backed choice is picked), so creating a plain
 * platform-default knowledge base never calls `/v1/knowledge-connections`.
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
  const [storage, setStorage] = React.useState<Storage>("platform");
  const [connectionId, setConnectionId] = React.useState("");
  const [partition, setPartition] = React.useState("");
  const createKb = useCreateKb();
  const { canWrite } = useWriteAccess();

  const connectionsQuery = useKnowledgeConnections({ enabled: open && storage !== "platform" });
  const storeConnections = React.useMemo(
    () => (connectionsQuery.data?.items ?? []).filter((c) => VECTOR_STORE_CONNECTION_KINDS.includes(c.kind)),
    [connectionsQuery.data],
  );
  const ragieConnections = React.useMemo(
    () => (connectionsQuery.data?.items ?? []).filter((c) => MANAGED_SEARCH_CONNECTION_KINDS.includes(c.kind)),
    [connectionsQuery.data],
  );

  // The only choice (the usual case) is picked for the user.
  React.useEffect(() => {
    if (connectionId !== "") return;
    const choices = storage === "connection" ? storeConnections : storage === "managed_search" ? ragieConnections : [];
    if (choices.length === 1) setConnectionId(choices[0].id);
  }, [storage, connectionId, storeConnections, ragieConnections]);

  function reset() {
    setName("");
    setEmbedderId(DEFAULT_EMBEDDER_ID);
    setStorage("platform");
    setConnectionId("");
    setPartition("");
  }

  function chooseStorage(next: Storage) {
    setStorage(next);
    setConnectionId("");
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (name.trim() === "") {
      toast.error("Name is required.");
      return;
    }
    let body: KbCreate = { name: name.trim(), embedder_id: embedderId };
    if (storage === "connection") {
      if (connectionId === "") {
        toast.error("Pick a knowledge connection, or add one first.");
        return;
      }
      body = { name: name.trim(), embedder_id: embedderId, connection_id: connectionId };
    } else if (storage === "managed_search") {
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
              {storage === "managed_search"
                ? "The documents stay in Ragie; agents search them there."
                : "Documents are chunked and embedded on upload."}
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
              <span className="text-body font-medium" id={`${uid}-storage-label`}>
                Where is this knowledge stored?
              </span>
              <RadioGroup
                value={storage}
                onValueChange={(value) => chooseStorage(value as Storage)}
                aria-labelledby={`${uid}-storage-label`}
                className="grid gap-2 sm:grid-cols-3"
              >
                <ChoiceCard
                  id={`${uid}-storage-platform`}
                  value="platform"
                  selected={storage === "platform"}
                  title="Platform default"
                  hint="No setup needed."
                />
                <ChoiceCard
                  id={`${uid}-storage-connection`}
                  value="connection"
                  selected={storage === "connection"}
                  title="A knowledge connection"
                  hint="Keep the vectors in a vector database you connect in Settings."
                />
                <ChoiceCard
                  id={`${uid}-storage-managed`}
                  value="managed_search"
                  selected={storage === "managed_search"}
                  title="Managed search (Ragie)"
                  hint="Your documents stay in Ragie. Nothing is uploaded here."
                />
              </RadioGroup>
            </div>

            {storage === "connection" ? (
              <ConnectionPicker
                idPrefix={`${uid}-connection`}
                loading={connectionsQuery.isLoading}
                connections={storeConnections}
                connectionId={connectionId}
                onChange={setConnectionId}
                emptyMessage="No knowledge connections yet."
              />
            ) : null}

            {storage === "managed_search" ? (
              <>
                <ConnectionPicker
                  idPrefix={`${uid}-ragie`}
                  label="Ragie connection"
                  loading={connectionsQuery.isLoading}
                  connections={ragieConnections}
                  connectionId={connectionId}
                  onChange={setConnectionId}
                  emptyMessage="No Ragie connection yet. An admin adds your Ragie key, then a Ragie connection under knowledge connections."
                  connectionHint={(connection) =>
                    connection.status === "ok"
                      ? "Tested and working."
                      : connection.status === "error"
                        ? "The last test failed; check the key."
                        : "Not tested yet."
                  }
                />
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
            ) : (
              // Both "platform" and "connection" still embed the uploaded documents — only "managed_search"
              // (Ragie holds and searches its own) has no embedder to choose.
              <div className="flex flex-col gap-1.5">
                <span className="text-body font-medium">Embedder</span>
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
            )}
          </div>
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => setOpen(false)}>
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

/**
 * The connection-backed choices' shared body: a loading line, an empty
 * state naming where to add one, or a radio card per connection (auto-picked
 * by the caller when there is exactly one).
 */
function ConnectionPicker({
  idPrefix,
  label = "Connection",
  loading,
  connections,
  connectionId,
  onChange,
  emptyMessage,
  connectionHint,
}: {
  idPrefix: string;
  label?: string;
  loading: boolean;
  connections: KnowledgeConnectionOut[];
  connectionId: string;
  onChange: (id: string) => void;
  emptyMessage: string;
  connectionHint?: (connection: KnowledgeConnectionOut) => string;
}) {
  const labelId = `${idPrefix}-label`;
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-body font-medium" id={labelId}>
        {label}
      </span>
      {loading ? (
        <p className="text-caption text-text-secondary">Loading connections…</p>
      ) : connections.length === 0 ? (
        <p className="text-caption text-text-secondary" role="status">
          {emptyMessage}{" "}
          <Link
            href="/console/settings?tab=knowledge-connections"
            className="font-medium text-foreground underline underline-offset-2"
          >
            Add one in Settings
          </Link>
          .
        </p>
      ) : (
        <RadioGroup value={connectionId} onValueChange={onChange} aria-labelledby={labelId} className="grid gap-2">
          {connections.map((connection) => (
            <ChoiceCard
              key={connection.id}
              id={`${idPrefix}-${connection.id}`}
              value={connection.id}
              selected={connectionId === connection.id}
              title={connection.name}
              hint={connectionHint?.(connection)}
            />
          ))}
        </RadioGroup>
      )}
    </div>
  );
}
