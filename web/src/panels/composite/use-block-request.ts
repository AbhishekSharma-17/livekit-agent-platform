"use client";

/**
 * `useBlockRequest` — the answer half of the generic blocking request round
 * trip (CONTRACTS-V2 §4.4, V5-02 → V5-03) shared by every requestable
 * block's renderer.
 *
 * The *question* half is already answered by block state alone: the agent
 * flips `status` to `"requested"` (`RequestableState`) before it even sends
 * the `lkap.ui.request {method: "request" | "form"}` RPC, so a reconnecting
 * browser renders the pending question straight from the snapshot — this
 * hook never talks to the RPC layer (`composite/requests.ts` does, and only
 * to bring the block into view). What this hook drives is sending the
 * *answer* back: `lkap.agent.action {action: "block_submit", payload:
 * {block_id, values} | {block_id, cancelled: true}}`. That single action
 * name is accepted for a `request` and for the legacy `form` alias alike
 * (the agent dispatches by which method is pending, not by which action
 * name answered it) — one code path, two method names.
 */
import { useCallback, useRef, useState } from "react";

import type { PanelProps } from "@/panels/registry";

import type { RequestableStatus } from "../blocks/types";

export type BlockRequestSending = "submit" | "cancel" | null;

export interface UseBlockRequestResult {
  /** `true` exactly when `status === "requested"` — the block's own pending marker. */
  pending: boolean;
  /** Which answer is in flight, if any. */
  sending: BlockRequestSending;
  /** The agent's refusal, or a transport error, from the last answer sent. */
  error: string | null;
  /** Send `{block_id, values}`. */
  submit: (values: Record<string, unknown>) => Promise<void>;
  /** Send `{block_id, cancelled: true}`. */
  cancel: () => Promise<void>;
}

type BlockSubmitPayload =
  | { block_id: string; values: Record<string, unknown> }
  | { block_id: string; cancelled: true };

/**
 * `block_submit` is not yet in `PanelAction` (`@/panels/registry`, not an
 * exclusive file of this package) even though the contract's `AgentAction`
 * has carried it since V5-02 and `agentActionFor` already forwards any
 * action/payload pair it doesn't special-case. Filed in `docs/v5/_asks.md`
 * (ask requesting `PanelAction` grow a `block_submit` member); this cast is
 * the only thing standing in for that until the coordinator applies it.
 */
function blockSubmitAction(payload: BlockSubmitPayload): Parameters<PanelProps["perform"]>[0] {
  return { action: "block_submit", payload } as unknown as Parameters<PanelProps["perform"]>[0];
}

/**
 * Drive one requestable block's answer. `status` is the block's own
 * `RequestableState.status` (`data.status` in a `BlockRenderProps`); `perform`
 * is `panel.perform`. Resets `sending`/`error` whenever `status` crosses in
 * or out of `"requested"` — a fresh question, or the agent having accepted
 * the last answer — so a second request never inherits stale UI from the
 * first.
 */
export function useBlockRequest(
  blockId: string,
  status: RequestableStatus | undefined,
  perform: PanelProps["perform"],
): UseBlockRequestResult {
  const pending = status === "requested";
  const [sending, setSending] = useState<BlockRequestSending>(null);
  const [error, setError] = useState<string | null>(null);

  const wasPending = useRef(pending);
  if (wasPending.current !== pending) {
    wasPending.current = pending;
    if (sending !== null) setSending(null);
    if (error !== null) setError(null);
  }

  const send = useCallback(
    async (payload: BlockSubmitPayload, kind: "submit" | "cancel") => {
      setError(null);
      setSending(kind);
      try {
        const result = (await perform(blockSubmitAction(payload))) as { ok?: boolean; error?: string | null } | undefined;
        if (result && result.ok === false) {
          setError(result.error || "The agent didn't accept that. Try again.");
          setSending(null);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Couldn't reach the agent. Try again.");
        setSending(null);
      }
    },
    [perform],
  );

  const submit = useCallback(
    (values: Record<string, unknown>) => send({ block_id: blockId, values }, "submit"),
    [blockId, send],
  );
  const cancel = useCallback(() => send({ block_id: blockId, cancelled: true }, "cancel"), [blockId, send]);

  return { pending, sending, error, submit, cancel };
}
