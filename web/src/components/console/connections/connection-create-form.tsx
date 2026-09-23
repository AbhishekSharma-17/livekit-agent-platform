"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { CloudIcon, ServerIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { CapabilityList } from "@/components/console/connections/capability-list";
import { slugify } from "@/components/console/connections/connection-model";
import { errorMessage } from "@/components/console/shared/error-banner";
import { Field } from "@/components/shared/field";
import { Icon } from "@/components/shared/icon";
import { StatusChip } from "@/components/shared/status-chip";
import { useCreateConnection, useTestUnsavedConnection } from "@/hooks/useConnections";
import { cn } from "@/lib/utils";
import type {
  ConnectionCreate,
  ConnectionTestResult,
} from "@/contracts/lkap-contracts";

type DeploymentType = NonNullable<ConnectionCreate["deployment_type"]>;
type DeploymentMode = NonNullable<ConnectionCreate["deployment_mode"]>;
type WorkerImage = NonNullable<ConnectionCreate["worker_image"]>;

const DEPLOYMENT_TYPES: { value: DeploymentType; title: string; hint: string; icon: typeof CloudIcon }[] = [
  { value: "cloud", title: "LiveKit Cloud", hint: "A project url + key/secret from cloud.livekit.io.", icon: CloudIcon },
  { value: "self_hosted", title: "Self-hosted", hint: "Your own `livekit-server` url + key/secret.", icon: ServerIcon },
];

const DEPLOYMENT_MODES: { value: DeploymentMode; title: string; hint: string; disabledFor?: DeploymentType }[] = [
  { value: "external", title: "External", hint: "You run the worker yourself (dev, or your own process manager)." },
  { value: "supervised", title: "Supervised", hint: "LKAP's supervisor starts and restarts a pool of workers for you." },
  { value: "cloud_hosted", title: "Cloud-hosted", hint: "Deploy the worker to LiveKit Cloud with the `lk` CLI.", disabledFor: "self_hosted" },
];

interface FormState {
  name: string;
  slugTouched: boolean;
  slug: string;
  deployment_type: DeploymentType;
  url: string;
  api_key: string;
  api_secret: string;
  agent_name: string;
  use_inference: boolean;
  worker_image: WorkerImage;
  deployment_mode: DeploymentMode;
  replicas: number;
}

const INITIAL: FormState = {
  name: "",
  slugTouched: false,
  slug: "",
  deployment_type: "cloud",
  url: "",
  api_key: "",
  api_secret: "",
  agent_name: "lkap-agent",
  use_inference: true,
  worker_image: "slim",
  deployment_mode: "external",
  replicas: 1,
};

function toCreatePayload(state: FormState): ConnectionCreate {
  return {
    name: state.name.trim(),
    slug: state.slug.trim() || slugify(state.name),
    deployment_type: state.deployment_type,
    url: state.url.trim(),
    api_key: state.api_key,
    api_secret: state.api_secret,
    agent_name: state.agent_name.trim() || "lkap-agent",
    use_inference: state.deployment_type === "cloud" ? state.use_inference : false,
    worker_image: state.worker_image,
    deployment_mode: state.deployment_mode,
    replicas: state.replicas,
  };
}

/**
 * `/console/connections/new` (UI_UX_SPEC-V2-AMENDMENTS §2.1): "Test
 * connection" (`POST /v1/connections/test`, nothing stored) must pass before
 * Save unlocks — the create form's own gate, independent of the api's
 * validation. Never auto-runs: the constraint against testing/creating the
 * user's live default connection through the UI during a capture only
 * matters for buttons a script could accidentally trigger, and this one only
 * fires on an explicit click with real typed-in credentials.
 */
export function ConnectionCreateForm() {
  const router = useRouter();
  const [state, setState] = React.useState<FormState>(INITIAL);
  const [testResult, setTestResult] = React.useState<ConnectionTestResult | null>(null);
  const [testedPayloadKey, setTestedPayloadKey] = React.useState<string | null>(null);
  const [errors, setErrors] = React.useState<Record<string, string>>({});

  const testMutation = useTestUnsavedConnection();
  const createMutation = useCreateConnection();

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setState((prev) => ({ ...prev, [key]: value }));
  }

  const payload = toCreatePayload(state);
  const payloadKey = JSON.stringify(payload);
  const testPassed = testResult?.ok === true && testedPayloadKey === payloadKey;

  function validate(): boolean {
    const next: Record<string, string> = {};
    if (state.name.trim() === "") next.name = "Give the connection a name.";
    if (state.url.trim() === "") next.url = "The LiveKit url is required.";
    if (state.api_key.trim() === "") next.api_key = "The API key is required.";
    if (state.api_secret.trim() === "") next.api_secret = "The API secret is required.";
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  async function runTest() {
    if (!validate()) return;
    try {
      const result = await testMutation.mutateAsync(payload);
      setTestResult(result);
      setTestedPayloadKey(payloadKey);
      if (!result.ok) toast.error(result.message);
    } catch (error) {
      const message = errorMessage(error);
      setTestResult({ ok: false, message, capabilities: {} });
      setTestedPayloadKey(payloadKey);
      toast.error(message);
    }
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!validate() || !testPassed) return;
    try {
      const connection = await createMutation.mutateAsync(payload);
      toast.success(`${connection.name} created.`);
      router.push(`/console/connections/${connection.id}`);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex max-w-[720px] flex-col gap-6" noValidate>
      <fieldset className="m-0 flex flex-col gap-3 border-0 p-0">
        <legend className="mb-1 text-sm font-medium text-foreground">Type</legend>
        <div role="radiogroup" aria-label="Deployment type" className="grid gap-2 sm:grid-cols-2">
          {DEPLOYMENT_TYPES.map((option) => (
            <label
              key={option.value}
              className={cn(
                "relative flex cursor-pointer gap-3 rounded-lg border border-border bg-card p-4",
                "transition-colors duration-(--dur-2) hover:bg-accent",
                "has-[:checked]:border-brand-line has-[:checked]:bg-brand-soft",
              )}
            >
              <input
                type="radio"
                name="deployment_type"
                value={option.value}
                checked={state.deployment_type === option.value}
                onChange={() => update("deployment_type", option.value)}
                className="sr-only"
              />
              <Icon as={option.icon} size="md" className="mt-0.5 shrink-0 text-muted-foreground" />
              <span className="flex flex-col gap-0.5">
                <span className="text-sm font-semibold text-foreground">{option.title}</span>
                <span className="text-[0.8125rem] text-pretty text-muted-foreground">{option.hint}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      <Field label="Name" htmlFor="conn-name" required error={errors.name}>
        <Input
          id="conn-name"
          value={state.name}
          onChange={(event) => {
            const name = event.target.value;
            setState((prev) => ({ ...prev, name, slug: prev.slugTouched ? prev.slug : slugify(name) }));
          }}
          placeholder="Production"
        />
      </Field>

      <Field label="Slug" htmlFor="conn-slug" hint="Used in urls; letters, numbers and dashes.">
        <Input
          id="conn-slug"
          value={state.slug}
          onChange={(event) => {
            update("slug", event.target.value);
            update("slugTouched", true);
          }}
          className="font-mono text-[0.8125rem]"
        />
      </Field>

      <Field label="URL" htmlFor="conn-url" required error={errors.url} hint="wss:// (Cloud) or ws(s):// (self-hosted).">
        <Input
          id="conn-url"
          value={state.url}
          onChange={(event) => update("url", event.target.value)}
          placeholder="wss://my-project.livekit.cloud"
          className="font-mono text-[0.8125rem]"
        />
      </Field>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="API key" htmlFor="conn-key" required error={errors.api_key}>
          <Input
            id="conn-key"
            value={state.api_key}
            onChange={(event) => update("api_key", event.target.value)}
            autoComplete="off"
            className="font-mono text-[0.8125rem]"
          />
        </Field>
        <Field label="API secret" htmlFor="conn-secret" required error={errors.api_secret}>
          <Input
            id="conn-secret"
            type="password"
            value={state.api_secret}
            onChange={(event) => update("api_secret", event.target.value)}
            autoComplete="new-password"
            className="font-mono text-[0.8125rem]"
          />
        </Field>
      </div>

      <Field
        label="Agent name"
        htmlFor="conn-agent-name"
        hint="Must be unique inside this LiveKit project."
      >
        <Input id="conn-agent-name" value={state.agent_name} onChange={(event) => update("agent_name", event.target.value)} className="font-mono text-[0.8125rem]" />
      </Field>

      {state.deployment_type === "cloud" ? (
        <Field label="Use LiveKit Inference" htmlFor="conn-inference" inline hint="Lets STT/LLM/TTS slots run with no vendor key.">
          <Switch id="conn-inference" checked={state.use_inference} onCheckedChange={(next) => update("use_inference", next)} />
        </Field>
      ) : null}

      <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
        <legend className="mb-1 text-sm font-medium text-foreground">Worker image</legend>
        <div role="radiogroup" aria-label="Worker image" className="grid gap-2 sm:grid-cols-2">
          {(["slim", "full"] as const).map((image) => (
            <label
              key={image}
              className={cn(
                "flex cursor-pointer flex-col gap-0.5 rounded-md border border-border bg-background px-3 py-2.5",
                "has-[:checked]:border-brand-line has-[:checked]:bg-brand-soft",
              )}
            >
              <span className="flex items-center gap-2">
                <input
                  type="radio"
                  name="worker_image"
                  value={image}
                  checked={state.worker_image === image}
                  onChange={() => update("worker_image", image)}
                  className="sr-only"
                />
                <span className="text-sm font-medium capitalize text-foreground">{image}</span>
              </span>
              <span className="text-xs text-muted-foreground">
                {image === "slim" ? "The v1 provider set + Inference." : "Every available provider (larger image, more native deps)."}
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
        <legend className="mb-1 text-sm font-medium text-foreground">Deployment mode</legend>
        <div role="radiogroup" aria-label="Deployment mode" className="grid gap-2 sm:grid-cols-3">
          {DEPLOYMENT_MODES.map((mode) => {
            const disabled = mode.disabledFor === state.deployment_type;
            return (
              <label
                key={mode.value}
                className={cn(
                  "flex flex-col gap-0.5 rounded-md border border-border bg-background px-3 py-2.5",
                  disabled ? "cursor-not-allowed opacity-50" : "cursor-pointer has-[:checked]:border-brand-line has-[:checked]:bg-brand-soft",
                )}
              >
                <input
                  type="radio"
                  name="deployment_mode"
                  value={mode.value}
                  checked={state.deployment_mode === mode.value}
                  disabled={disabled}
                  onChange={() => update("deployment_mode", mode.value)}
                  className="sr-only"
                />
                <span className="text-sm font-medium text-foreground">{mode.title}</span>
                <span className="text-xs text-pretty text-muted-foreground">{mode.hint}</span>
              </label>
            );
          })}
        </div>
      </fieldset>

      <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold text-foreground">Test connection</h2>
            <p className="text-xs text-muted-foreground">Runs before Save unlocks — nothing is stored yet.</p>
          </div>
          <Button type="button" variant="outline" onClick={() => void runTest()} disabled={testMutation.isPending}>
            {testMutation.isPending ? "Testing…" : "Test connection"}
          </Button>
        </div>
        {testResult ? (
          <div className="flex flex-col gap-3 border-t border-border pt-3">
            <StatusChip tone={testResult.ok ? "success" : "danger"} dot>
              {testResult.ok ? "Connection OK" : "Connection failed"}
            </StatusChip>
            <p className="text-[0.8125rem] text-pretty text-muted-foreground">{testResult.message}</p>
            {testResult.ok ? <CapabilityList capabilities={testResult.capabilities} /> : null}
          </div>
        ) : null}
      </div>

      <div className="flex items-center justify-end gap-2">
        <Button type="button" variant="outline" onClick={() => router.push("/console/connections")}>
          Cancel
        </Button>
        <Button type="submit" disabled={!testPassed || createMutation.isPending}>
          {createMutation.isPending ? "Creating…" : "Create connection"}
        </Button>
      </div>
      {!testPassed && testResult ? (
        <p className="text-right text-xs text-muted-foreground">
          {testedPayloadKey !== payloadKey ? "Details changed since the last test — test again to enable Save." : "Save unlocks once the test passes."}
        </p>
      ) : null}
    </form>
  );
}
