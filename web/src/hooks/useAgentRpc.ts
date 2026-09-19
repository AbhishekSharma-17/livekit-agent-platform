"use client";

/**
 * `useAgentRpc` — CONTRACTS §10: the UI → agent direction of the protocol,
 * wrapping `useRpc().perform` for the `lkap.agent.action` method the agent
 * registers with `register_rpc_method`.
 */
import { useCallback, useMemo } from "react";
import { useRpc, useSessionContext, useVoiceAssistant } from "@livekit/components-react";
import { serializers } from "livekit-client";

import type {
  AgentAction,
  AgentActionResult,
} from "@/contracts/lkap-contracts";
import { RPC_AGENT_ACTION } from "@/lib/livekit";

export interface UseAgentRpcReturn {
  /** Call the agent. Rejects when no agent participant is in the room yet. */
  perform: (action: AgentAction) => Promise<AgentActionResult>;
  /** The agent is present and can be called. */
  ready: boolean;
  /** Convenience wrapper used by panels (CONTRACTS §11 `PanelProps.perform`). */
  performUiAction: (name: string, data?: unknown) => Promise<AgentActionResult>;
}

export function useAgentRpc(): UseAgentRpcReturn {
  const session = useSessionContext();
  const { perform } = useRpc(session);
  const { agent } = useVoiceAssistant();
  const agentIdentity = agent?.identity;

  const call = useCallback(
    async (action: AgentAction): Promise<AgentActionResult> => {
      if (!agentIdentity) {
        return {
          ok: false,
          payload: {},
          error: "The agent has not joined the room yet.",
        };
      }
      try {
        return await perform<AgentActionResult, AgentAction>(
          {
            destinationIdentity: agentIdentity,
            method: RPC_AGENT_ACTION,
            payload: action,
          },
          serializers.json<AgentActionResult, AgentAction>(),
        );
      } catch (error) {
        return {
          ok: false,
          payload: {},
          error: error instanceof Error ? error.message : String(error),
        };
      }
    },
    [agentIdentity, perform],
  );

  const performUiAction = useCallback(
    (name: string, data?: unknown) =>
      call({
        v: 1,
        action: "ui_action",
        payload: { name, data: data ?? null },
      }),
    [call],
  );

  return useMemo(
    () => ({ perform: call, ready: Boolean(agentIdentity), performUiAction }),
    [call, agentIdentity, performUiAction],
  );
}
