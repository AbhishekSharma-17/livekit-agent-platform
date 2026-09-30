/**
 * Busy labels (docs/ui/DESIGN-SYSTEM.md section 3): a button's verb becomes
 * a gerund plus an ellipsis while it works, "Delete agent" -> "Deleting
 * agent…". Only the first word changes.
 */
const IRREGULAR: Record<string, string> = {
  be: "being",
  see: "seeing",
  free: "freeing",
  agree: "agreeing",
  hang: "hanging",
  log: "logging",
  sign: "signing",
  set: "setting",
  reset: "resetting",
  get: "getting",
  put: "putting",
  cut: "cutting",
  run: "running",
  rerun: "rerunning",
  stop: "stopping",
  drop: "dropping",
  ship: "shipping",
  plan: "planning",
  skip: "skipping",
  swap: "swapping",
  pin: "pinning",
  unpin: "unpinning",
  submit: "submitting",
  admit: "admitting",
  commit: "committing",
  omit: "omitting",
  cancel: "cancelling",
  label: "labelling",
  model: "modelling",
  dial: "dialling",
  redial: "redialling",
  refer: "referring",
  transfer: "transferring",
  forget: "forgetting",
  begin: "beginning",
  tie: "tying",
  die: "dying",
  lie: "lying",
};

/** "Save" -> "Saving", "Delete" -> "Deleting", "Stop" -> "Stopping". */
export function gerund(verb: string): string {
  const lower = verb.toLowerCase();
  let result: string;
  if (IRREGULAR[lower]) result = IRREGULAR[lower];
  else if (lower.endsWith("ie")) result = `${lower.slice(0, -2)}ying`;
  else if (lower.endsWith("ee") || lower.endsWith("ye") || lower.endsWith("oe")) result = `${lower}ing`;
  else if (lower.endsWith("e") && lower.length > 2) result = `${lower.slice(0, -1)}ing`;
  else result = `${lower}ing`;
  return verb.charAt(0) === verb.charAt(0).toUpperCase() ? result.charAt(0).toUpperCase() + result.slice(1) : result;
}

/** "Delete agent" -> "Deleting agent…" (a label that is already busy is returned as is). */
export function busyLabelFor(label: string): string {
  const trimmed = label.trim();
  if (trimmed === "" || trimmed.endsWith("…")) return trimmed;
  const [first, ...rest] = trimmed.split(/\s+/);
  return `${[gerund(first), ...rest].join(" ")}…`;
}
