import {
  CloudIcon,
  MicIcon,
  PhoneIcon,
  RadioTowerIcon,
  VideoIcon,
  WavesIcon,
  type LucideIcon,
} from "lucide-react";

import type { ConnectionCapabilities, ProviderSpec } from "@/contracts/lkap-contracts";

/**
 * `CAPABILITY_META` (R-V2-2, docs/v2/PLAN-V2.md §8 — "`web/src/components/console/registry/**`
 * and `CAPABILITY_META` → V2-13"): the connection-capability chip row on the
 * connections list/detail (UI_UX_SPEC-V2-AMENDMENTS §2.1 — inference, SIP,
 * egress, NC tier) and the providers catalog's verification chip
 * (UI_UX_SPEC-V2-AMENDMENTS §4). Separate from
 * `components/shared/capability-badge.tsx`'s `CapabilityKind` (WP-0's,
 * provider/model capabilities such as vision/tools/voices) — that file is
 * not touched here.
 */

export type ConnectionCapabilityKey =
  | "inference"
  | "sip"
  | "egress"
  | "ingress"
  | "cloud_hosting"
  | "noise_cancellation"
  | "turn_detector";

export interface ConnectionCapabilityMeta {
  icon: LucideIcon;
  label: string;
  /** One sentence, used as the chip's tooltip / the capability list's detail column. */
  help: string;
}

export const CAPABILITY_META: Record<ConnectionCapabilityKey, ConnectionCapabilityMeta> = {
  inference: {
    icon: CloudIcon,
    label: "Inference",
    help: "LiveKit Inference is reachable — STT/LLM/TTS slots can run with no vendor key.",
  },
  sip: {
    icon: PhoneIcon,
    label: "SIP",
    help: "The SIP service answers — trunks and dispatch rules can be configured.",
  },
  egress: {
    icon: VideoIcon,
    label: "Egress",
    help: "Room recording (Egress) is available.",
  },
  ingress: {
    icon: RadioTowerIcon,
    label: "Ingress",
    help: "External media (RTMP/WHIP) can be pulled into a room.",
  },
  cloud_hosting: {
    icon: CloudIcon,
    label: "Cloud-hosted",
    help: "This project accepts a cloud-hosted worker deploy bundle.",
  },
  noise_cancellation: {
    icon: WavesIcon,
    label: "Noise cancellation",
    help: "Background voice/noise cancellation is available for this connection.",
  },
  turn_detector: {
    icon: MicIcon,
    label: "Turn detector",
    help: "LiveKit's turn-detector model is available for this connection.",
  },
};

export interface ConnectionCapabilityChip {
  key: ConnectionCapabilityKey;
  meta: ConnectionCapabilityMeta;
  present: boolean;
  /** e.g. "krisp" / "local" — appended to the label when the flag is a tri-state, not just present/absent. */
  detail?: string;
}

/**
 * The capability row for one connection (`ConnectionCapabilities`, possibly
 * `undefined` for an unverified connection: every flag then reads absent —
 * the most restrictive reading, matching the api's own default).
 */
export function connectionCapabilityChips(caps: ConnectionCapabilities | undefined): ConnectionCapabilityChip[] {
  const ncTier = caps?.noise_cancellation_tier ?? "none";
  const turnMode = caps?.turn_detector_mode;
  return [
    { key: "inference", meta: CAPABILITY_META.inference, present: Boolean(caps?.inference_available) },
    { key: "sip", meta: CAPABILITY_META.sip, present: Boolean(caps?.sip_enabled) },
    { key: "egress", meta: CAPABILITY_META.egress, present: Boolean(caps?.egress_enabled) },
    { key: "ingress", meta: CAPABILITY_META.ingress, present: Boolean(caps?.ingress_enabled) },
    { key: "cloud_hosting", meta: CAPABILITY_META.cloud_hosting, present: Boolean(caps?.cloud_hosting) },
    { key: "noise_cancellation", meta: CAPABILITY_META.noise_cancellation, present: ncTier !== "none", detail: ncTier !== "none" ? ncTier : undefined },
    { key: "turn_detector", meta: CAPABILITY_META.turn_detector, present: Boolean(turnMode), detail: turnMode },
  ];
}

/**
 * Capability reasons are always sentences with a fix
 * (UI_UX_SPEC-V2-AMENDMENTS §4). Used by the connection test result view for
 * a flag that came back `false`.
 */
export const CAPABILITY_FALSE_REASON: Record<ConnectionCapabilityKey, string> = {
  inference: "Not reachable on this connection — self-hosted deployments need their own STT/LLM/TTS keys.",
  sip: "Not reachable — deploy the SIP service on this LiveKit project and re-test.",
  egress: "Not reachable — deploy the Egress service on this LiveKit project and re-test.",
  ingress: "Not reachable — deploy the Ingress service on this LiveKit project and re-test.",
  cloud_hosting: "Only LiveKit Cloud projects accept a cloud-hosted deploy bundle.",
  noise_cancellation: "No noise-cancellation tier detected for this connection.",
  turn_detector: "No hosted turn-detector model for this connection; the worker falls back to a local one.",
};

export type VerificationTone = "verified" | "unverified";

export interface VerificationMeta {
  label: string;
  /** UI_UX_SPEC-V2-AMENDMENTS §4's exact copy for the chip's help text. */
  help: string;
}

export const VERIFICATION_META: Record<VerificationTone, VerificationMeta> = {
  verified: { label: "Verified", help: "Verified: passed a live call on this platform." },
  unverified: { label: "Available", help: "Available: installed and configurable, not yet live-tested." },
};

export function verificationMeta(spec: Pick<ProviderSpec, "verification">): VerificationMeta {
  return VERIFICATION_META[spec.verification === "verified" ? "verified" : "unverified"];
}
