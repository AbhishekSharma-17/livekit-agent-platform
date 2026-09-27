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
} from "@/components/ui/dialog";
import { Field } from "@/components/shared/field";
import { StatusChip, type StatusTone } from "@/components/shared/status-chip";
import { VendorMark } from "@/components/shared/vendor-mark";
import { useProviders } from "@/components/console/lib/api-hooks";
import { CredentialPicker } from "@/components/console/registry/credential-picker";
import {
  defaultFieldValues,
  RegistryForm,
  type FieldValues,
} from "@/components/console/registry/registry-form";
import {
  useCreateKnowledgeConnection,
  useTestKnowledgeConnection,
  useUpdateKnowledgeConnection,
} from "@/components/console/lib/api-hooks";
import { errorMessage } from "@/components/console/shared/error-banner";
import { cn } from "@/lib/utils";
import type {
  KnowledgeConnectionCreate,
  KnowledgeConnectionOut,
  KnowledgeConnectionTestOut,
  KnowledgeConnectionUpdate,
  ProviderSpec,
} from "@/contracts/lkap-contracts";

/**
 * `KnowledgeConnectionKind` (`contracts/src/lkap_contracts/api_models.py`):
 * not exported to the generated TS (it's a plain module constant, not a
 * Pydantic model — `lkap_contracts.export` only walks `BaseModel`s), so this
 * mirrors it by hand. Flagged as a follow-up ask (contracts export).
 */
export type KnowledgeConnectionKind = KnowledgeConnectionCreate["kind"];

/** The registry (`GET /v1/providers`) entry each connection kind's non-secret fields and key come from. */
export const KNOWLEDGE_CONNECTION_PROVIDER_ID: Record<KnowledgeConnectionKind, string> = {
  qdrant: "qdrant",
  pinecone: "pinecone",
  weaviate: "weaviate",
  cohere_rerank: "cohere-rerank",
  voyage_rerank: "voyage-rerank",
};

/** Where a knowledge base's vectors may live (`KbCreate.connection_id`). */
export const VECTOR_STORE_CONNECTION_KINDS: readonly KnowledgeConnectionKind[] = ["qdrant", "pinecone", "weaviate"];
/** What `KnowledgeConfig.rerank = "connection:<id>"` may name (the search tool only). */
export const RERANKER_CONNECTION_KINDS: readonly KnowledgeConnectionKind[] = ["cohere_rerank", "voyage_rerank"];

/** Kinds a connection can be saved without a key for (a local or private cluster) — mirrors `KEY_REQUIRED` in `knowledge_connections/settings.py` (inverted). */
const KEY_OPTIONAL_KINDS: ReadonlySet<KnowledgeConnectionKind> = new Set(["qdrant", "weaviate"]);

/** Settings that fix where a knowledge base's vectors live — read-only once the connection stores one. Mirrors `LOCATION_FIELDS` in `knowledge_connections/settings.py`. */
const LOCATION_FIELD_NAMES: Record<KnowledgeConnectionKind, ReadonlySet<string>> = {
  qdrant: new Set(["url", "collection"]),
  pinecone: new Set(["index"]),
  weaviate: new Set(["url", "collection"]),
  cohere_rerank: new Set(),
  voyage_rerank: new Set(),
};

export function knowledgeConnectionStatusMeta(
  status: KnowledgeConnectionOut["status"],
): { tone: StatusTone; label: string } {
  switch (status) {
    case "ok":
      return { tone: "success", label: "Working" };
    case "error":
      return { tone: "danger", label: "Problem" };
    default:
      return { tone: "neutral", label: "Not tested yet" };
  }
}

export function knowledgeConnectionKindLabel(kind: KnowledgeConnectionKind, providers: ProviderSpec[]): string {
  return providers.find((p) => p.id === KNOWLEDGE_CONNECTION_PROVIDER_ID[kind])?.label ?? kind;
}

export interface KnowledgeConnectionDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Editing this connection; omit (or pass `null`) for "add a connection". */
  connection?: KnowledgeConnectionOut | null;
}

/**
 * Add / edit a knowledge connection (V5-24, K §5.3): kind radio cards grouped
 * "Vector databases" / "Re-ranking services" (create only — the kind never
 * changes after), the kind's non-secret fields (`RegistryForm` over the
 * registry entry's own `fields`, like every other provider slot), the key
 * (`CredentialPicker`, optional for Qdrant/Weaviate — a local cluster may run
 * keyless), then Save. A freshly created connection offers "Test connection"
 * right away, since every row starts `unverified`.
 *
 * A dialog, never a side drawer (the console-wide rule). `settings` on
 * `PUT` replaces the whole bag (docs/v5/_asks.md #187), so Save always sends
 * every one of the kind's fields, not just the ones the person touched.
 */
export function KnowledgeConnectionDialog({ open, onOpenChange, connection = null }: KnowledgeConnectionDialogProps) {
  const uid = React.useId();
  const mode: "create" | "edit" = connection ? "edit" : "create";
  const providersQuery = useProviders();
  const knowledgeProviders = React.useMemo(
    () => (providersQuery.data?.providers ?? []).filter((p) => p.kind === "knowledge"),
    [providersQuery.data],
  );

  const [kind, setKind] = React.useState<KnowledgeConnectionKind>(connection?.kind ?? "qdrant");
  const [name, setName] = React.useState(connection?.name ?? "");
  const [credentialId, setCredentialId] = React.useState<string | null>(connection?.credential_id ?? null);
  const [fieldValues, setFieldValues] = React.useState<FieldValues>({});
  const [errors, setErrors] = React.useState<Record<string, string | undefined>>({});
  const [formError, setFormError] = React.useState<string | null>(null);
  const [saved, setSaved] = React.useState<KnowledgeConnectionOut | null>(null);
  const [testResult, setTestResult] = React.useState<KnowledgeConnectionTestOut | null>(null);

  const spec = knowledgeProviders.find((p) => p.id === KNOWLEDGE_CONNECTION_PROVIDER_ID[kind]);
  const isVectorStore = VECTOR_STORE_CONNECTION_KINDS.includes(kind);
  const keyRequired = !KEY_OPTIONAL_KINDS.has(kind);

  const createMutation = useCreateKnowledgeConnection();
  const updateMutation = useUpdateKnowledgeConnection();
  const testMutation = useTestKnowledgeConnection();
  const pending = createMutation.isPending || updateMutation.isPending;

  const reset = React.useCallback(() => {
    setKind(connection?.kind ?? "qdrant");
    setName(connection?.name ?? "");
    setCredentialId(connection?.credential_id ?? null);
    setErrors({});
    setFormError(null);
    setSaved(null);
    setTestResult(null);
  }, [connection]);

  React.useEffect(() => {
    if (open) reset();
  }, [open, reset]);

  // Seed the settings form once the registry answers (or the kind changes):
  // the row's own values merged over the kind's defaults, so every field the
  // kind has always carries a value (PUT sends the whole bag).
  React.useEffect(() => {
    if (!spec) return;
    const defaults = defaultFieldValues(spec.fields ?? []);
    const seeded: FieldValues =
      mode === "edit" && connection && connection.kind === kind
        ? { ...defaults, ...(connection.settings as FieldValues) }
        : defaults;
    setFieldValues(seeded);
    // Only re-seed when the kind itself changes (or the dialog opens) — not on every keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spec?.id, open]);

  function chooseKind(next: KnowledgeConnectionKind) {
    setKind(next);
    setCredentialId(null);
    setErrors({});
  }

  const locationFields = LOCATION_FIELD_NAMES[kind];
  const locked = mode === "edit" && (connection?.knowledge_base_count ?? 0) > 0;
  const nativeHybridTurningOn =
    mode === "edit" &&
    (connection?.knowledge_base_count ?? 0) > 0 &&
    connection?.settings?.native_hybrid !== true &&
    fieldValues.native_hybrid === true;

  function setOpen(next: boolean) {
    onOpenChange(next);
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    event.stopPropagation();
    if (saved) {
      setOpen(false);
      return;
    }
    const nextErrors: Record<string, string | undefined> = {};
    if (name.trim() === "") nextErrors.name = "Give the connection a name you'll recognise.";
    if (!spec) nextErrors.kind = "Choose a kind.";
    if (keyRequired && !credentialId) nextErrors.credential = "Choose or add a key.";
    setErrors(nextErrors);
    if (Object.values(nextErrors).some(Boolean)) return;

    const settings = Object.fromEntries((spec?.fields ?? []).map((field) => [field.name, fieldValues[field.name]]));
    setFormError(null);
    try {
      if (mode === "create") {
        const created = await createMutation.mutateAsync({
          name: name.trim(),
          kind,
          settings,
          credential_id: credentialId,
        });
        toast.success(`"${created.name}" added.`);
        setSaved(created);
      } else {
        // Only the fields the person actually changed: `update_connection`
        // (api) resets `status` to `unverified` whenever `settings` or
        // `credential_id` is present in the payload at all — even to the
        // same value — so a bare rename must never carry them along and
        // silently drop a working connection back to "Not tested yet".
        const body: KnowledgeConnectionUpdate = {};
        const trimmedName = name.trim();
        if (trimmedName !== connection!.name) body.name = trimmedName;
        const storedSettings = (connection!.settings ?? {}) as Record<string, unknown>;
        if (Object.entries(settings).some(([key, value]) => storedSettings[key] !== value)) {
          body.settings = settings;
        }
        const storedCredentialId = connection!.credential_id ?? null;
        if (credentialId !== storedCredentialId) body.credential_id = credentialId;
        await updateMutation.mutateAsync({ id: connection!.id, body });
        toast.success("Connection updated.");
        setOpen(false);
      }
    } catch (error) {
      setFormError(errorMessage(error));
    }
  }

  async function runTest(id: string) {
    setTestResult(null);
    try {
      const result = await testMutation.mutateAsync(id);
      setTestResult(result);
    } catch (error) {
      toast.error(`Couldn't test the connection — ${errorMessage(error)}`);
    }
  }

  const title = saved
    ? `"${saved.name}" added`
    : mode === "edit"
      ? `Edit ${connection?.name ?? "connection"}`
      : "Add a knowledge connection";

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent size="lg" aria-describedby="knowledge-connection-dialog-description">
        <form onSubmit={handleSubmit} className="flex min-h-0 flex-1 flex-col" noValidate>
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription id="knowledge-connection-dialog-description">
              {saved
                ? "Run a test to check the key and see what it can see."
                : "A vector store to keep a knowledge base's vectors in your own account, or a hosted service to re-rank search results."}
            </DialogDescription>
          </DialogHeader>

          <DialogBody>
            {saved ? (
              <SavedView connection={saved} result={testResult} testing={testMutation.isPending} onTest={() => void runTest(saved.id)} />
            ) : (
              <>
                {formError ? <p className="text-[0.8125rem] text-danger-text">{formError}</p> : null}

                {mode === "create" ? (
                  <>
                    <KindPicker
                      idPrefix={uid}
                      knowledgeProviders={knowledgeProviders}
                      loading={providersQuery.isLoading}
                      value={kind}
                      onChange={chooseKind}
                    />
                    {errors.kind ? <p className="text-[0.8125rem] text-danger-text">{errors.kind}</p> : null}
                  </>
                ) : spec ? (
                  <div className="flex items-center gap-2.5">
                    <VendorMark vendor={spec.vendor} size="md" />
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-foreground">{spec.label}</p>
                      <p className="text-xs text-muted-foreground">
                        {isVectorStore ? "Vector database" : "Re-ranking service"}
                      </p>
                    </div>
                  </div>
                ) : null}

                <Field label="Name" htmlFor={`${uid}-name`} required error={errors.name} hint="Shown in the connections table and in pickers.">
                  <Input
                    id={`${uid}-name`}
                    autoComplete="off"
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    placeholder={spec ? `${spec.label} (production)` : undefined}
                    disabled={pending}
                  />
                </Field>

                {spec ? (
                  <RegistryForm
                    fields={spec.fields ?? []}
                    values={fieldValues}
                    onChange={(fieldName, value) => setFieldValues((prev) => ({ ...prev, [fieldName]: value }))}
                    idPrefix={`${uid}-field`}
                    singleColumn
                    lockedReason={(field) =>
                      locked && locationFields.has(field.name)
                        ? "Can't change while knowledge bases are stored through this connection."
                        : null
                    }
                  />
                ) : null}

                {nativeHybridTurningOn ? (
                  <p className="text-[0.8125rem] text-pretty text-warning-text">
                    Existing documents were indexed before keyword search was on — re-index this connection&apos;s
                    knowledge bases so their keyword search is complete (RUNBOOK §9.4, <code>reindex --to-connection</code>).
                  </p>
                ) : null}

                {spec ? (
                  <CredentialPicker
                    id={`${uid}-credential`}
                    spec={spec}
                    value={credentialId}
                    onChange={setCredentialId}
                    required={keyRequired}
                    error={errors.credential}
                    label="Key"
                  />
                ) : null}
                {spec && !keyRequired ? (
                  <p className="-mt-2 text-[0.8125rem] text-muted-foreground">
                    Leave this without a key for a local or private cluster.
                  </p>
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
                <Button type="submit" disabled={pending}>
                  {pending ? "Saving…" : mode === "edit" ? "Save" : "Add connection"}
                </Button>
              </>
            )}
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function KindPicker({
  idPrefix,
  knowledgeProviders,
  loading,
  value,
  onChange,
}: {
  idPrefix: string;
  knowledgeProviders: ProviderSpec[];
  loading: boolean;
  value: KnowledgeConnectionKind;
  onChange: (kind: KnowledgeConnectionKind) => void;
}) {
  const groups: { heading: string; kinds: readonly KnowledgeConnectionKind[] }[] = [
    { heading: "Vector databases", kinds: VECTOR_STORE_CONNECTION_KINDS },
    { heading: "Re-ranking services", kinds: RERANKER_CONNECTION_KINDS },
  ];
  const name = `${idPrefix}-kind`;
  return (
    <div className="flex flex-col gap-4">
      {groups.map((group) => (
        <fieldset key={group.heading} className="m-0 flex flex-col gap-2 border-0 p-0">
          <legend className="mb-1 text-sm font-medium text-foreground">{group.heading}</legend>
          {loading ? (
            <p className="text-[0.8125rem] text-muted-foreground">Loading…</p>
          ) : (
            <div className="grid gap-2 sm:grid-cols-2">
              {group.kinds.map((kind) => {
                const spec = knowledgeProviders.find((p) => p.id === KNOWLEDGE_CONNECTION_PROVIDER_ID[kind]);
                if (!spec) return null;
                const checked = value === kind;
                const inputId = `${name}-${kind}`;
                return (
                  <label
                    key={kind}
                    htmlFor={inputId}
                    className={cn(
                      "relative flex cursor-pointer items-start gap-2.5 rounded-md border border-border bg-background px-3 py-2.5",
                      "transition-colors duration-(--dur-2) hover:bg-accent",
                      "has-[:checked]:border-brand-line has-[:checked]:bg-brand-soft",
                      "has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring has-[:focus-visible]:ring-offset-2 has-[:focus-visible]:ring-offset-background",
                    )}
                  >
                    <input
                      type="radio"
                      id={inputId}
                      name={name}
                      value={kind}
                      checked={checked}
                      onChange={() => onChange(kind)}
                      className="sr-only"
                    />
                    <VendorMark vendor={spec.vendor} size="sm" className="mt-0.5" />
                    <span className="flex min-w-0 flex-col gap-0.5">
                      <span className="text-sm font-medium text-foreground">{spec.label}</span>
                      {spec.notes ? <span className="text-xs text-pretty text-muted-foreground">{spec.notes}</span> : null}
                    </span>
                  </label>
                );
              })}
            </div>
          )}
        </fieldset>
      ))}
    </div>
  );
}

/** After a create: what was saved (never the key) + "Test connection". */
function SavedView({
  connection,
  result,
  testing,
  onTest,
}: {
  connection: KnowledgeConnectionOut;
  result: KnowledgeConnectionTestOut | null;
  testing: boolean;
  onTest: () => void;
}) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium text-foreground">{connection.name}</span>
        {connection.credential_fingerprint ? (
          <span className="font-mono text-xs text-muted-foreground">{connection.credential_fingerprint}</span>
        ) : (
          <span className="text-xs text-muted-foreground">No key</span>
        )}
      </div>
      <div>
        <Button type="button" variant="outline" size="sm" onClick={onTest} disabled={testing}>
          {testing ? "Testing…" : result ? "Test again" : "Test connection"}
        </Button>
      </div>
      {testing || result ? <KnowledgeConnectionTestResultView result={result} /> : null}
    </div>
  );
}

/** The result of `POST /v1/knowledge-connections/{id}/test`, shown from the dialog and the connections table. */
export function KnowledgeConnectionTestResultView({ result }: { result: KnowledgeConnectionTestOut | null }) {
  if (!result) return null;
  const tone: StatusTone = result.ok ? "success" : "danger";
  const collections = result.collections ?? [];
  return (
    <div className="flex flex-col gap-1.5 rounded-md border border-border bg-muted/30 p-2.5">
      <div className="flex items-center gap-2">
        <StatusChip tone={tone} size="sm">
          {result.ok ? "Working" : "Problem"}
        </StatusChip>
        <span className="text-[0.8125rem] text-pretty text-foreground">{result.message}</span>
      </div>
      {collections.length > 0 ? (
        <p className="text-xs text-muted-foreground">
          Sees {collections.length === 1 ? "1 collection or index" : `${collections.length} collections or indexes`}:{" "}
          <span className="font-mono">{collections.slice(0, 8).join(", ")}</span>
          {collections.length > 8 ? "…" : ""}
        </p>
      ) : null}
      {result.dimension_expected !== null && result.dimension_expected !== undefined ? (
        <p className="text-xs text-muted-foreground">
          Vector width: knowledge bases here use {result.dimension_expected}
          {result.dimension_found !== null && result.dimension_found !== undefined
            ? `, this target holds ${result.dimension_found}`
            : ""}
          .
        </p>
      ) : null}
    </div>
  );
}
