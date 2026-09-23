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
