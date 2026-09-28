"use client";

/**
 * `useCallerEdit` — the caller's half of a block edit (V6-06 → V6-08,
 * D-V6-19, ask #24/#56): `lkap.agent.action {action: "block_action", payload:
 * {block_id, name: "edit", data}}` on a `details` / `checklist` / `notebook`
 * block whose config allows it.
 *
 * A sibling of `composite/use-block-request.ts` (the requestable-block answer
 * hook) for the caller-edit wire instead: the same `{ok, payload, error}`
 * shape, but the "question" here is not a pending request — it is any
 * on-screen change the caller makes, any number of times. The result is
 * always one of:
 *
 * - `{ok: false, error}` — refused; the caller's plain-words reason
 *   (`caller_edit_refusal` / `check_caller_edit` on the worker), never a raw
 *   code. The caller's on-screen value is left as it was.
 * - `{ok: true, payload: {changed: false}}` — a no-op (the value already
 *   matched); treated as success with nothing to show.
 * - `{ok: true, …}` — applied; the real value arrives moments later on the
 *   envelope patch, same as any other block change.
 *
 * Shared by `checklist.tsx`, `details.tsx` and `notebook/sections.tsx`
 * (all three are this package's files) so the three editors show the same
 * busy/error behaviour.
 */
import { useCallback, useState } from "react";

import type { PanelBlockAction, PanelProps } from "@/panels/registry";

/** `MAX_CALLER_EDIT_CHARS` (`lkap_contracts.ui_protocol`): the longest value a caller may type. */
export const MAX_CALLER_EDIT_CHARS = 500;

export interface CallerEditResult {
  ok: boolean;
  changed: boolean;
  error: string | null;
}

export interface UseCallerEditResult {
  /** An edit is in flight. */
  sending: boolean;
  /** The last refusal or transport error, in plain words. */
  error: string | null;
  /** Clear a shown error (e.g. the caller starts typing again). */
  clearError: () => void;
  /** Send `{block_id, name: "edit", data}`; never throws. */
  send: (data: Record<string, unknown>) => Promise<CallerEditResult>;
}

interface RawResult {
  ok?: boolean;
  error?: string | null;
  payload?: { changed?: boolean };
}

/** Drive one block's caller edits. `blockId` is the block the edit targets (`payload.block_id`). */
export function useCallerEdit(blockId: string, perform: PanelProps["perform"]): UseCallerEditResult {
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const send = useCallback(
    async (data: Record<string, unknown>): Promise<CallerEditResult> => {
      setError(null);
      setSending(true);
      const action: PanelBlockAction = { action: "block_action", payload: { block_id: blockId, name: "edit", data } };
      try {
        const result = (await perform(action)) as RawResult | undefined;
        setSending(false);
        if (result && result.ok === false) {
          const message = result.error || "The agent didn't accept that. Try again.";
          setError(message);
          return { ok: false, changed: false, error: message };
        }
        return { ok: true, changed: result?.payload?.changed !== false, error: null };
      } catch (err) {
        setSending(false);
        const message = err instanceof Error ? err.message : "Couldn't reach the agent. Try again.";
        setError(message);
        return { ok: false, changed: false, error: message };
      }
    },
    [blockId, perform],
  );

  return { sending, error, clearError: () => setError(null), send };
}
