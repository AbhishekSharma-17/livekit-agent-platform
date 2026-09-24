"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { ArrowLeftIcon, CircleAlertIcon, FlaskConicalIcon } from "lucide-react";

import { Field } from "@/components/shared/field";
import { Icon } from "@/components/shared/icon";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { RadioGroup } from "@/components/ui/radio-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { useCreateAgent, useCredentials, useProviders } from "@/components/console/lib/api-hooks";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { useConnections } from "@/hooks/useConnections";
import type { AgentCreate, TemplateOut } from "@/contracts/lkap-contracts";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

import { TemplatePreview } from "./template-preview";
import { TemplateChipPill, TemplateTile } from "./template-tile";
import {
  TEMPLATE_CATEGORY_META,
  agentLandingHref,
  gatedDifferences,
  providerLookup,
  type ProviderLookup,
} from "./template-meta";
import { useTemplates, type TemplateGallery } from "./use-templates";

export interface CreateAgentDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Preselect this starter (`/console/agents/new?template=<id>`); unknown ids fall back to the first. */
  initialTemplateId?: string | null;
  /** Called with the new agent's id just before navigating to it. */
  onCreated?: (agentId: string) => void;
}

/**
 * The New agent dialog (docs/v4/TEMPLATES.md §6, R-V4-2): a `size="xl"`
 * panel with two steps — choose a starter, then name it — opened from every
 * "New agent" trigger. Creating posts `{name, description, template_id,
 * connection_id?, config: null}`; the api seeds everything else. On success
 * it toasts "Created from <starter>" (plus a line for anything seeding had to
 * leave off) and opens the editor on the starter's first next-step section.
 * The dialog never closes on an error.
 */
export function CreateAgentDialog({ open, onOpenChange, initialTemplateId, onCreated }: CreateAgentDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* Content (and so all step state) mounts on open, so every open starts fresh at step 1. */}
      <DialogContent size="xl" className="lg:h-[min(85dvh,46rem)]" onOpenAutoFocus={focusSelectedTile}>
        <CreateAgentSteps initialTemplateId={initialTemplateId} onOpenChange={onOpenChange} onCreated={onCreated} />
      </DialogContent>
    </Dialog>
  );
}

/** §6.4 a11y: focus moves to the gallery (the checked tile) on open. */
function focusSelectedTile(event: Event) {
  const content = event.currentTarget as HTMLElement | null;
  const checked = content?.querySelector<HTMLElement>('[role="radio"][aria-checked="true"]');
  if (checked) {
    event.preventDefault();
    checked.focus({ preventScroll: false });
  }
}

type Step = "choose" | "name";

interface IssueLine {
  message: string;
  path?: string;
}

interface CreateError {
  title: string;
  lines: IssueLine[];
  /** The api refused a keyless pipeline (no LiveKit Inference on this connection). */
  needsKeys: boolean;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : null;
}

/** Turn a failed `POST /v1/agents` into the list shown under the form. */
export function describeCreateError(error: unknown): CreateError {
  if (!(error instanceof ApiError)) {
    return { title: "Couldn't create the agent", lines: [{ message: errorMessage(error) }], needsKeys: false };
  }
  const details = asRecord(error.details);
  const lines: IssueLine[] = [];
  const issues = Array.isArray(details?.issues) ? details.issues : [];
  for (const raw of issues) {
    const issue = asRecord(raw);
    if (!issue || typeof issue.message !== "string") continue;
    if (issue.severity === "warning") continue;
    lines.push({ message: issue.message, path: typeof issue.path === "string" && issue.path ? issue.path : undefined });
  }
  if (lines.length === 0 && Array.isArray(details?.errors)) {
    for (const message of details.errors) if (typeof message === "string") lines.push({ message });
  }
  if (lines.length === 0) lines.push({ message: error.message });

  if (Array.isArray(details?.known) && typeof details?.template_id === "string") {
    return {
      title: "This starter isn't available on this server",
      lines: [{ message: `Go back and pick one of: ${(details.known as unknown[]).join(", ")}.` }],
      needsKeys: false,
    };
  }
  const needsKeys = lines.some((line) => /inference/i.test(line.message));
  return {
    title: error.status === 422 ? "This agent can't be created yet" : "Couldn't create the agent",
    lines,
    needsKeys,
  };
}

function CreateAgentSteps({
  initialTemplateId,
  onOpenChange,
  onCreated,
}: Omit<CreateAgentDialogProps, "open">) {
  const router = useRouter();
  const galleryQuery = useTemplates();
  const providersQuery = useProviders();
  const keysQuery = useCredentials();
  const createAgent = useCreateAgent();

  const [step, setStep] = React.useState<Step>("choose");
  const [selectedId, setSelectedId] = React.useState<string>("");
  const [name, setName] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [connectionId, setConnectionId] = React.useState<string>("");
  const [nameTouched, setNameTouched] = React.useState(false);
  const [createError, setCreateError] = React.useState<CreateError | null>(null);

  const items = React.useMemo(() => galleryQuery.data?.items ?? [], [galleryQuery.data]);
  const providers = React.useMemo(() => providerLookup(providersQuery.data?.providers), [providersQuery.data]);
  const keyProviderIds = React.useMemo(
    () => new Set((keysQuery.data?.items ?? []).map((key) => key.provider_id)),
    [keysQuery.data],
  );

  // Preselect once the gallery arrives: the deep link's starter, else the first (Blank agent).
  React.useEffect(() => {
    if (selectedId || items.length === 0) return;
    const wanted = initialTemplateId ? items.find((item) => item.template.id === initialTemplateId) : undefined;
    setSelectedId((wanted ?? items[0]).template.id);
  }, [initialTemplateId, items, selectedId]);

  const galleryRef = React.useRef<HTMLDivElement>(null);

  const selected = items.find((item) => item.template.id === selectedId);

  function goToName() {
    if (!selected) return;
    setName(selected.template.name);
    setDescription(selected.template.tagline);
    setNameTouched(false);
    setCreateError(null);
    setStep("name");
  }

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    setNameTouched(true);
    if (!selected || name.trim().length === 0) return;
    setCreateError(null);
    const body: AgentCreate = { name: name.trim(), description: description.trim(), config: null };
    if (galleryQuery.data?.source === "packs") {
      body.pack_id = selected.pack.id;
    } else {
      body.template_id = selected.template.id;
    }
    if (connectionId) body.connection_id = connectionId;
    try {
      const agent = await createAgent.mutateAsync(body);
      const notes = gatedDifferences(selected, agent.config, providers);
      toast.success(`Created from ${selected.template.name}`, notes.length > 0 ? { description: notes.join(" ") } : undefined);
      onCreated?.(agent.id);
      router.push(agentLandingHref(agent.id, selected.template));
      onOpenChange(false);
    } catch (error) {
      setCreateError(describeCreateError(error));
    }
  }

  if (step === "name" && selected) {
    return (
      <NameStep
        item={selected}
        name={name}
        description={description}
        connectionId={connectionId}
        nameTouched={nameTouched}
        pending={createAgent.isPending}
        error={createError}
        onName={setName}
        onNameBlur={() => setNameTouched(true)}
        onDescription={setDescription}
        onConnection={setConnectionId}
        onBack={() => {
          setCreateError(null);
          setStep("choose");
        }}
        onSubmit={handleCreate}
      />
    );
  }

  return (
    <ChooseStep
      gallery={galleryQuery.data}
      isLoading={galleryQuery.isLoading}
      error={galleryQuery.isError ? galleryQuery.error : null}
      onRetry={() => void galleryQuery.refetch()}
      selectedId={selectedId}
      onSelect={setSelectedId}
      selected={selected}
      providers={providers}
      keyProviderIds={keyProviderIds}
      galleryRef={galleryRef}
      onCancel={() => onOpenChange(false)}
      onContinue={goToName}
    />
  );
}

/* -------------------------------------------------------------------------- */
/* Step 1 — choose a starter                                                  */
/* -------------------------------------------------------------------------- */

interface ChooseStepProps {
  gallery: TemplateGallery | undefined;
  isLoading: boolean;
  error: unknown;
  onRetry: () => void;
  selectedId: string;
  onSelect: (id: string) => void;
  selected: TemplateOut | undefined;
  providers: ProviderLookup;
  keyProviderIds: ReadonlySet<string>;
  galleryRef: React.RefObject<HTMLDivElement | null>;
  onCancel: () => void;
  onContinue: () => void;
}

function ChooseStep({
  gallery,
  isLoading,
  error,
  onRetry,
  selectedId,
  onSelect,
  selected,
  providers,
  keyProviderIds,
  galleryRef,
  onCancel,
  onContinue,
}: ChooseStepProps) {
  const items = gallery?.items ?? [];

  // Focus the checked tile once it exists (on open the gallery may still be
  // loading; on "Back" this step remounts) and scroll it into view, which
  // matters for a deep link to a starter low in the list.
  const focused = React.useRef(false);
  React.useEffect(() => {
    if (focused.current || !selectedId) return;
    const radio = galleryRef.current?.querySelector<HTMLElement>('[role="radio"][aria-checked="true"]');
    if (!radio) return;
    focused.current = true;
    radio.focus({ preventScroll: true });
    radio.closest<HTMLElement>('[data-slot="template-tile"]')?.scrollIntoView?.({ block: "nearest" });
  }, [galleryRef, selectedId, items.length]);

  // Code-pack examples (insurance) sit after the configuration starters, under their own label.
  const firstExample = items.findIndex((item) => item.template.category === "example");
  const labelId = React.useId();

  return (
    <>
      <DialogHeader>
        <DialogTitle>New agent</DialogTitle>
        <DialogDescription>Start from a starter and change anything afterwards.</DialogDescription>
      </DialogHeader>

      <DialogBody className="gap-0 p-0 lg:grid lg:grid-cols-12 lg:overflow-hidden">
        <div
          ref={galleryRef}
          className="flex flex-col gap-3 px-5 py-5 lg:col-span-7 lg:min-h-0 lg:overflow-y-auto lg:overscroll-contain"
        >
          <p id={labelId} className="sr-only">
            Starter
          </p>
          {error ? (
            <ErrorBanner message={`Couldn't load starters — ${errorMessage(error)}`} onRetry={onRetry} />
          ) : isLoading ? (
            <div role="status" aria-label="Loading starters" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-2">
              {Array.from({ length: 6 }, (_, i) => (
                <Skeleton key={i} className="h-36 w-full rounded-lg" />
              ))}
            </div>
          ) : (
            <RadioGroup
              value={selectedId}
              onValueChange={onSelect}
              aria-labelledby={labelId}
              className="grid gap-3 sm:grid-cols-2"
            >
              {items.map((item, index) => (
                <React.Fragment key={item.template.id}>
                  {index === firstExample && firstExample > 0 ? (
                    <div className="col-span-full mt-2 flex items-center gap-3" data-slot="gallery-divider">
                      <span className="inline-flex items-center gap-1.5 text-[0.6875rem] font-semibold tracking-[0.06em] text-muted-foreground uppercase">
                        <Icon as={FlaskConicalIcon} size="sm" className="size-3.5" />
                        Advanced example
                      </span>
                      <span aria-hidden="true" className="h-px flex-1 bg-border" />
                    </div>
                  ) : null}
                  <TemplateTile
                    item={item}
                    selected={item.template.id === selectedId}
                    keyProviderIds={keyProviderIds}
                    providers={providers}
                  >
                    {/* Below lg the preview pane is gone; the selected tile expands to show it. */}
                    <TemplatePreview item={item} providers={providers} variant="inline" />
                  </TemplateTile>
                </React.Fragment>
              ))}
            </RadioGroup>
          )}
          {gallery?.source === "packs" ? (
            <p className="text-xs text-muted-foreground">
              This server lists packs rather than starters. Update the server to see every starter.
            </p>
          ) : null}
        </div>

        <aside
          aria-label="Starter preview"
          className="hidden border-l border-border bg-muted/30 lg:col-span-5 lg:block lg:min-h-0 lg:overflow-y-auto lg:overscroll-contain"
        >
          <div className="px-5 py-5">
            {selected ? (
              <TemplatePreview key={selected.template.id} item={selected} providers={providers} variant="pane" />
            ) : (
              <div className="flex flex-col gap-3" aria-hidden="true">
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-6 w-48" />
                <Skeleton className="h-16 w-full" />
                <Skeleton className="h-40 w-full" />
              </div>
            )}
          </div>
        </aside>
      </DialogBody>

      <DialogFooter className="sm:items-center">
        {selected ? (
          <p className="mr-auto hidden min-w-0 truncate text-xs text-muted-foreground sm:block" aria-live="polite">
            Selected: <span className="font-medium text-foreground">{selected.template.name}</span>
          </p>
        ) : null}
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="button" onClick={onContinue} disabled={!selected}>
          Continue
        </Button>
      </DialogFooter>
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* Step 2 — name it                                                           */
/* -------------------------------------------------------------------------- */

interface NameStepProps {
  item: TemplateOut;
  name: string;
  description: string;
  connectionId: string;
  nameTouched: boolean;
  pending: boolean;
  error: CreateError | null;
  onName: (value: string) => void;
  onNameBlur: () => void;
  onDescription: (value: string) => void;
  onConnection: (value: string) => void;
  onBack: () => void;
  onSubmit: (event: React.FormEvent) => void;
}

function NameStep({
  item,
  name,
  description,
  connectionId,
  nameTouched,
  pending,
  error,
  onName,
  onNameBlur,
  onDescription,
  onConnection,
  onBack,
  onSubmit,
}: NameStepProps) {
  const uid = React.useId();
  const { template } = item;
  const category = TEMPLATE_CATEGORY_META[template.category] ?? TEMPLATE_CATEGORY_META.example;
  const nameError = nameTouched && name.trim().length === 0 ? "Name is required." : null;
  const nameRef = React.useRef<HTMLInputElement>(null);
  const errorRef = React.useRef<HTMLDivElement>(null);

  // §6.4 a11y: focus moves to the Name field on step 2.
  React.useEffect(() => {
    nameRef.current?.focus();
    nameRef.current?.select();
  }, []);

  // A failed create scrolls its issue list into view.
  React.useEffect(() => {
    if (error) errorRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [error]);

  // Connection: only when the workspace has more than one (default preselected).
  const connectionsQuery = useConnections();
  const connections = React.useMemo(() => connectionsQuery.data?.items ?? [], [connectionsQuery.data]);
  const showConnection = connections.length > 1;
  React.useEffect(() => {
    if (!showConnection || connectionId) return;
    const fallback = connections.find((connection) => connection.is_default) ?? connections[0];
    if (fallback) onConnection(fallback.id);
  }, [connectionId, connections, onConnection, showConnection]);

  return (
    <form onSubmit={onSubmit} noValidate className="flex min-h-0 flex-1 flex-col">
      <DialogHeader>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            onClick={onBack}
            aria-label="Back to starters"
            className="-ml-1.5 shrink-0"
          >
            <ArrowLeftIcon />
          </Button>
          <DialogTitle className="min-w-0 truncate">New agent · {template.name}</DialogTitle>
        </div>
        <DialogDescription>Name your agent. You can change everything afterwards.</DialogDescription>
      </DialogHeader>

      <DialogBody>
        <div className="mx-auto flex w-full max-w-xl flex-col gap-5">
          <div className="flex items-start gap-3 rounded-lg border border-brand-line bg-brand-soft/40 p-3.5">
            <span
              aria-hidden="true"
              className="inline-flex size-9 shrink-0 items-center justify-center rounded-md border border-brand-line bg-brand-soft text-brand-text"
            >
              <Icon as={category.icon} size="md" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-foreground">{template.name}</p>
              <p className="mt-0.5 text-[0.8125rem] leading-[1.125rem] text-muted-foreground">{template.tagline}</p>
              {(template.chips ?? []).length > 0 ? (
                <span role="list" aria-label="Capabilities" className="mt-2.5 flex flex-wrap gap-1.5">
                  {(template.chips ?? []).map((chip) => (
                    <TemplateChipPill key={chip} chip={chip} />
                  ))}
                </span>
              ) : null}
            </div>
            <Button type="button" variant="link" size="sm" onClick={onBack} className="h-auto shrink-0 px-0 text-xs">
              Change
            </Button>
          </div>

          <Field label="Name" htmlFor={`${uid}-name`} required error={nameError}>
            <Input
              ref={nameRef}
              id={`${uid}-name`}
              value={name}
              onChange={(event) => onName(event.target.value)}
              onBlur={onNameBlur}
              placeholder="Support line"
              autoComplete="off"
              maxLength={120}
            />
          </Field>
          <Field label="Description" htmlFor={`${uid}-description`} optional hint="Shown to callers on the call page.">
            <Textarea
              id={`${uid}-description`}
              value={description}
              onChange={(event) => onDescription(event.target.value)}
              placeholder="What this agent is for"
              rows={3}
            />
          </Field>
          {showConnection ? (
            <Field
              label="Connection"
              htmlFor={`${uid}-connection`}
              hint="The LiveKit deployment this agent runs on. You can move it later."
            >
              <Select value={connectionId} onValueChange={onConnection}>
                <SelectTrigger id={`${uid}-connection`} className="w-full">
                  <SelectValue placeholder="Choose a connection" />
                </SelectTrigger>
                <SelectContent>
                  {connections.map((connection) => (
                    <SelectItem key={connection.id} value={connection.id}>
                      {connection.name}
                      {connection.is_default ? " (default)" : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          ) : null}

          {error ? (
            <div
              ref={errorRef}
              role="alert"
              data-slot="create-agent-issues"
              className="overflow-hidden rounded-lg border border-danger/30 bg-danger-soft"
            >
              <p className="flex items-center gap-2 px-4 pt-3 text-sm font-semibold text-danger-text">
                <Icon as={CircleAlertIcon} size="md" />
                {error.title}
              </p>
              <ul className="flex flex-col gap-2 px-4 py-3">
                {error.lines.map((line, index) => (
                  <li key={`${line.path ?? ""}-${index}`} className="text-[0.8125rem] leading-[1.125rem] text-danger-text">
                    <p className="text-pretty break-words">{line.message}</p>
                    {line.path ? <p className="mt-0.5 font-mono text-xs break-all opacity-80">{line.path}</p> : null}
                  </li>
                ))}
              </ul>
              {error.needsKeys ? (
                <p className="border-t border-danger/20 px-4 py-2.5 text-[0.8125rem] leading-[1.125rem] text-danger-text">
                  This connection can&apos;t use LiveKit Inference.{" "}
                  <Link href="/console/providers" className="font-medium underline underline-offset-2">
                    Add a provider key
                  </Link>{" "}
                  or pick a LiveKit Cloud connection, then try again.
                </p>
              ) : null}
            </div>
          ) : null}
        </div>
      </DialogBody>

      <DialogFooter>
        <Button type="button" variant="outline" onClick={onBack} disabled={pending}>
          Back
        </Button>
        <Button type="submit" disabled={pending} className={cn(pending && "cursor-progress")}>
          {pending ? "Creating…" : "Create agent"}
        </Button>
      </DialogFooter>
    </form>
  );
}
