/**
 * The one lifecycle map (docs/ui/DESIGN-SYSTEM.md section 6.6): every
 * status the api sends, mapped to a tone and a human label. Status is always
 * a word plus a tone, never colour alone.
 *
 * | Tone    | States |
 * |---------|--------|
 * | success | done, ready, approved, sent, delivered, connected, published |
 * | warning | waiting, draft, needs review, pending, needs reauth |
 * | info    | scheduled, in progress, processing, indexing, starting |
 * | danger  | failed, needs attention, dead, error, expired |
 * | live    | live, active call (brand tone with a pulsing dot) |
 * | neutral | stopped, cancelled, ended, archived, skipped, idle |
 *
 * Plain data, importable from both surfaces.
 */
export type StatusTone = "neutral" | "info" | "success" | "warning" | "danger" | "live";

export interface LifecycleStatus {
  tone: StatusTone;
  label: string;
}

export const LIFECYCLE: Record<string, LifecycleStatus> = {
  // success
  done: { tone: "success", label: "Done" },
  completed: { tone: "success", label: "Done" },
  ready: { tone: "success", label: "Ready" },
  approved: { tone: "success", label: "Approved" },
  sent: { tone: "success", label: "Sent" },
  delivered: { tone: "success", label: "Delivered" },
  connected: { tone: "success", label: "Connected" },
  published: { tone: "success", label: "Published" },
  ok: { tone: "success", label: "Working" },
  verified: { tone: "success", label: "Verified" },
  stored: { tone: "success", label: "Saved" },
  recalled: { tone: "success", label: "Recalled" },
  found: { tone: "success", label: "Found" },
  // warning
  waiting: { tone: "warning", label: "Waiting" },
  draft: { tone: "warning", label: "Draft" },
  needs_review: { tone: "warning", label: "Ready to review" },
  pending: { tone: "warning", label: "Waiting" },
  queued: { tone: "warning", label: "Queued" },
  requested: { tone: "warning", label: "Requested" },
  needs_reauth: { tone: "warning", label: "Needs sign-in again" },
  unverified: { tone: "warning", label: "Not checked yet" },
  draining: { tone: "warning", label: "Finishing calls" },
  timeout: { tone: "warning", label: "Timed out" },
  missed: { tone: "warning", label: "Missed" },
  // info
  scheduled: { tone: "info", label: "Scheduled" },
  in_progress: { tone: "info", label: "In progress" },
  processing: { tone: "info", label: "Processing" },
  indexing: { tone: "info", label: "Indexing" },
  running: { tone: "info", label: "Running" },
  starting: { tone: "info", label: "Starting" },
  created: { tone: "info", label: "Starting" },
  connecting: { tone: "info", label: "Connecting" },
  submitted: { tone: "info", label: "Submitted" },
  opened: { tone: "info", label: "Opened" },
  // danger
  failed: { tone: "danger", label: "Failed" },
  needs_attention: { tone: "danger", label: "Needs attention" },
  error: { tone: "danger", label: "Needs attention" },
  dead: { tone: "danger", label: "Gave up" },
  expired: { tone: "danger", label: "Expired" },
  revoked: { tone: "danger", label: "Revoked" },
  join_failed: { tone: "danger", label: "Couldn't join" },
  unavailable: { tone: "danger", label: "Unavailable" },
  // live
  live: { tone: "live", label: "Live" },
  active: { tone: "live", label: "Live" },
  // neutral
  stopped: { tone: "neutral", label: "Stopped" },
  cancelled: { tone: "neutral", label: "Cancelled" },
  canceled: { tone: "neutral", label: "Cancelled" },
  ended: { tone: "neutral", label: "Ended" },
  archived: { tone: "neutral", label: "Archived" },
  skipped: { tone: "neutral", label: "Skipped" },
  idle: { tone: "neutral", label: "Idle" },
  none: { tone: "neutral", label: "Not set up" },
  not_connected: { tone: "neutral", label: "Not connected" },
  disabled: { tone: "neutral", label: "Off" },
  gone: { tone: "neutral", label: "Gone" },
  empty: { tone: "neutral", label: "Empty" },
  deferred: { tone: "neutral", label: "Later" },
};

/** "needs_client_registration" -> "Needs client registration". */
export function humanizeStatus(value: string): string {
  const words = value.replace(/[_-]+/g, " ").trim().toLowerCase();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : "Unknown";
}

/** Tone and human label for any status string; unknown ones read as neutral words. */
export function lifecycleStatus(state: string | null | undefined): LifecycleStatus {
  if (!state) return { tone: "neutral", label: "Unknown" };
  const key = state.trim().toLowerCase().replace(/[\s-]+/g, "_");
  return LIFECYCLE[key] ?? { tone: "neutral", label: humanizeStatus(state) };
}
