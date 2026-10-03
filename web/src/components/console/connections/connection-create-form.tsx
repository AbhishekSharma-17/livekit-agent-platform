"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { CloudIcon, ServerIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { CapabilityList } from "@/components/console/connections/capability-list";
import {
  AGENT_NAME_HINT,
  agentNameError,
  slugify,
  WORKER_NEEDED_NOTE,
  WORKER_START_BY_MODE,
} from "@/components/console/connections/connection-model";
import { errorMessage } from "@/components/console/shared/error-banner";
import { OptionCard } from "@/components/shared/choice";
import { Field } from "@/components/shared/field";
import { Icon } from "@/components/shared/icon";
import { SecretInput } from "@/components/shared/password-input";
import { Section, SectionRow } from "@/components/shared/section";
import { StatusPill } from "@/components/shared/status-chip";
import { lifecycleStatus } from "@/components/shared/status-map";
import { VendorMark } from "@/components/shared/vendor-mark";
import { useCreateConnection, useTestUnsavedConnection } from "@/hooks/useConnections";
import type {
  ConnectionCreate,
  ConnectionTestResult,
} from "@/contracts/lkap-contracts";

type DeploymentType = NonNullable<ConnectionCreate["deployment_type"]>;
type DeploymentMode = NonNullable<ConnectionCreate["deployment_mode"]>;
type WorkerImage = NonNullable<ConnectionCreate["worker_image"]>;

const DEPLOYMENT_TYPES: { value: DeploymentType; title: string; hint: string; icon: typeof CloudIcon; mark?: string }[] = [
  { value: "cloud", title: "LiveKit Cloud", hint: "A project URL plus its key and secret from cloud.livekit.io.", icon: CloudIcon, mark: "LiveKit" },
  { value: "self_hosted", title: "Self-hosted", hint: "Your own LiveKit server's URL plus its key and secret.", icon: ServerIcon },
];

const DEPLOYMENT_MODES: { value: DeploymentMode; title: string; hint: string; disabledFor?: DeploymentType }[] = [
  { value: "external", title: "External", hint: "You run the worker yourself (for development, or your own process manager)." },
  { value: "supervised", title: "Supervised", hint: "LKAP starts and restarts a pool of workers for you." },
  { value: "cloud_hosted", title: "Cloud-hosted", hint: "Deploy the worker to LiveKit Cloud with the lk command-line tool.", disabledFor: "self_hosted" },
];

const WORKER_IMAGES: { value: WorkerImage; title: string; hint: string }[] = [
  { value: "slim", title: "Slim", hint: "The core provider set plus LiveKit Inference." },
  { value: "full", title: "Full", hint: "Every available provider (a larger image with more native dependencies)." },
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
 *
 * The Form archetype (docs/ui/DESIGN-SYSTEM.md section 7.4): grouped fields,
 * optional ones marked, option cards for choices that need explaining, the
 * secret write-only, and "Create connection" as the one primary, last.
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
    if (state.url.trim() === "") next.url = "Enter the LiveKit URL.";
    if (state.api_key.trim() === "") next.api_key = "Enter the API key.";
    if (state.api_secret.trim() === "") next.api_secret = "Enter the API secret.";
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  /** V6-27: a 409 `agent_name_in_use` goes under the Agent name field; `true` when it was one. */
  function showAgentNameError(error: unknown): boolean {
    const message = agentNameError(error);
    if (!message) return false;
    setErrors((prev) => ({ ...prev, agent_name: message }));
    return true;
  }

  async function runTest() {
    if (!validate()) return;
    try {
      const result = await testMutation.mutateAsync(payload);
      setTestResult(result);
      setTestedPayloadKey(payloadKey);
    } catch (error) {
      if (showAgentNameError(error)) {
        // The api checks the name before it probes: there is no probe result to show.
        setTestResult(null);
        setTestedPayloadKey(null);
        return;
      }
      setTestResult({ ok: false, message: errorMessage(error), capabilities: {} });
      setTestedPayloadKey(payloadKey);
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
      if (!showAgentNameError(error)) toast.error(errorMessage(error));
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-6" noValidate>
      <fieldset className="m-0 flex min-w-0 flex-col gap-2 border-0 p-0">
        <legend className="mb-1.5 text-label font-medium text-foreground">Type</legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {DEPLOYMENT_TYPES.map((option) => (
            <OptionCard
              key={option.value}
              name="deployment_type"
              value={option.value}
              checked={state.deployment_type === option.value}
              onChange={() => update("deployment_type", option.value)}
              title={
                <span className="flex items-center gap-2">
                  {option.mark ? (
                    <VendorMark vendor={option.mark} size="xs" />
                  ) : (
                    <Icon as={option.icon} size="md" className="text-text-secondary" />
                  )}
                  {option.title}
                </span>
              }
              description={option.hint}
            />
          ))}
        </div>
      </fieldset>

      <div className="flex flex-col gap-4">
        <Field label="Name" htmlFor="conn-name" error={errors.name}>
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

        <Field label="Slug" htmlFor="conn-slug" optional hint="Used in links: letters, numbers and dashes.">
          <Input
            id="conn-slug"
            value={state.slug}
            onChange={(event) => {
              update("slug", event.target.value);
              update("slugTouched", true);
            }}
            className="font-mono"
          />
        </Field>

        <Field label="URL" htmlFor="conn-url" error={errors.url} hint="wss:// for LiveKit Cloud, ws:// or wss:// for a self-hosted server.">
          <Input
            id="conn-url"
            value={state.url}
            onChange={(event) => update("url", event.target.value)}
            placeholder="wss://my-project.livekit.cloud"
            className="font-mono"
          />
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="API key" htmlFor="conn-key" error={errors.api_key}>
            <Input
              id="conn-key"
              value={state.api_key}
              onChange={(event) => update("api_key", event.target.value)}
              autoComplete="off"
              spellCheck={false}
              className="font-mono"
            />
          </Field>
          <Field label="API secret" htmlFor="conn-secret" error={errors.api_secret} hint="Stored encrypted and never shown again.">
            <SecretInput id="conn-secret" value={state.api_secret} onChange={(event) => update("api_secret", event.target.value)} />
          </Field>
        </div>

        <Field label="Agent name" htmlFor="conn-agent-name" optional hint={AGENT_NAME_HINT} error={errors.agent_name}>
          <Input
            id="conn-agent-name"
            value={state.agent_name}
            onChange={(event) => {
              update("agent_name", event.target.value);
              setErrors((prev) => {
                const next = { ...prev };
                delete next.agent_name;
                return next;
              });
            }}
            className="font-mono"
          />
        </Field>

        {state.deployment_type === "cloud" ? (
          <Field label="Use LiveKit Inference" htmlFor="conn-inference" inline hint="Lets speech, language and voice slots run with no vendor key.">
            <Switch id="conn-inference" checked={state.use_inference} onCheckedChange={(next) => update("use_inference", next)} />
          </Field>
        ) : null}
      </div>

      <fieldset className="m-0 flex min-w-0 flex-col gap-2 border-0 p-0">
        <legend className="mb-1.5 text-label font-medium text-foreground">Worker image</legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {WORKER_IMAGES.map((image) => (
            <OptionCard
              key={image.value}
              name="worker_image"
              value={image.value}
              checked={state.worker_image === image.value}
              onChange={() => update("worker_image", image.value)}
              title={image.title}
              description={image.hint}
            />
          ))}
        </div>
      </fieldset>

      <fieldset className="m-0 flex min-w-0 flex-col gap-2 border-0 p-0">
        <legend className="mb-1.5 text-label font-medium text-foreground">Deployment mode</legend>
        <div className="grid gap-2 sm:grid-cols-3">
          {DEPLOYMENT_MODES.map((mode) => (
            <OptionCard
              key={mode.value}
              name="deployment_mode"
              value={mode.value}
              checked={state.deployment_mode === mode.value}
              disabled={mode.disabledFor === state.deployment_type}
              onChange={() => update("deployment_mode", mode.value)}
              title={mode.title}
              description={mode.hint}
            />
          ))}
        </div>
        <p className="text-label text-pretty text-text-secondary" data-slot="worker-note">
          {WORKER_NEEDED_NOTE} {WORKER_START_BY_MODE[state.deployment_mode]}
        </p>
      </fieldset>

      <Section
        id="connection-test"
        title="Test connection"
        description="Runs before Create unlocks. Nothing is stored yet."
        aside={
          <Button
            type="button"
            onClick={() => void runTest()}
            busy={testMutation.isPending}
            busyLabel="Testing…"
          >
            Test connection
          </Button>
        }
      >
        {testResult ? (
          <SectionRow className="flex flex-col gap-3" role="status" aria-live="polite">
            <StatusPill tone={lifecycleStatus(testResult.ok ? "ok" : "failed").tone}>
              {testResult.ok ? "Connection OK" : "Connection failed"}
            </StatusPill>
            <p className="text-label text-pretty text-text-secondary">{testResult.message}</p>
            {testResult.ok ? (
              <CapabilityList capabilities={testResult.capabilities} />
            ) : (
              <p className="text-label text-pretty text-text-secondary">Check the URL, key and secret, then test again.</p>
            )}
          </SectionRow>
        ) : (
          <SectionRow className="text-label text-text-secondary">Not tested yet.</SectionRow>
        )}
      </Section>

      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Button type="button" onClick={() => router.push("/console/connections")}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            disabled={!testPassed}
            busy={createMutation.isPending}
            busyLabel="Creating…"
          >
            Create connection
          </Button>
        </div>
        {!testPassed && testResult ? (
          <p className="text-right text-caption text-text-secondary">
            {testedPayloadKey !== payloadKey ? "Details changed since the last test. Test again to enable Save." : "Save unlocks once the test passes."}
          </p>
        ) : null}
      </div>
    </form>
  );
}
