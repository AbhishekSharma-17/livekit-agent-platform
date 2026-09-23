/**
 * Telephony wire types (PLAN-V2 V2-17, CONTRACTS-V2 §3.4 "Telephony").
 *
 * HAND-WRITTEN STOPGAP: the trunk / dispatch-rule / number shapes live in
 * `api/src/lkap_api/telephony/models.py`, not in `lkap_contracts` yet, so they
 * are not in `@/contracts/lkap-contracts`. `docs/v2/_asks.md` V2-17-1 asks the
 * contracts owner to promote them; once generated, delete this file and import
 * from `@/contracts/lkap-contracts` (the field names below are identical).
 * `CallCreate` / `CallOut` / `CallPage` already come from the contracts.
 */

export type TrunkDirection = "inbound" | "outbound";
export type ProviderHint = "twilio" | "telnyx" | "other";

export interface TrunkOut {
  id: string;
  connection_id: string;
  direction: TrunkDirection;
  name: string;
  lk_trunk_id: string | null;
  numbers: string[];
  provider_hint: ProviderHint;
  address: string | null;
  auth_username: string | null;
  has_password: boolean;
  created_at: string;
  updated_at: string;
}

export interface TrunkCreate {
  connection_id?: string | null;
  direction: TrunkDirection;
  name: string;
  numbers?: string[];
  provider_hint?: ProviderHint;
  address?: string | null;
  auth_username?: string | null;
  auth_password?: string | null;
}

export interface DispatchRuleOut {
  id: string;
  connection_id: string;
  lk_rule_id: string | null;
  trunk_id: string;
  agent_id: string;
  numbers: string[];
  room_prefix: string;
  has_pin: boolean;
  managed_by_number: string | null;
  created_at: string;
}

export interface DispatchRuleCreate {
  trunk_id: string;
  agent_id: string;
  numbers?: string[];
  room_prefix?: string;
  pin?: string | null;
}

export interface PhoneNumberOut {
  id: string;
  e164: string;
  trunk_id: string | null;
  inbound_agent_id: string | null;
  label: string;
  dispatch_rule_id: string | null;
}

export interface PhoneNumberCreate {
  e164: string;
  trunk_id?: string | null;
  inbound_agent_id?: string | null;
  label?: string;
}

/** `PUT /v1/telephony/numbers/{id}`: only the keys present change; `inbound_agent_id: null` unroutes. */
export type PhoneNumberUpdate = Partial<Pick<PhoneNumberOut, "trunk_id" | "inbound_agent_id" | "label">>;

export interface Page<T> {
  items: T[];
  total: number;
}

export interface CallDtmfOut {
  call_id: string;
  digits: string;
  queued: boolean;
}

/** E.164 as the api validates it: `+`, a non-zero country digit, 7–15 digits. */
export const E164_PATTERN = /^\+[1-9]\d{6,14}$/;

/** Keys `POST /v1/calls/{id}/dtmf` accepts. */
export const DTMF_PATTERN = /^[0-9*#A-D]{1,32}$/;

/** Transfer targets: E.164 or a `tel:` / `sip:` URI. */
export const TRANSFER_TARGET_PATTERN = /^(\+[1-9]\d{6,14}|tel:\+?[0-9]{3,20}|sips?:[^\s@]+@\S+)$/;
