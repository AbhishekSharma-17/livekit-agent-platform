import type { BlockSpec, RequestableState } from "@/contracts/lkap-contracts";
import type { PanelProps } from "@/panels/registry";

/**
 * Site allowlists and safe-link checks (`link.allowed_hosts`,
 * `cards.image_hosts`, V5-43 → V5-44).
 *
 * A straight TypeScript port of `lkap_contracts.ui_protocol`'s
 * `HOST_PATTERN` / `normalize_host` / `host_allowed` / `https_url_problem` —
 * the web can't import the Python package, and a link or a card image must
 * be re-checked here regardless of what the worker already validated,
 * because the browser renders whatever the agent last wrote to `UiState`
 * with no further trip through the api. Kept in this shared `types.ts`
 * (not a new file) so `link.tsx`, `cards.tsx` and the composer's
 * `block-config-form.tsx` "hosts" editor read one copy.
 */

/** Longest link a `link` block or a card image may carry (`MAX_URL_CHARS`). */
export const MAX_URL_CHARS = 2048;

/**
 * A site name in an allowlist: a host name with at least one dot, lower
 * case, optionally `*.` in front for "any sub-domain of" (the bare domain
 * itself is not included then). Mirrors `ui_protocol.HOST_PATTERN`.
 */
export const HOST_PATTERN = /^(\*\.)?([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;

/** Characters a link can never contain (control characters, quotes, angle brackets, `|^`). */
const URL_FORBIDDEN_RE = /[\x00-\x20\x7f-\x9f\\<>"'`{}|^]/;

function isNumericHost(host: string): boolean {
  const stripped = host.replaceAll(".", "").replaceAll("*", "");
  return stripped.length > 0 && /^\d+$/.test(stripped);
}

/**
 * One allowlist entry as a lower-case site name (`example.com`,
 * `*.example.com`), or `null` for anything that is not one: a scheme, a
 * path, a port, an IP address, spaces or a name without a dot.
 */
export function normalizeHost(value: string): string | null {
  const host = value.trim().toLowerCase().replace(/\.+$/, "");
  if (!HOST_PATTERN.test(host) || isNumericHost(host)) return null;
  return host;
}

/** Whether `host` is one of `allowedHosts` (exact, or a sub-domain of a `*.` entry). */
export function hostAllowed(host: string, allowedHosts: readonly string[]): boolean {
  const target = host.trim().toLowerCase().replace(/\.+$/, "");
  for (const raw of allowedHosts) {
    const entry = raw.trim().toLowerCase().replace(/\.+$/, "");
    if (entry.startsWith("*.")) {
      const suffix = entry.slice(1); // ".example.com"
      if (target.endsWith(suffix) && target.length > suffix.length) return true;
    } else if (target === entry) {
      return true;
    }
  }
  return false;
}

/**
 * Why `url` may not be shown to a caller, or `null` when it may. Only
 * `https://` links with a host name are accepted: never `javascript:`,
 * `data:`, `http:` or any other scheme, never user names or passwords,
 * never spaces/quotes/angle-brackets/backslashes/control characters, never
 * longer than `MAX_URL_CHARS`. With `allowedHosts` the host must also be one
 * of them (`hostAllowed`). Mirrors `ui_protocol.https_url_problem`.
 */
export function httpsUrlProblem(url: string, allowedHosts?: readonly string[]): string | null {
  if (!url) return "the link is empty";
  if (url.length > MAX_URL_CHARS) return `the link is longer than ${MAX_URL_CHARS} characters`;
  if (URL_FORBIDDEN_RE.test(url)) return "the link contains spaces or characters a link cannot have";
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return "the link is not a valid web address";
  }
  if (parsed.protocol !== "https:") return "only https:// links can be shown";
  if (parsed.username || parsed.password) return "a link cannot carry a user name or password";
  const host = parsed.hostname.toLowerCase();
  if (!host || !HOST_PATTERN.test(host) || isNumericHost(host)) return "the link has no valid site name";
  if (allowedHosts !== undefined && !hostAllowed(host, allowedHosts)) {
    return `${host} is not one of the sites this panel may link to (${allowedHosts.join(", ") || "none"})`;
  }
  return null;
}

/** `url` when `httpsUrlProblem` finds nothing wrong with it against `allowedHosts`, else `null`. */
export function checkedHttpsUrl(url: string | null | undefined, allowedHosts: readonly string[]): string | null {
  if (!url) return null;
  return httpsUrlProblem(url, allowedHosts) === null ? url : null;
}

/** `BlockSpec.config.allowed_hosts` / `.image_hosts` as a plain string array. */
export function hostsOf(config: unknown, key: "allowed_hosts" | "image_hosts"): string[] {
  const record = typeof config === "object" && config !== null ? (config as Record<string, unknown>) : {};
  const value = record[key];
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}


/**
 * What every block component receives from `<Block>`.
 *
 * - `spec`: the `BlockSpec` from the layout (id, type, title, config).
 * - `data`: the block's own state — `UiState.blocks[spec.id]` over the type's
 *   initial state (`catalog.initialBlockState`), so no field is ever missing.
 *   `{}` for the envelope blocks, which read `panel.state` instead.
 * - `panel`: the panel's props (envelope, asset URLs, transcript, `perform`).
 * - `title`: the resolved heading (`spec.title` → the type's default; `null`
 *   renders no heading).
 * - `highlighted`: `show_block` / a `form` or `request` just pointed at this block.
 */
export interface BlockRenderProps<S = Record<string, unknown>> {
  spec: BlockSpec;
  data: S;
  panel: PanelProps;
  title: string | null;
  highlighted?: boolean;
}

/**
 * The pending/answered lifecycle of any requestable block (CONTRACTS-V2
 * §4.4 `RequestableState`, V5-02). `"requested"` is the pending marker a
 * renderer (and `useBlockRequest`, `composite/use-block-request.ts`) reads
 * straight from block state — never from the `request`/`form` RPC, so a
 * reconnecting browser renders it from the snapshot alone.
 */
export type RequestableStatus = NonNullable<RequestableState["status"]>;

/** DOM id of a block's frame, the scroll/focus target for `show_block`. */
export function blockDomId(blockId: string): string {
  return `lkap-block-${blockId}`;
}
