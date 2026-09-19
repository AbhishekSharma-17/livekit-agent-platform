"use client";

/**
 * `useUiRequests` — CONTRACTS §10: the agent → UI RPC direction
 * (`lkap.ui.request`, methods `open_dialog` | `focus` | `request_video_source`
 * | `toast`).
 *
 * `useRpc` keeps the handler in a ref and only re-registers when the room or
 * method name changes, so the handler does not need to be memoised.
 */
import { useCallback } from "react";
import { useRpc, useSessionContext, useVoiceAssistant } from "@livekit/components-react";
import { serializers } from "livekit-client";

import type { UiRequest, UiRequestResult } from "@/contracts/lkap-contracts";
import { RPC_UI_REQUEST } from "@/lib/livekit";

export type UiRequestHandler = (
  request: UiRequest,
) => Promise<UiRequestResult> | UiRequestResult;

/**
 * Register the browser-side handler. Only one handler per room may exist, so
 * this is mounted once by the session page.
 */
export function useUiRequests(handler: UiRequestHandler): void {
  const session = useSessionContext();
  const { agent } = useVoiceAssistant();

  const wrapped = useCallback(
    async (request: UiRequest): Promise<UiRequestResult> => {
      try {
        return await handler(request);
      } catch (error) {
        return {
          ok: false,
          payload: {
            error: error instanceof Error ? error.message : String(error),
          },
        };
      }
    },
    [handler],
  );

  useRpc(session, RPC_UI_REQUEST, wrapped, {
    serializer: serializers.json<UiRequest, UiRequestResult>(),
    fromIdentity: agent?.identity,
  });
}
