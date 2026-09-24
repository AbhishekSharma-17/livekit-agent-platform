"use client";

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { PlusIcon, SendIcon, WebhookIcon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { Checkbox } from "@/components/ui/checkbox";
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
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { CopyButton } from "@/components/shared/copy-button";
import { EmptyState } from "@/components/shared/empty-state";
import { Field } from "@/components/shared/field";
import { RelativeTime } from "@/components/shared/relative-time";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { Section, SectionRow } from "@/components/shared/section";
import { StatusChip, type StatusTone } from "@/components/shared/status-chip";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { api, ApiError } from "@/lib/api";
import type {
  WebhookDeliveryOut,
  WebhookDeliveryPage,
  WebhookEndpointOut,
  WebhookEndpointPage,
} from "@/contracts/lkap-contracts";
import { KNOWN_WEBHOOK_EVENTS, WEBHOOK_EVENT_LABEL, type WebhookEndpointCreated } from "./api-types";
import { SkeletonRows } from "@/components/shared/loading-state";
import { RequireWrite } from "@/components/shared/require-write";

function useWebhooks() {
  return useQuery({
    queryKey: ["settings", "webhooks"] as const,
    queryFn: () => api.get<WebhookEndpointPage>("webhooks", { limit: 200 }),
  });
}

const DELIVERY_TONE: Record<NonNullable<WebhookDeliveryOut["status"]>, StatusTone> = {
  pending: "info",
  delivered: "success",
  failed: "warning",
  dead: "danger",
};

/**
 * `/v1/webhooks` needs `admin` server-side for both reads and writes
 * (`auth/roles.py::ROUTE_POLICY`) — unlike every other settings tab, a
 * builder can't even list endpoints here, so the gate wraps the whole tab
 * rather than just its mutating buttons (docs/v2/_asks.md V2-20-5): no point
 * mounting a query that only ever 403s.
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
  const endpoints = query.data?.items ?? [];
  const [deliveriesFor, setDeliveriesFor] = React.useState<WebhookEndpointOut | null>(null);
  const [editing, setEditing] = React.useState<WebhookEndpointOut | null>(null);

  const columns: ResponsiveTableColumn<WebhookEndpointOut>[] = [
    {
      id: "endpoint",
      header: "Endpoint",
      cell: (endpoint) => (
        <div className="min-w-0">
          <div className="truncate font-mono text-sm text-foreground">{endpoint.url}</div>
          {endpoint.description ? (
            <div className="truncate text-xs text-muted-foreground">{endpoint.description}</div>
          ) : null}
        </div>
      ),
    },
    {
      id: "events",
      header: "Events",
      cell: (endpoint) => (
        <span className="text-xs text-muted-foreground">
          {endpoint.events && endpoint.events.length > 0
            ? endpoint.events.map((e) => WEBHOOK_EVENT_LABEL[e] ?? e).join(", ")
            : "All events"}
        </span>
      ),
    },
    {
      id: "status",
      header: "Status",
      cell: (endpoint) => (
        <StatusChip tone={endpoint.enabled ? "success" : "neutral"} size="sm">
          {endpoint.enabled ? "Enabled" : "Disabled"}
        </StatusChip>
      ),
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "end",
      interactive: true,
      cell: (endpoint) => (
        <div className="flex items-center justify-end gap-1">
          <Button type="button" variant="ghost" size="sm" onClick={() => setDeliveriesFor(endpoint)}>
            Deliveries
          </Button>
          <TestButton endpoint={endpoint} />
          <Button type="button" variant="ghost" size="sm" onClick={() => setEditing(endpoint)}>
            Edit
          </Button>
          <DeleteButton endpoint={endpoint} onDeleted={invalidate} />
        </div>
      ),
    },
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
          <SkeletonRows label="Loading webhooks" rowClassName="h-12" />
        ) : query.isError ? (
          <ErrorBanner message={`Couldn't load webhooks — ${errorMessage(query.error)}`} onRetry={() => query.refetch()} />
        ) : endpoints.length === 0 ? (
          <EmptyState icon={WebhookIcon} title="No webhooks yet" compact />
        ) : (
          <ResponsiveTable<WebhookEndpointOut>
            columns={columns}
            rows={endpoints}
            label="Webhook endpoints"
            getRowKey={(endpoint) => endpoint.id}
            renderCard={(endpoint) => (
              <div className="space-y-1">
                <div className="truncate font-mono text-sm text-foreground">{endpoint.url}</div>
                <StatusChip tone={endpoint.enabled ? "success" : "neutral"} size="sm">
                  {endpoint.enabled ? "Enabled" : "Disabled"}
                </StatusChip>
              </div>
            )}
          />
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

      {deliveriesFor ? (
        <DeliveriesDialog endpoint={deliveriesFor} onClose={() => setDeliveriesFor(null)} />
      ) : null}
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
    <div className="space-y-2">
      <Label>Events</Label>
      <p className="text-xs text-muted-foreground">Leave every box unchecked to subscribe to all events.</p>
      <div className="grid grid-cols-2 gap-2">
        {KNOWN_WEBHOOK_EVENTS.map((event) => (
          <label key={event} className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={selected.has(event)}
              disabled={disabled}
              onCheckedChange={() => {
                const next = new Set(selected);
                if (next.has(event)) next.delete(event);
                else next.add(event);
                onChange(next);
              }}
            />
            <span className="text-xs">{WEBHOOK_EVENT_LABEL[event] ?? event}</span>
          </label>
        ))}
      </div>
    </div>
  );
}

function CreateEndpointDialog({ onCreated }: { onCreated: () => void }) {
  const [open, setOpen] = React.useState(false);
  const [url, setUrl] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [events, setEvents] = React.useState<Set<string>>(new Set());
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
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
      setError(err instanceof ApiError ? err.message : errorMessage(err));
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
        <Button type="button" size="sm">
          <PlusIcon />
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
              <div className="flex items-center gap-2 rounded-md border border-border bg-muted/50 p-2">
                <code className="min-w-0 flex-1 font-mono text-xs break-all">{created.secret}</code>
                <CopyButton value={created.secret} label="Copy signing secret" />
              </div>
              <p className="text-xs text-muted-foreground">
                Verify deliveries with the <code className="font-mono">X-LKAP-Signature</code> header and this secret.
                It won&apos;t be shown again.
              </p>
            </DialogBody>
            <DialogFooter showCloseButton />
          </>
        ) : (
          <form onSubmit={onSubmit} className="flex min-h-0 flex-1 flex-col">
            <DialogBody className="gap-4">
              {error ? <ErrorBanner message={error} /> : null}
              <Field label="URL" htmlFor="webhook-url" required hint="https:// (http:// only in dev).">
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
                <Input id="webhook-description" value={description} onChange={(e) => setDescription(e.target.value)} disabled={saving} />
              </Field>
              <EventsPicker selected={events} onChange={setEvents} disabled={saving} />
            </DialogBody>
            <DialogFooter>
              <Button type="submit" disabled={saving}>
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
  const [error, setError] = React.useState<string | null>(null);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await api.put(`webhooks/${endpoint.id}`, { url, description, events: [...events], enabled });
      toast.success("Webhook updated");
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : errorMessage(err));
      setSaving(false);
    }
  }

  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit webhook</DialogTitle>
          <DialogDescription>The signing secret never changes here — delete and recreate to rotate it.</DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} className="space-y-4">
          {error ? <ErrorBanner message={error} /> : null}
          <Field label="URL" htmlFor="webhook-edit-url" required>
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
          <Field label="Enabled" htmlFor="webhook-edit-enabled" inline>
            <Switch id="webhook-edit-enabled" checked={enabled} onCheckedChange={setEnabled} disabled={saving} />
          </Field>
          <DialogFooter>
            <Button type="submit" disabled={saving}>
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
      else toast.warning(`Test event ${delivery.status}${delivery.last_error ? ` — ${delivery.last_error}` : ""}`);
    } catch (err) {
      toast.error(`Couldn't send test event — ${errorMessage(err)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Button type="button" variant="ghost" size="sm" onClick={onClick} disabled={busy}>
      <SendIcon />
      Test
    </Button>
  );
}

function DeleteButton({ endpoint, onDeleted }: { endpoint: WebhookEndpointOut; onDeleted: () => void }) {
  const [busy, setBusy] = React.useState(false);

  async function onClick() {
    setBusy(true);
    try {
      await api.delete(`webhooks/${endpoint.id}`);
      toast.success("Webhook deleted");
      onDeleted();
    } catch (err) {
      toast.error(`Couldn't delete webhook — ${errorMessage(err)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <ConfirmDialog
      trigger={
        <Button type="button" variant="ghost" size="sm" disabled={busy}>
          Delete
        </Button>
      }
      title="Delete this webhook?"
      description={`${endpoint.url} stops receiving events. Its delivery history is deleted too.`}
      confirmLabel="Delete webhook"
      onConfirm={onClick}
    />
  );
}

/** Delivery attempts for one endpoint, in a modal (no side drawers — UI_UX_SPEC-V2-AMENDMENTS §5). */
function DeliveriesDialog({ endpoint, onClose }: { endpoint: WebhookEndpointOut; onClose: () => void }) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["settings", "webhooks", endpoint.id, "deliveries"] as const,
    queryFn: () => api.get<WebhookDeliveryPage>(`webhooks/${endpoint.id}/deliveries`, { limit: 50 }),
  });
  const deliveries = query.data?.items ?? [];

  async function redeliver(delivery: WebhookDeliveryOut) {
    try {
      await api.post(`webhooks/deliveries/${delivery.id}/redeliver`);
      toast.success("Redelivery queued");
      queryClient.invalidateQueries({ queryKey: ["settings", "webhooks", endpoint.id, "deliveries"] });
    } catch (err) {
      toast.error(`Couldn't redeliver — ${errorMessage(err)}`);
    }
  }

  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>Deliveries</DialogTitle>
          <DialogDescription>
            <span className="block truncate font-mono text-xs text-foreground" title={endpoint.url}>
              {endpoint.url}
            </span>
            Delivery attempts, newest first.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="gap-2">
          {query.isLoading ? (
            <SkeletonRows label="Loading deliveries" rowClassName="h-14" />
          ) : deliveries.length === 0 ? (
            <EmptyState icon={SendIcon} title="No deliveries yet" compact />
          ) : (
            deliveries.map((delivery) => (
              <div key={delivery.id} className="flex items-center justify-between gap-2 rounded-md border border-border p-2.5">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium text-foreground">{delivery.event_type}</span>
                    <StatusChip tone={DELIVERY_TONE[delivery.status ?? "pending"]} size="sm">
                      {delivery.status}
                    </StatusChip>
                  </div>
                  <div className="text-xs text-muted-foreground">
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
                    <div className="line-clamp-2 text-xs break-words text-danger-text" title={delivery.last_error}>
                      {delivery.last_error}
                    </div>
                  ) : null}
                </div>
                {delivery.status === "failed" || delivery.status === "dead" ? (
                  <Button type="button" variant="outline" size="sm" onClick={() => redeliver(delivery)}>
                    Redeliver
                  </Button>
                ) : null}
              </div>
            ))
          )}
        </DialogBody>
        <DialogFooter showCloseButton />
      </DialogContent>
    </Dialog>
  );
}
