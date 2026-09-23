"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type {
  CallCreate,
  CallDtmfOut,
  CallOut,
  CallPage,
  DispatchRuleCreate,
  DispatchRuleOut,
  DispatchRulePage,
  PhoneNumberCreate,
  PhoneNumberOut,
  PhoneNumberPage,
  PhoneNumberUpdate,
  TrunkCreate,
  TrunkOut,
  TrunkPage,
} from "@/contracts/lkap-contracts";

/**
 * Telephony data (V2-17): `/v1/telephony/{trunks,dispatch-rules,numbers}` and
 * `/v1/calls`, through the same `/api/console/*` proxy as every console hook.
 */

export const telephonyKeys = {
  trunks: ["telephony", "trunks"] as const,
  rules: ["telephony", "rules"] as const,
  numbers: ["telephony", "numbers"] as const,
  calls: ["calls"] as const,
  call: (id: string) => ["calls", id] as const,
};

/** Calls still in progress (the log polls while any exist). */
export const OPEN_CALL_STATUSES = new Set(["dialing", "ringing", "answered"]);

export function useTrunks() {
  return useQuery({ queryKey: telephonyKeys.trunks, queryFn: () => api.get<TrunkPage>("telephony/trunks") });
}

export function useDispatchRules() {
  return useQuery({
    queryKey: telephonyKeys.rules,
    queryFn: () => api.get<DispatchRulePage>("telephony/dispatch-rules"),
  });
}

export function usePhoneNumbers() {
  return useQuery({
    queryKey: telephonyKeys.numbers,
    queryFn: () => api.get<PhoneNumberPage>("telephony/numbers"),
  });
}

export function useCalls(limit = 50) {
  return useQuery({
    queryKey: [...telephonyKeys.calls, limit],
    queryFn: () => api.get<CallPage>("calls", { limit }),
    refetchInterval: (query) =>
      (query.state.data?.items ?? []).some((c) => OPEN_CALL_STATUSES.has(c.status ?? "dialing")) ? 3_000 : false,
  });
}

export function useCall(id: string | null) {
  return useQuery({
    queryKey: telephonyKeys.call(id ?? ""),
    queryFn: () => api.get<CallOut>(`calls/${id}`),
    enabled: Boolean(id),
    refetchInterval: (query) => (OPEN_CALL_STATUSES.has(query.state.data?.status ?? "dialing") ? 2_000 : false),
  });
}

/** Every telephony write can change trunks, rules and numbers at once (routing follows numbers). */
function useInvalidateTelephony() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["telephony"] });
  };
}

export function useCreateTrunk() {
  const invalidate = useInvalidateTelephony();
  return useMutation({
    mutationFn: (body: TrunkCreate) => api.post<TrunkOut>("telephony/trunks", body),
    onSuccess: invalidate,
  });
}

export function useSyncTrunk() {
  const invalidate = useInvalidateTelephony();
  return useMutation({
    mutationFn: (id: string) => api.post<TrunkOut>(`telephony/trunks/${id}/sync`),
    onSuccess: invalidate,
  });
}

export function useDeleteTrunk() {
  const invalidate = useInvalidateTelephony();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`telephony/trunks/${id}`),
    onSuccess: invalidate,
  });
}

export function useCreateDispatchRule() {
  const invalidate = useInvalidateTelephony();
  return useMutation({
    mutationFn: (body: DispatchRuleCreate) => api.post<DispatchRuleOut>("telephony/dispatch-rules", body),
    onSuccess: invalidate,
  });
}

export function useDeleteDispatchRule() {
  const invalidate = useInvalidateTelephony();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`telephony/dispatch-rules/${id}`),
    onSuccess: invalidate,
  });
}

export function useCreateNumber() {
  const invalidate = useInvalidateTelephony();
  return useMutation({
    mutationFn: (body: PhoneNumberCreate) => api.post<PhoneNumberOut>("telephony/numbers", body),
    onSuccess: invalidate,
  });
}

export function useUpdateNumber() {
  const invalidate = useInvalidateTelephony();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: PhoneNumberUpdate }) =>
      api.put<PhoneNumberOut>(`telephony/numbers/${id}`, body),
    onSuccess: invalidate,
  });
}

export function useDeleteNumber() {
  const invalidate = useInvalidateTelephony();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`telephony/numbers/${id}`),
    onSuccess: invalidate,
  });
}

function useCallMutation<TBody, TOut>(fn: (id: string, body: TBody) => Promise<TOut>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: TBody }) => fn(id, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: telephonyKeys.calls });
    },
  });
}

export function usePlaceCall() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CallCreate) => api.post<CallOut>("calls", body),
    onSuccess: (call) => {
      queryClient.setQueryData(telephonyKeys.call(call.id), call);
      void queryClient.invalidateQueries({ queryKey: telephonyKeys.calls });
    },
  });
}

export function useHangupCall() {
  return useCallMutation((id: string) => api.post<CallOut>(`calls/${id}/hangup`));
}

export function useTransferCall() {
  return useCallMutation((id: string, body: { to: string }) => api.post<CallOut>(`calls/${id}/transfer`, body));
}

export function useSendDtmf() {
  return useCallMutation((id: string, body: { digits: string }) => api.post<CallDtmfOut>(`calls/${id}/dtmf`, body));
}
