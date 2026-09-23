/**
 * Stand-in for the vendored `AgentControlBar` (docs/UI_UX_SPEC.md §7.11 item
 * 2: "the real control bar needs a room; a `ControlBarPlaceholder` with the
 * same dimensions is acceptable for screenshots"). `session-controls.tsx`
 * wraps `@/components/agents-ui/agent-control-bar`, which calls
 * `useChat()`/`useRoomContext()` and throws outside a `RoomContext.Provider` —
 * it cannot render in the preview route, so this mirrors its footprint
 * (height, radius, shadow, button sizes) without a room.
 */
import { MessageSquareTextIcon, MicIcon, PhoneOffIcon, VideoIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { cn } from "@/lib/utils";

export interface ControlBarPlaceholderProps {
  camera?: boolean;
  chat?: boolean;
  /** Mirrors `SessionControls`' compact (embed) bar. */
  compact?: boolean;
  className?: string;
}

export function ControlBarPlaceholder({
  camera = true,
  chat = true,
  compact = false,
  className,
}: ControlBarPlaceholderProps) {
  return (
    <div
      data-testid="control-bar-placeholder"
      aria-hidden="true"
      className={cn(
        "border-border bg-card flex w-full items-center justify-center gap-2 rounded-xl border",
        compact ? "h-[3.25rem] p-1.5" : "h-16 p-2 shadow-md",
        className,
      )}
    >
      <span className="bg-accent text-muted-foreground inline-flex size-10 items-center justify-center rounded-full">
        <Icon as={MicIcon} size="md" />
      </span>
      {camera && (
        <span className="bg-accent text-muted-foreground inline-flex size-10 items-center justify-center rounded-full">
          <Icon as={VideoIcon} size="md" />
        </span>
      )}
      {chat && (
        <span className="bg-accent text-muted-foreground inline-flex size-10 items-center justify-center rounded-full">
          <Icon as={MessageSquareTextIcon} size="md" />
        </span>
      )}
      <span className="bg-destructive text-destructive-foreground ml-2 inline-flex h-10 items-center gap-1.5 rounded-full px-4 text-sm font-medium">
        <Icon as={PhoneOffIcon} size="md" />
        End call
      </span>
    </div>
  );
}
