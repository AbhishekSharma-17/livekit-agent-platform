import * as React from "react";

import {
  AudioLinesIcon,
  AudioWaveformIcon,
  CloudIcon,
  EyeIcon,
  KeyRoundIcon,
  type LucideIcon,
  PencilLineIcon,
  TypeIcon,
  VolumeXIcon,
  WrenchIcon,
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
  | "custom";

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
  custom: { icon: PencilLineIcon, label: "Custom", tone: "neutral" },
};

const TONE_CLASSES = {
  neutral: "bg-muted text-muted-foreground",
  success: "bg-success-soft text-success-text",
  warning: "bg-warning-soft text-warning-text",
} as const;

/** Provider/model capability (docs/UI_UX_SPEC.md §2.7, §4.4): icon + word, micro type. */
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
        "inline-flex h-5 w-fit shrink-0 items-center gap-1 rounded-xs px-1.5 whitespace-nowrap",
        "text-[0.6875rem] leading-[0.875rem] font-medium tracking-[0.02em]",
        TONE_CLASSES[meta.tone],
        className,
      )}
    >
      <Icon as={meta.icon} size="sm" className="size-3" />
      {text}
    </span>
  );
}
