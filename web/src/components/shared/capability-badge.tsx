import * as React from "react";

import {
  AudioLinesIcon,
  AudioWaveformIcon,
  BrainIcon,
  CloudIcon,
  EyeIcon,
  KeyRoundIcon,
  type LucideIcon,
  PencilIcon,
  TypeIcon,
  VolumeXIcon,
  WrenchIcon,
  ZapIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";

import { Icon } from "./icon";

export type CapabilityKind =
  | "vision"
  | "realtime"
  | "tools"
  | "silent-tools"
  | "voices"
  | "no-key"
  | "key-required"
  | "key-set"
  /** The model can answer in text as well as audio (UI_UX_SPEC-V2-AMENDMENTS §2.2). */
  | "text-modality"
  /** Runs only through LiveKit Cloud (Inference / Cloud-hosted). */
  | "cloud-only"
  /** A model id outside the suggestions and the vendor's live list (docs/v4/CUSTOM-MODELS.md D-V4-23). */
  | "custom"
  /** The model thinks before it answers (V6-31). */
  | "reasoning"
  /** A small model that answers quickly on a live call (V6-31, `FAST_VOICE_NOTE`). */
  | "fast-voice";

export interface CapabilityBadgeProps {
  kind: CapabilityKind;
  /** Shown before the word for countable kinds ("12 voices"). */
  count?: number;
  /** Override the default word, e.g. `key-set` → "Team key · 3f9a…". */
  children?: React.ReactNode;
  className?: string;
}

interface CapabilityMeta {
  icon: LucideIcon;
  label: string;
  /** Plural used with `count` (defaults to `label`). */
  plural?: string;
  tone: "neutral" | "success" | "warning";
}

export const CAPABILITY_BADGE_META: Record<CapabilityKind, CapabilityMeta> = {
  vision: { icon: EyeIcon, label: "Vision", tone: "neutral" },
  realtime: { icon: AudioWaveformIcon, label: "Realtime", tone: "neutral" },
  tools: { icon: WrenchIcon, label: "Tools", tone: "neutral" },
  "silent-tools": { icon: VolumeXIcon, label: "Silent tools", tone: "neutral" },
  voices: { icon: AudioLinesIcon, label: "Voice", plural: "Voices", tone: "neutral" },
  "no-key": { icon: KeyRoundIcon, label: "No key needed", tone: "success" },
  "key-required": { icon: KeyRoundIcon, label: "Key required", tone: "warning" },
  "key-set": { icon: KeyRoundIcon, label: "Key set", tone: "neutral" },
  "text-modality": { icon: TypeIcon, label: "Text modality", tone: "neutral" },
  "cloud-only": { icon: CloudIcon, label: "Cloud only", tone: "neutral" },
  custom: { icon: PencilIcon, label: "Custom", tone: "neutral" },
  reasoning: { icon: BrainIcon, label: "Reasoning", tone: "neutral" },
  "fast-voice": { icon: ZapIcon, label: "Fast for voice", tone: "success" },
};

const TONE_CLASSES = {
  neutral: "border-border bg-muted text-text-secondary",
  success: "border-success-border bg-success-subtle text-success-text",
  warning: "border-warning-border bg-warning-subtle text-warning-text",
} as const;

/** Provider/model capability: icon + word on a tone (docs/ui/DESIGN-SYSTEM.md section 6.6). */
export function CapabilityBadge({ kind, count, children, className }: CapabilityBadgeProps) {
  const meta = CAPABILITY_BADGE_META[kind];
  let text: React.ReactNode = children;
  if (text === undefined) {
    if (count === undefined) text = meta.label;
    else text = `${count} ${(count === 1 ? meta.label : (meta.plural ?? meta.label)).toLowerCase()}`;
  }
  return (
    <span
      data-slot="capability-badge"
      data-kind={kind}
      className={cn(
        "inline-flex h-5 w-fit shrink-0 items-center gap-1 rounded-sm border px-1.5 whitespace-nowrap",
        "text-caption leading-none font-medium",
        TONE_CLASSES[meta.tone],
        className,
      )}
    >
      <Icon as={meta.icon} size="xs" />
      {text}
    </span>
  );
}
