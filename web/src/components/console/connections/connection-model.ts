import type { ConnectionOut, WorkerInstanceOut } from "@/contracts/lkap-contracts";
import type { StatusTone } from "@/components/shared/status-chip";

/** Pure helpers for the connections list/detail (UI_UX_SPEC-V2-AMENDMENTS §2.1). Kept apart from the components for easy unit testing. */

export function connectionStatusTone(status: ConnectionOut["status"] | undefined): StatusTone {
  switch (status) {
    case "ok":
      return "success";
    case "error":
      return "danger";
    default:
      return "neutral";
  }
}

export function connectionStatusLabel(status: ConnectionOut["status"] | undefined): string {
  switch (status) {
    case "ok":
      return "OK";
    case "error":
      return "Error";
    default:
      return "Unverified";
  }
}

export const DEPLOYMENT_TYPE_LABEL: Record<NonNullable<ConnectionOut["deployment_type"]>, string> = {
  cloud: "LiveKit Cloud",
  self_hosted: "Self-hosted",
};

export const DEPLOYMENT_MODE_LABEL: Record<NonNullable<ConnectionOut["deployment_mode"]>, string> = {
  external: "External",
  supervised: "Supervised",
  cloud_hosted: "Cloud-hosted",
};

/** `URL host` for the list's "URL host" column — falls back to the raw string when it doesn't parse. */
export function urlHost(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

export function slugify(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "")
    .slice(0, 48);
}

/** A `gone` instance from more than an hour ago is not worth showing (the fleet endpoint already includes the last hour's `gone` rows — CONTRACTS-V2 §3.4). */
export function visibleInstances(instances: WorkerInstanceOut[] | undefined): WorkerInstanceOut[] {
  return instances ?? [];
}

export interface FleetHealth {
  ready: number;
  desired: number;
  /** `true` when the pool mixes `external` and `supervisor` rows — PLAN-V2 §7's risk row. */
  mixedManagement: boolean;
}

export function fleetHealth(desiredReplicas: number | undefined, instances: WorkerInstanceOut[] | undefined): FleetHealth {
  const rows = instances ?? [];
  const ready = rows.filter((row) => row.status === "ready").length;
  const managedBy = new Set(rows.map((row) => row.managed_by).filter((value): value is NonNullable<typeof value> => Boolean(value)));
  return {
    ready,
    desired: desiredReplicas ?? 0,
    mixedManagement: managedBy.has("external") && managedBy.has("supervisor"),
  };
}

export function instanceStatusTone(status: WorkerInstanceOut["status"] | undefined): StatusTone {
  switch (status) {
    case "ready":
      return "success";
    case "starting":
      return "info";
    case "draining":
      return "warning";
    case "gone":
      return "neutral";
    default:
      return "neutral";
  }
}

/* -------------------------------------------------------------------------- */
/* V6-27: agent-name clashes and "which worker serves this connection"        */
/* -------------------------------------------------------------------------- */

/** The Agent name field's hint, on the create form and the edit form. */
export const AGENT_NAME_HINT =
  "Workers register under this name. Must be unique on this LiveKit server — if another app or connection uses it, calls get split between them.";

/** The api's 409 code when another connection uses the agent name on the same LiveKit server. */
export const AGENT_NAME_IN_USE = "agent_name_in_use";

/** The message to show under the Agent name field when `error` is that 409, else `null`. */
export function agentNameError(error: unknown): string | null {
  if (typeof error !== "object" || error === null) return null;
  const { code, message } = error as { code?: unknown; message?: unknown };
  return code === AGENT_NAME_IN_USE && typeof message === "string" ? message : null;
}

type DeploymentMode = NonNullable<ConnectionOut["deployment_mode"]>;

/** Shown under Deployment mode on the create form. */
export const WORKER_NEEDED_NOTE =
  "Each connection needs its own worker. After you create this connection, start a worker for it — agents bound here won't answer calls until one is running.";

/** Who starts that worker, per mode (the create form's note). */
export const WORKER_START_BY_MODE: Record<DeploymentMode, string> = {
  external: "External: you start it — the connection's page gives you its worker settings and the start command.",
  supervised: "Supervised: LKAP starts it once you press Start on the connection's Fleet tab.",
  cloud_hosted: "Cloud-hosted: deploy it to LiveKit Cloud with the bundle from the connection's Deploy tab.",
};

/** The empty state's title when nothing is running for a connection. */
export const NO_WORKER_TITLE = "No worker is running for this connection.";

/** Said wherever the empty state shows. */
export const OTHER_CONNECTION_WORKER_NOTE = "A worker started for another connection won't serve this one.";

/**
 * The one-line start command next to "Copy worker settings", run from the
 * repository root: the settings saved *outside* the repository (RUNBOOK §1,
 * R-V2-35 — a file with secrets never lives in the checkout) with the `<…>`
 * placeholders filled in, then the same `lkap_agent.main start` the settings'
 * `lk` format ends with.
 */
export const WORKER_START_COMMAND =
  'set -a && . "$HOME/.config/lkap/worker.env" && set +a && cd agent && uv run python -m lkap_agent.main start';

/** "2 workers ready" / "1 worker ready" / "No workers running"; `null` when the count is unknown. */
export function workerStatusLabel(ready: number | null | undefined): string | null {
  if (ready === null || ready === undefined) return null;
  if (ready <= 0) return "No workers running";
  return `${ready} ${ready === 1 ? "worker" : "workers"} ready`;
}

/** The warning when another connection's workers answer to this connection's agent name. */
export function sharedAgentNameWarning(count: number | undefined, agentName: string): string | null {
  if (!count || count <= 0) return null;
  const subject =
    count === 1
      ? "1 worker started for another connection is"
      : `${count} workers started for another connection are`;
  return `${subject} running under the agent name “${agentName}” on this LiveKit server, so calls are split between them. Give one of the connections a different agent name.`;
}
