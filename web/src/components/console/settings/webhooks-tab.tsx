"use client";

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { PlusIcon, SendIcon, WebhookIcon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
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
import { CheckboxRow, SwitchRow } from "@/components/shared/choice";
import { CopyButton } from "@/components/shared/copy-button";
import { EmptyState } from "@/components/shared/empty-state";
import { Field } from "@/components/shared/field";
import { RelativeTime } from "@/components/shared/relative-time";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { Section, SectionRow } from "@/components/shared/section";
import { LifecycleBadge } from "@/components/shared/status-chip";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { api } from "@/lib/api";
import type {
  WebhookDeliveryOut,
  WebhookDeliveryPage,
  WebhookEndpointOut,
  WebhookEndpointPage,
} from "@/contracts/lkap-contracts";
import { KNOWN_WEBHOOK_EVENTS, WEBHOOK_EVENT_LABEL, type WebhookEndpointCreated } from "./api-types";
import { RequireWrite } from "@/components/shared/require-write";
import { Highlight, ListNoMatches, ListSearchField, useListSearch } from "@/components/shared/list-search";
import { RowsSkeleton } from "./settings-card";

function useWebhooks() {
  return useQuery({
    queryKey: ["settings", "webhooks"] as const,
    queryFn: () => api.get<WebhookEndpointPage>("webhooks", { limit: 200 }),
  });
}

function eventsLabel(endpoint: WebhookEndpointOut): string {
  return endpoint.events && endpoint.events.length > 0
    ? endpoint.events.map((e) => WEBHOOK_EVENT_LABEL[e] ?? e).join(", ")
    : "All events";
}

function EndpointStatus({ endpoint }: { endpoint: WebhookEndpointOut }) {
  return <LifecycleBadge state={endpoint.enabled ? "enabled" : "disabled"} size="sm" />;
}

/**
 * `/v1/webhooks` needs `admin` server-side for both reads and writes
 * (`auth/roles.py::ROUTE_POLICY`) — unlike most settings tabs, a builder
 * can't even list endpoints here, so the gate wraps the whole tab rather than
 * just its mutating buttons (docs/v2/_asks.md V2-20-5): no point mounting a
 * query that only ever 403s.
 */
export function WebhooksTab() {
  return (
    <RequireWrite min="admin" title="Only admins and owners can see webhooks">
      <WebhooksTabInner />
    </RequireWrite>
  );
}

function WebhooksTabInner() {
  const query = useWebhooks();
  const queryClient = useQueryClient();
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["settings", "webhooks"] });
  const endpoints = React.useMemo(() => query.data?.items ?? [], [query.data]);
  const [deliveriesFor, setDeliveriesFor] = React.useState<WebhookEndpointOut | null>(null);
  const [editing, setEditing] = React.useState<WebhookEndpointOut | null>(null);
  const search = useListSearch("webhooks", endpoints, (endpoint) => [
    endpoint.url,
    endpoint.description,
    eventsLabel(endpoint),
  ]);
  const searchQuery = search.query;

  const actions = (endpoint: WebhookEndpointOut) => (
    <div className="flex flex-wrap items-center justify-end gap-1">
      <Button type="button" variant="ghost" size="sm" onClick={() => setDeliveriesFor(endpoint)}>
        Deliveries
      </Button>
      <TestButton endpoint={endpoint} />
      <Button type="button" variant="ghost" size="sm" onClick={() => setEditing(endpoint)}>
        Edit
      </Button>
      <DeleteButton endpoint={endpoint} onDeleted={invalidate} />
    </div>
  );

  const columns: ResponsiveTableColumn<WebhookEndpointOut>[] = [
    {
      id: "endpoint",
      header: "Endpoint",
      cell: (endpoint) => (
        <div className="min-w-0">
          <div className="truncate font-mono text-label text-foreground">
            <Highlight text={endpoint.url} query={searchQuery} />
          </div>
          {endpoint.description ? (
            <div className="truncate text-caption text-text-secondary">
              <Highlight text={endpoint.description} query={searchQuery} />
            </div>
          ) : null}
        </div>
      ),
    },
    {
      id: "events",
      header: "Events",
      className: "whitespace-normal",
      cell: (endpoint) => <span className="text-caption text-text-secondary">{eventsLabel(endpoint)}</span>,
    },
    { id: "status", header: "Status", cell: (endpoint) => <EndpointStatus endpoint={endpoint} /> },
    { id: "actions", header: <span className="sr-only">Actions</span>, align: "end", interactive: true, cell: actions },
  ];

  return (
    <Section
      id="webhooks"
      title="Webhooks"
      description="Notify an external endpoint when events happen in this workspace."
      aside={<CreateEndpointDialog onCreated={invalidate} />}
    >
      <SectionRow>
        {query.isLoading ? (
          <RowsSkeleton label="Loading webhooks" />
        ) : query.isError ? (
          <ErrorBanner error={query.error} context={{ action: "load webhooks" }} onRetry={() => void query.refetch()} />
        ) : endpoints.length === 0 ? (
          <EmptyState
            variant="plain"
            icon={WebhookIcon}
            title="No webhooks yet"
            description="Add an endpoint to hear about calls and sessions as they happen."
          />
        ) : (
          <>
            <ListSearchField search={search} label="Search webhooks" total={endpoints.length} />
            {search.noMatches ? (
              <ListNoMatches search={search} items="webhooks" />
            ) : (
              <ResponsiveTable<WebhookEndpointOut>
                columns={columns}
                rows={search.filtered}
                label="Webhook endpoints"
                getRowKey={(endpoint) => endpoint.id}
                renderCard={(endpoint) => (
                  // Phones get the same actions as the table.
                  <div className="flex flex-col gap-2">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="truncate font-mono text-label text-foreground">
                          <Highlight text={endpoint.url} query={searchQuery} />
                        </div>
                        <div className="text-caption text-text-secondary">{eventsLabel(endpoint)}</div>
                      </div>
                      <EndpointStatus endpoint={endpoint} />
                    </div>
                    <div className="-mx-2">{actions(endpoint)}</div>
                  </div>
                )}
              />
            )}
          </>
        )}
      </SectionRow>

      {editing ? (
        <EditEndpointDialog
          endpoint={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            invalidate();
          }}
        />
      ) : null}

      {deliveriesFor ? <DeliveriesDialog endpoint={deliveriesFor} onClose={() => setDeliveriesFor(null)} /> : null}
    </Section>
  );
}

function EventsPicker({
  selected,
  onChange,
  disabled,
}: {
  selected: Set<string>;
  onChange: (next: Set<string>) => void;
  disabled?: boolean;
}) {
  return (
    <fieldset className="m-0 flex min-w-0 flex-col gap-2 border-0 p-0" aria-describedby="webhook-events-hint">
      <legend className="text-label font-medium text-foreground">Events</legend>
      <p id="webhook-events-hint" className="text-caption text-text-secondary">
        Leave every box unchecked to subscribe to all events.
      </p>
      <div className="grid gap-2 sm:grid-cols-2">
        {KNOWN_WEBHOOK_EVENTS.map((event) => (
          <CheckboxRow
            key={event}
            checked={selected.has(event)}
            disabled={disabled}
            onChange={() => {
              const next = new Set(selected);
              if (next.has(event)) next.delete(event);
              else next.add(event);
              onChange(next);
            }}
            label={WEBHOOK_EVENT_LABEL[event] ?? event}
          />
        ))}
      </div>
    </fieldset>
  );
}

function CreateEndpointDialog({ onCreated }: { onCreated: () => void }) {
  const [open, setOpen] = React.useState(false);
  const [url, setUrl] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [events, setEvents] = React.useState<Set<string>>(new Set());
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<unknown>(null);
  const [created, setCreated] = React.useState<WebhookEndpointCreated | null>(null);

  function reset() {
    setUrl("");
    setDescription("");
    setEvents(new Set());
    setError(null);
    setCreated(null);
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const endpoint = await api.post<WebhookEndpointCreated>("webhooks", {
        url,
        description,
        events: [...events],
        enabled: true,
      });
      setCreated(endpoint);
      onCreated();
    } catch (err) {
      setError(err);
    } finally {
      setSaving(false);
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
        <Button type="button" variant="primary" size="sm">
          <PlusIcon aria-hidden="true" />
          Add webhook
        </Button>
      </DialogTrigger>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>Add a webhook endpoint</DialogTitle>
          <DialogDescription>The signing secret is shown once, right after creation.</DialogDescription>
        </DialogHeader>
        {created ? (
          <>
            <DialogBody className="gap-3">
              <div className="flex items-center gap-2 rounded border border-border bg-muted p-2">
                <code className="min-w-0 flex-1 font-mono text-caption break-all">{created.secret}</code>
                <CopyButton value={created.secret} label="Copy signing secret" />
              </div>
              <p className="text-caption text-text-secondary">
                Verify deliveries with the <code className="font-mono">X-LKAP-Signature</code> header and this secret.
                It won&apos;t be shown again.
              </p>
            </DialogBody>
            <DialogFooter showCloseButton />
          </>
        ) : (
          <form onSubmit={onSubmit} className="flex min-h-0 flex-1 flex-col">
            <DialogBody className="gap-4">
              {error ? <ErrorBanner error={error} context={{ action: "add the webhook" }} /> : null}
              <Field label="URL" htmlFor="webhook-url" hint="Starts with https:// (http:// works in development only).">
                <Input
                  id="webhook-url"
                  type="url"
                  required
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://example.com/hooks/lkap"
                  disabled={saving}
                />
              </Field>
              <Field label="Description" htmlFor="webhook-description" optional>
                <Input
                  id="webhook-description"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  disabled={saving}
                />
              </Field>
              <EventsPicker selected={events} onChange={setEvents} disabled={saving} />
            </DialogBody>
            <DialogFooter>
              <Button type="button" onClick={() => setOpen(false)} disabled={saving}>
                Cancel
              </Button>
              <Button type="submit" variant="primary" busy={saving} busyLabel="Creating…">
                Create
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

function EditEndpointDialog({
  endpoint,
  onClose,
  onSaved,
}: {
  endpoint: WebhookEndpointOut;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [url, setUrl] = React.useState(endpoint.url);
  const [description, setDescription] = React.useState(endpoint.description ?? "");
  const [enabled, setEnabled] = React.useState(endpoint.enabled ?? true);
  const [events, setEvents] = React.useState<Set<string>>(new Set(endpoint.events ?? []));
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<unknown>(null);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await api.put(`webhooks/${endpoint.id}`, { url, description, events: [...events], enabled });
      toast.success("Webhook saved");
      onSaved();
    } catch (err) {
      setError(err);
      setSaving(false);
    }
  }

  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>Edit webhook</DialogTitle>
          <DialogDescription>The signing secret doesn&apos;t change here. To rotate it, delete and add the endpoint again.</DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} className="flex min-h-0 flex-1 flex-col">
          <DialogBody className="gap-4">
            {error ? <ErrorBanner error={error} context={{ action: "save the webhook" }} /> : null}
            <Field label="URL" htmlFor="webhook-edit-url">
              <Input id="webhook-edit-url" type="url" required value={url} onChange={(e) => setUrl(e.target.value)} disabled={saving} />
            </Field>
            <Field label="Description" htmlFor="webhook-edit-description" optional>
              <Input
                id="webhook-edit-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                disabled={saving}
              />
            </Field>
            <EventsPicker selected={events} onChange={setEvents} disabled={saving} />
            <SwitchRow
              id="webhook-edit-enabled"
              label="Enabled"
              description="A disabled endpoint keeps its settings but receives nothing."
              checked={enabled}
              onCheckedChange={setEnabled}
              disabled={saving}
            />
          </DialogBody>
          <DialogFooter>
            <Button type="button" onClick={onClose} disabled={saving}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" busy={saving} busyLabel="Saving…">
              Save
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function TestButton({ endpoint }: { endpoint: WebhookEndpointOut }) {
  const [busy, setBusy] = React.useState(false);

  async function onClick() {
    setBusy(true);
    try {
      const delivery = await api.post<WebhookDeliveryOut>(`webhooks/${endpoint.id}/test`);
      if (delivery.status === "delivered") toast.success("Test event delivered");
      // The endpoint's own answer is in Deliveries; the toast stays plain.
      else toast.warning("The test event wasn't delivered", { description: "Open Deliveries to see what the endpoint answered." });
    } catch (err) {
      toast.error("Couldn't send the test event", { description: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Button type="button" variant="ghost" size="sm" onClick={onClick} busy={busy} busyLabel="Sending…">
      <SendIcon aria-hidden="true" />
      Test
    </Button>
  );
}

function DeleteButton({ endpoint, onDeleted }: { endpoint: WebhookEndpointOut; onDeleted: () => void }) {
  return (
    <ConfirmDialog
      trigger={
        <Button type="button" variant="ghost" size="sm">
          Delete
        </Button>
      }
      title="Delete this webhook?"
      description={`${endpoint.url} stops receiving events. Its delivery history is deleted too.`}
      confirmLabel="Delete webhook"
      onConfirm={async () => {
        // A failure throws: the dialog stays open and says why.
        await api.delete(`webhooks/${endpoint.id}`);
        toast.success("Webhook deleted");
        onDeleted();
      }}
    />
  );
}

/** Delivery attempts for one endpoint, in a modal (no side drawers — UI_UX_SPEC-V2-AMENDMENTS §5). */
function DeliveriesDialog({ endpoint, onClose }: { endpoint: WebhookEndpointOut; onClose: () => void }) {
  const query = useQuery({
    queryKey: ["settings", "webhooks", endpoint.id, "deliveries"] as const,
    queryFn: () => api.get<WebhookDeliveryPage>(`webhooks/${endpoint.id}/deliveries`, { limit: 50 }),
  });
  const deliveries = query.data?.items ?? [];

  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>Deliveries</DialogTitle>
          <DialogDescription>
            <span className="block truncate font-mono text-caption text-foreground" title={endpoint.url}>
              {endpoint.url}
            </span>
            Delivery attempts, newest first.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="gap-2">
          {query.isLoading ? (
            <RowsSkeleton label="Loading deliveries" />
          ) : query.isError ? (
            <ErrorBanner error={query.error} context={{ action: "load deliveries" }} onRetry={() => void query.refetch()} />
          ) : deliveries.length === 0 ? (
            <EmptyState
              variant="plain"
              icon={SendIcon}
              title="No deliveries yet"
              description="Use Test to send a sample event to this endpoint."
            />
          ) : (
            <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
              {deliveries.map((delivery) => (
                <DeliveryRow key={delivery.id} delivery={delivery} endpointId={endpoint.id} />
              ))}
            </ul>
          )}
        </DialogBody>
        <DialogFooter showCloseButton />
      </DialogContent>
    </Dialog>
  );
}

function DeliveryRow({ delivery, endpointId }: { delivery: WebhookDeliveryOut; endpointId: string }) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = React.useState(false);

  async function redeliver() {
    setBusy(true);
    try {
      await api.post(`webhooks/deliveries/${delivery.id}/redeliver`);
      toast.success("Redelivery queued");
      queryClient.invalidateQueries({ queryKey: ["settings", "webhooks", endpointId, "deliveries"] });
    } catch (err) {
      toast.error("Couldn't queue the redelivery", { description: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="flex items-center justify-between gap-3 px-3.5 py-3">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate font-mono text-label font-medium text-foreground">{delivery.event_type}</span>
          <LifecycleBadge state={delivery.status ?? "pending"} size="sm" />
        </div>
        <div className="text-caption text-text-secondary tabular-nums">
          {delivery.last_status_code ? `HTTP ${delivery.last_status_code} · ` : ""}
          Attempt {delivery.attempt}
          {delivery.created_at ? (
            <>
              {" · "}
              <RelativeTime iso={delivery.created_at} />
            </>
          ) : null}
        </div>
        {delivery.last_error ? (
          // The endpoint's own answer (the person's system), kept as evidence for debugging it.
          <div className="line-clamp-2 text-caption break-words text-destructive-text" title={delivery.last_error}>
            {delivery.last_error}
          </div>
        ) : null}
      </div>
      {delivery.status === "failed" || delivery.status === "dead" ? (
        <Button type="button" size="sm" onClick={() => void redeliver()} busy={busy} busyLabel="Queuing…">
          Redeliver
        </Button>
      ) : null}
    </li>
  );
}
