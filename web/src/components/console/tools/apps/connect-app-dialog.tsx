"use client";

import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
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
import { CopyButton } from "@/components/shared/copy-button";
import { Field } from "@/components/shared/field";
import { StatusChip } from "@/components/shared/status-chip";
import { useAgents, useConnectApp, useReconnectApp, useToolProviderConnection, useToolProviderToolkit } from "@/components/console/lib/api-hooks";
import { appsErrorMessage } from "@/components/console/tools/apps/use-composio";
import type { AuthOption, ConnectMethod, SubjectKind } from "@/components/console/tools/apps/types";
import type { AppAuthField, ToolkitOut } from "@/contracts/lkap-contracts";

const METHOD_LABEL: Record<ConnectMethod, string> = {
  managed: "Managed — one click",
  custom_oauth: "Your own OAuth app",
  api_key: "API key",
  none: "No sign-in needed",
};

const KEY_LIKE: AuthOption[] = ["api_key", "bearer", "basic"];

/** Which `ConnectMethod`s a toolkit's `auth` options offer, best first (D-V5-C4). */
export function methodsFor(toolkit: Pick<ToolkitOut, "auth">): ConnectMethod[] {
  const auth = toolkit.auth ?? [];
  const methods: ConnectMethod[] = [];
  if (auth.includes("oauth_managed")) methods.push("managed");
  if (auth.includes("oauth_custom")) methods.push("custom_oauth");
  if (auth.some((option) => KEY_LIKE.includes(option as AuthOption))) methods.push("api_key");
  if (auth.includes("none")) methods.push("none");
  return methods;
}

/** The Connect dialog's fields for one method, from `ToolkitOut.auth_fields`. */
export function fieldsFor(toolkit: Pick<ToolkitOut, "auth_fields">, method: ConnectMethod): AppAuthField[] {
  const authFields = toolkit.auth_fields ?? {};
  if (method === "custom_oauth") return authFields.oauth_custom ?? [];
  if (method === "api_key") {
    for (const option of KEY_LIKE) {
      const fields = authFields[option];
      if (fields) return fields;
    }
  }
  return [];
}

export interface ConnectAppDialogProps {
  toolkit: { slug: string; name: string };
  trigger?: React.ReactNode;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  onConnected?: () => void;
}

/**
 * Connect dialog (docs/v5/COMPOSIO.md §6, D-V5-C4): the method radio (best
 * first), that method's fields, the workspace/agent choice, then either an
 * immediate result (API key, none) or a new-tab sign-in with status polling
 * (managed, your own OAuth app). Fields are forwarded to Composio once and
 * never rendered again once submitted (`AppConnectIn.fields`, write-only).
 */
export function ConnectAppDialog({ toolkit, trigger, open: openProp, onOpenChange, onConnected }: ConnectAppDialogProps) {
  const [openState, setOpenState] = React.useState(false);
  const open = openProp ?? openState;

  const queryClient = useQueryClient();
  const detailQuery = useToolProviderToolkit(open ? toolkit.slug : null);
  const agentsQuery = useAgents();
  const connectMutation = useConnectApp();
  const reconnectMutation = useReconnectApp();

  // `""` rather than `null` for "no method chosen yet": `RadioGroup value=`
  // must stay a defined string across renders (React warns on an
  // uncontrolled-to-controlled switch otherwise, since `undefined` and a
  // real value are different control modes for the same prop).
  const [method, setMethod] = React.useState<ConnectMethod | "">("");
  const [subject, setSubject] = React.useState<SubjectKind>("workspace");
  const [agentId, setAgentId] = React.useState("");
  const [fieldValues, setFieldValues] = React.useState<Record<string, string>>({});
  const [pendingId, setPendingId] = React.useState<string | null>(null);
  const [redirectUrl, setRedirectUrl] = React.useState<string | null>(null);

  const availableMethods = React.useMemo(() => (detailQuery.data ? methodsFor(detailQuery.data) : []), [detailQuery.data]);
  const fields = React.useMemo(
    () => (detailQuery.data && method ? fieldsFor(detailQuery.data, method) : []),
    [detailQuery.data, method],
  );

  React.useEffect(() => {
    if (availableMethods.length > 0 && !availableMethods.includes(method as ConnectMethod)) {
      setMethod(availableMethods[0]);
    }
  }, [availableMethods, method]);

  function reset() {
    setMethod("");
    setSubject("workspace");
    setAgentId("");
    setFieldValues({});
    setPendingId(null);
    setRedirectUrl(null);
  }

  function setOpen(next: boolean) {
    setOpenState(next);
    onOpenChange?.(next);
    if (!next) reset();
  }

  const pendingQuery = useToolProviderConnection(pendingId, { poll: true });
  const pendingStatus = pendingQuery.data?.status ?? null;

  React.useEffect(() => {
    if (pendingStatus === "active") {
      toast.success(`${toolkit.name} connected`);
      // The managed/custom-OAuth path reaches "active" through polling, not
      // a mutation's own `onSuccess` — nothing else refreshes the gallery's
      // `AppCard` (which reads `toolkit.connected` from the *toolkits*
      // list) without this.
      void queryClient.invalidateQueries({ queryKey: ["apps"] });
      onConnected?.();
      setOpen(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingStatus]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    event.stopPropagation();
    if (!method) return;
    if (subject === "agent" && !agentId) {
      toast.error("Choose an agent for this connection.");
      return;
    }
    try {
      const result = await connectMutation.mutateAsync({
        toolkit: toolkit.slug,
        method,
        subject,
        agent_id: subject === "agent" ? agentId : null,
        fields: method === "custom_oauth" || method === "api_key" ? fieldValues : {},
      });
      setFieldValues({});
      if (result.redirect_url) {
        window.open(result.redirect_url, "_blank", "noopener");
        setRedirectUrl(result.redirect_url);
      }
      setPendingId(result.connection_id);
      if (result.status === "active") {
        toast.success(`${toolkit.name} connected`);
        onConnected?.();
        setOpen(false);
      } else if (result.status === "failed" || result.status === "expired" || result.status === "inactive") {
        toast.error(`Couldn't connect ${toolkit.name} — try again.`);
      }
    } catch (error) {
      toast.error(`Couldn't connect — ${appsErrorMessage(error)}`);
    }
  }

  async function retry() {
    if (!pendingId) return;
    try {
      const result = await reconnectMutation.mutateAsync({ id: pendingId });
      if (result.redirect_url) {
        window.open(result.redirect_url, "_blank", "noopener");
        setRedirectUrl(result.redirect_url);
      }
    } catch (error) {
      toast.error(`Couldn't reconnect — ${appsErrorMessage(error)}`);
    }
  }

  const waiting = pendingId !== null && pendingStatus !== null && pendingStatus !== "active";
  const failed = waiting && pendingStatus !== "initiated";

  const content = (
    <DialogContent size="md" aria-describedby="connect-app-description">
      <form onSubmit={(event) => void submit(event)} className="flex min-h-0 flex-1 flex-col" noValidate>
        <DialogHeader>
          <DialogTitle>Connect {toolkit.name}</DialogTitle>
          <DialogDescription id="connect-app-description">
            Agents can use {toolkit.name} once it&apos;s connected — pick actions for them in Actions afterwards.
          </DialogDescription>
        </DialogHeader>

        <DialogBody>
          {waiting ? (
            <div className="flex flex-col gap-3">
              {failed ? (
                <>
                  <StatusChip tone="danger" dot size="sm">
                    Failed
                  </StatusChip>
                  <p className="text-[0.8125rem] text-muted-foreground">
                    The sign-in didn&apos;t finish. Try again.
                  </p>
                  <div>
                    <Button type="button" variant="outline" size="sm" onClick={() => void retry()}>
                      Retry
                    </Button>
                  </div>
                </>
              ) : (
                <>
                  <StatusChip tone="info" dot size="sm">
                    Waiting for sign-in…
                  </StatusChip>
                  <p className="text-[0.8125rem] text-muted-foreground">
                    Finish signing in in the tab that just opened, then come back here.
                  </p>
                  {redirectUrl ? (
                    <a
                      href={redirectUrl}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="text-[0.8125rem] font-medium text-foreground underline underline-offset-2"
                    >
                      Open sign-in page again
                    </a>
                  ) : null}
                </>
              )}
            </div>
          ) : detailQuery.isLoading ? (
            <p className="text-[0.8125rem] text-muted-foreground">Loading…</p>
          ) : !detailQuery.data ? (
            <p className="text-[0.8125rem] text-danger-text">Couldn&apos;t load this app&apos;s connect options.</p>
          ) : (
            <>
              <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
                <legend className="mb-1 text-sm font-medium text-foreground">How to connect</legend>
                <RadioGroup value={method} onValueChange={(value) => setMethod(value as ConnectMethod)}>
                  {availableMethods.map((option) => (
                    <Label
                      key={option}
                      htmlFor={`connect-method-${option}`}
                      className={`flex cursor-pointer items-center gap-2 rounded-md border p-2.5 text-sm font-normal ${
                        method === option ? "border-primary bg-muted/50" : "border-border"
                      }`}
                    >
                      <RadioGroupItem id={`connect-method-${option}`} value={option} />
                      {METHOD_LABEL[option]}
                    </Label>
                  ))}
                </RadioGroup>
              </fieldset>

              {method === "managed" ? (
                <p className="text-[0.8125rem] text-pretty text-muted-foreground">
                  Composio&apos;s shared sign-in includes 20,000 calls a month, then a small per-call fee.
                </p>
              ) : null}

              {method === "custom_oauth" && detailQuery.data.oauth_redirect_uri ? (
                <div className="flex flex-col gap-1.5 rounded-md border border-border p-2.5">
                  <span className="text-[0.8125rem] font-medium text-foreground">Redirect URI to register</span>
                  <div className="flex items-center gap-1">
                    <code className="min-w-0 truncate font-mono text-xs text-muted-foreground">
                      {detailQuery.data.oauth_redirect_uri}
                    </code>
                    <CopyButton value={detailQuery.data.oauth_redirect_uri} label="Copy redirect URI" size="xs" />
                  </div>
                  {detailQuery.data.auth_guide_url ? (
                    <a
                      href={detailQuery.data.auth_guide_url}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="text-[0.8125rem] font-medium text-foreground underline underline-offset-2"
                    >
                      Set up your OAuth app
                    </a>
                  ) : null}
                </div>
              ) : null}

              {fields.map((field) => (
                <Field key={field.name} label={field.label} htmlFor={`connect-field-${field.name}`} required={field.required}>
                  <Input
                    id={`connect-field-${field.name}`}
                    type={field.secret ? "password" : "text"}
                    autoComplete={field.secret ? "new-password" : "off"}
                    value={fieldValues[field.name] ?? ""}
                    onChange={(event) => setFieldValues((prev) => ({ ...prev, [field.name]: event.target.value }))}
                  />
                </Field>
              ))}

              <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
                <legend className="mb-1 text-sm font-medium text-foreground">Who can use it</legend>
                <RadioGroup value={subject} onValueChange={(value) => setSubject(value as SubjectKind)} className="flex flex-col gap-2">
                  <Label htmlFor="connect-subject-workspace" className="flex cursor-pointer items-center gap-2 text-sm font-normal">
                    <RadioGroupItem id="connect-subject-workspace" value="workspace" />
                    For this workspace
                  </Label>
                  <Label htmlFor="connect-subject-agent" className="flex cursor-pointer items-center gap-2 text-sm font-normal">
                    <RadioGroupItem id="connect-subject-agent" value="agent" />
                    For one agent
                  </Label>
                </RadioGroup>
                {subject === "agent" ? (
                  <Select value={agentId || undefined} onValueChange={setAgentId}>
                    <SelectTrigger className="w-full" aria-label="Agent">
                      <SelectValue placeholder={agentsQuery.isLoading ? "Loading agents…" : "Choose an agent"} />
                    </SelectTrigger>
                    <SelectContent>
                      {(agentsQuery.data?.items ?? []).map((agent) => (
                        <SelectItem key={agent.id} value={agent.id}>
                          {agent.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                ) : null}
              </fieldset>
            </>
          )}
        </DialogBody>

        <DialogFooter>
          {waiting ? (
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Close
            </Button>
          ) : (
            <>
              <Button type="button" variant="outline" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={!method || connectMutation.isPending}>
                {connectMutation.isPending ? "Connecting…" : "Connect"}
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
