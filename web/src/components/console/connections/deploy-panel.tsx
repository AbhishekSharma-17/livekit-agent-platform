"use client";

import * as React from "react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CopyButton } from "@/components/shared/copy-button";
import { errorMessage } from "@/components/console/shared/error-banner";
import { downloadDeployBundle, fetchWorkerEnv, type WorkerEnvFormat } from "@/hooks/useConnections";
import type { ConnectionOut } from "@/contracts/lkap-contracts";
import { SkeletonRows } from "@/components/shared/loading-state";

const FORMATS: { value: WorkerEnvFormat; label: string }[] = [
  { value: "env", label: ".env" },
  { value: "compose", label: "docker compose" },
  { value: "lk", label: "lk CLI" },
];

/**
 * Detail → Deploy tab (UI_UX_SPEC-V2-AMENDMENTS §2.1): three copyable
 * snippets for `external` (secrets redacted — the api never returns them,
 * see `worker_env_template`), or the deploy bundle download for
 * `cloud_hosted`. Never fetched eagerly (only on tab open / button click),
 * so a screenshot capture that merely navigates here never mutates or
 * downloads anything.
 */
export function DeployPanel({ connection }: { connection: ConnectionOut }) {
  if (connection.deployment_mode === "cloud_hosted") {
    return <CloudHostedBundle connection={connection} />;
  }
  return <WorkerEnvSnippets connectionId={connection.id} />;
}

function WorkerEnvSnippets({ connectionId }: { connectionId: string }) {
  const [format, setFormat] = React.useState<WorkerEnvFormat>("env");
  const [text, setText] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [errorText, setErrorText] = React.useState<string | null>(null);

  const load = React.useCallback(
    async (next: WorkerEnvFormat) => {
      setLoading(true);
      setErrorText(null);
      try {
        setText(await fetchWorkerEnv(connectionId, next));
      } catch (error) {
        setErrorText(errorMessage(error));
      } finally {
        setLoading(false);
      }
    },
    [connectionId],
  );

  React.useEffect(() => {
    void load(format);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `load` is stable per `connectionId`
  }, [format, connectionId]);

  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-muted-foreground">
        What an <span className="font-mono">external</span> worker for this connection needs. Secrets are shown as{" "}
        <span className="font-mono">{"<NAME>"}</span> placeholders — the api never returns them.
      </p>
      <Tabs value={format} onValueChange={(next) => setFormat(next as WorkerEnvFormat)}>
        <TabsList>
          {FORMATS.map((f) => (
            <TabsTrigger key={f.value} value={f.value}>
              {f.label}
            </TabsTrigger>
          ))}
        </TabsList>
        {FORMATS.map((f) => (
          <TabsContent key={f.value} value={f.value} className="pt-3">
            {loading ? (
              <SkeletonRows label="Loading the deploy snippet" rows={4} rowClassName="h-5" />
            ) : errorText ? (
              <Alert variant="danger">
                <AlertDescription>{errorText}</AlertDescription>
              </Alert>
            ) : (
              <div className="relative">
                <pre className="max-h-96 overflow-auto rounded-md border border-border bg-muted/40 p-4 font-mono text-xs leading-5 text-foreground">
                  {text ?? ""}
                </pre>
                <CopyButton value={text ?? ""} label="Copy snippet" className="absolute top-2 right-2" />
              </div>
            )}
          </TabsContent>
        ))}
      </Tabs>
    </div>
  );
}

function CloudHostedBundle({ connection }: { connection: ConnectionOut }) {
  const [pending, setPending] = React.useState(false);

  async function download() {
    setPending(true);
    try {
      await downloadDeployBundle(connection.id, `lkap-${connection.slug}-bundle`);
      toast.success("Bundle downloaded.");
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {connection.deployment_type !== "cloud" ? (
        <Alert variant="warning">
          <AlertDescription>Cloud-hosted deploys need a LiveKit Cloud connection.</AlertDescription>
        </Alert>
      ) : null}
      <p className="text-sm text-muted-foreground">
        A zip with <span className="font-mono">livekit.toml</span>, <span className="font-mono">secrets.env</span>{" "}
        (placeholders, not real secrets) and the <span className="font-mono">lk agent</span> commands to deploy a
        worker for this connection on LiveKit Cloud. Requires a public HTTPS api url
        (<span className="font-mono">LKAP_PUBLIC_BASE_URL</span>).
      </p>
      <div>
        <Button type="button" onClick={() => void download()} disabled={pending || connection.deployment_type !== "cloud"}>
          {pending ? "Building…" : "Download deploy bundle"}
        </Button>
      </div>
    </div>
  );
}
