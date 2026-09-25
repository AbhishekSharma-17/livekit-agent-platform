/**
 * `lkap.ui.request` handling for the composite panel (CONTRACTS-V2 §4.4 and
 * the V2-10 → V2-11 wire contract): `request`, `form`, `show_block`,
 * `navigate`.
 *
 * `PanelDefinition` is a module object while the scroll target and the
 * confirm dialog live in the mounted component, so — like the notebook's
 * `openPacketDialog` — requests reach the component through a module-level
 * listener set. Every request is answered **at once**: the browser only sees
 * ≈3 s (`show_block`) / ≈8 s (`request` / the legacy `form`) of the worker's
 * RPC timeout.
 *
 * `request` (V5-02, the generic blocking request on any requestable block)
 * and `form` (its deprecated, one-release alias) share one handler: both
 * only bring the block into view. The pending marker itself — `status ==
 * "requested"` — is already in the block's own state, patched by the agent
 * *before* either RPC is sent, so a reconnecting browser needs no RPC at all
 * to render the pending question (`composite/use-block-request.ts` reads
 * `status` directly, never this module). One code path, two method names.
 */
import type { UiRequest, UiRequestResult } from "@/contracts/lkap-contracts";

export type CompositeRequestEvent =
  | { kind: "reveal"; blockId: string; focus: boolean }
  | { kind: "navigate"; url: string };

type Listener = (event: CompositeRequestEvent) => boolean;

const listeners = new Set<Listener>();

/** Called by the mounted composite panel. Returns the unsubscribe function. */
export function subscribeCompositeRequests(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Deliver to every mounted composite panel; `true` if one of them handled it. */
function emit(event: CompositeRequestEvent): boolean {
  let handled = false;
  for (const listener of listeners) handled = listener(event) || handled;
  return handled;
}

/** `navigate` accepts web links only — never `javascript:`, `data:` or relative paths. */
export function safeNavigateUrl(raw: unknown): string | null {
  if (typeof raw !== "string" || !raw) return null;
  try {
    const url = new URL(raw);
    return url.protocol === "https:" || url.protocol === "http:" ? url.toString() : null;
  } catch {
    return null;
  }
}

function blockIdOf(payload: Record<string, unknown> | undefined): string | null {
  const id = payload?.block_id;
  return typeof id === "string" && id !== "" ? id : null;
}

export function handleCompositeRequest(request: UiRequest): UiRequestResult {
  const payload = request.payload ?? {};
  switch (request.method) {
    case "request":
    case "form": {
      // The block already shows its question from state (`status:
      // "requested"`); this only brings it into view. Always ack
      // immediately (wire step 2) — the agent logs and keeps waiting on any
      // RPC failure either way, so there is nothing to fail on here.
      const blockId = blockIdOf(payload);
      if (!blockId) return { ok: false, payload: { error: `${request.method} needs a \`block_id\`` } };
      emit({ kind: "reveal", blockId, focus: true });
      return { ok: true, payload: {} };
    }
    case "show_block": {
      const blockId = blockIdOf(payload);
      if (!blockId) return { ok: false, payload: { error: "show_block needs a `block_id`" } };
      const shown = emit({ kind: "reveal", blockId, focus: false });
      return shown ? { ok: true, payload: {} } : { ok: false, payload: { error: `no block "${blockId}" on this panel` } };
    }
    case "focus": {
      // `focus {target}` — a composite block id is a valid target.
      const target = typeof payload.target === "string" ? payload.target : "";
      const shown = target !== "" && emit({ kind: "reveal", blockId: target, focus: true });
      return shown ? { ok: true, payload: {} } : { ok: false, payload: { error: `nothing called "${target}" to focus` } };
    }
    case "navigate": {
      const url = safeNavigateUrl(payload.url);
      if (!url) return { ok: false, payload: { error: "navigate needs an http(s) `url`" } };
      const asked = emit({ kind: "navigate", url });
      // The visitor confirms in the panel; the answer is not awaited.
      return asked ? { ok: true, payload: { confirming: true } } : { ok: false, payload: { error: "the panel is not open" } };
    }
    default:
      return { ok: false, payload: { error: `${request.method} is not supported by this panel` } };
  }
}
