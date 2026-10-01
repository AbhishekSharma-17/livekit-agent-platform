"use client";

import * as React from "react";
import { toast } from "sonner";
import { DownloadIcon } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { CopyButton } from "@/components/shared/copy-button";
import { LoadingRegion } from "@/components/shared/loading-state";
import { Section, SectionRow } from "@/components/shared/section";
import { SegmentedControl } from "@/components/shared/segmented-control";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { IfCan, ReadOnlyNote, readOnlyCopy } from "@/components/console/shared/permission";
import { downloadDeployBundle, fetchWorkerEnv, type WorkerEnvFormat } from "@/hooks/useConnections";
import type { ConnectionOut } from "@/contracts/lkap-contracts";

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
  const [loadError, setLoadError] = React.useState<unknown>(null);

  const load = React.useCallback(
    async (next: WorkerEnvFormat) => {
      setLoading(true);
      setLoadError(null);
      try {
        setText(await fetchWorkerEnv(connectionId, next));
      } catch (error) {
        setLoadError(error ?? new Error("load failed"));
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

  const label = FORMATS.find((f) => f.value === format)?.label ?? format;

  return (
    <Section
      id="connection-deploy"
      title="Worker settings"
      description="What an external worker for this connection needs. Secrets are <NAME> placeholders. The api never returns them."
    >
      <SectionRow className="flex flex-col gap-3">
        <SegmentedControl<WorkerEnvFormat> label="Snippet format" value={format} onValueChange={setFormat} options={FORMATS} />
        {loading ? (
          <LoadingRegion label="Loading the worker settings" className="flex flex-col gap-2 rounded border border-border bg-muted p-4">
            {[0, 1, 2, 3].map((index) => (
              <Skeleton key={index} className={index === 3 ? "h-3.5 w-3/5" : "h-3.5 w-full"} />
            ))}
          </LoadingRegion>
        ) : loadError ? (
          <ErrorBanner error={loadError} context={{ action: "load the worker settings" }} onRetry={() => void load(format)} />
        ) : (
          <div className="relative">
            <pre
              tabIndex={0}
              aria-label={`${label} snippet`}
              className="max-h-96 overflow-auto rounded border border-border bg-muted p-4 pr-12 font-mono text-caption leading-5 text-foreground"
            >
              {text ?? ""}
            </pre>
            <CopyButton value={text ?? ""} label={`Copy the ${label} snippet`} className="absolute top-2 right-2" />
          </div>
        )}
      </SectionRow>
    </Section>
  );
}

function CloudHostedBundle({ connection }: { connection: ConnectionOut }) {
  const [pending, setPending] = React.useState(false);
  const isCloud = connection.deployment_type === "cloud";

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
    <Section
      id="connection-deploy"
      title="Deploy bundle"
      description="Everything the lk command-line tool needs to deploy a worker for this connection on LiveKit Cloud."
    >
      <SectionRow className="flex flex-col gap-4">
        {!isCloud ? (
          <Alert tone="warning" title="This needs a LiveKit Cloud connection">
            Cloud-hosted workers only deploy to LiveKit Cloud. Change the connection&apos;s mode, or add a LiveKit Cloud
            connection.
          </Alert>
        ) : null}
        <p className="max-w-[72ch] text-label text-pretty text-text-secondary">
          A zip with <span className="font-mono">livekit.toml</span>, <span className="font-mono">secrets.env</span>{" "}
          (placeholders, not real secrets) and the <span className="font-mono">lk agent</span> commands. It needs a public
          HTTPS address for the api (<span className="font-mono">LKAP_PUBLIC_BASE_URL</span>).
        </p>
        {isCloud ? (
          <IfCan min="admin" fallback={<ReadOnlyNote>{readOnlyCopy("admin", "download the deploy bundle")}</ReadOnlyNote>}>
            <div>
              <Button type="button" onClick={() => void download()} busy={pending} busyLabel="Building…">
                <DownloadIcon aria-hidden="true" />
                Download deploy bundle
              </Button>
            </div>
          </IfCan>
        ) : null}
      </SectionRow>
    </Section>
  );
}
