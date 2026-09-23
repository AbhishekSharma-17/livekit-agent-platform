"use client";

/**
 * Pre-call screen for `/s/[slug]` (docs/UI_UX_SPEC.md §5.1).
 *
 * The card is **injectable**: the device state arrives as a `devices` prop
 * (`useMicCheck()` supplies it at runtime), so every state — idle, requesting,
 * granted with a live level, denied with per-browser guidance, unsupported —
 * renders in a test or the preview route without a browser permission prompt.
 *
 * The Start click is the browser's user gesture: the parent uses it to prime
 * audio playback (§5.2) before the room mounts.
 */
import * as React from "react";
import {
  MessageSquareTextIcon,
  MicIcon,
  MonitorUpIcon,
  PhoneIcon,
  VideoIcon,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import type { AgentPublicOut } from "@/contracts/lkap-contracts";
import { Icon } from "@/components/shared/icon";
import { StateMeter } from "@/components/shared/state-meter";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  SessionCard,
  SessionCardScreen,
} from "@/components/session/session-card";
import { TestModeBar } from "@/components/session/test-mode-bar";
import {
  expectations,
  micPermissionHint,
  MIC_STATUS_MESSAGE,
  type ExpectationIcon,
} from "@/components/session/session-state";
import type { MicDevices } from "@/hooks/use-mic-check";

const EXPECTATION_ICON: Record<ExpectationIcon, LucideIcon> = {
  mic: MicIcon,
  video: VideoIcon,
  screen: MonitorUpIcon,
  chat: MessageSquareTextIcon,
};

export interface PreCallCardProps {
  agent: AgentPublicOut;
  participantName: string;
  onParticipantNameChange: (value: string) => void;
  /** Start the call. Called from a click, so it can prime audio playback. */
  onStart: () => void;
  /** Injected device state (`useMicCheck()` at runtime). */
  devices: MicDevices;
  /** "Check microphone" / the retry after a denial. */
  onCheckMicrophone: () => void;
  onSelectInput: (deviceId: string) => void;
  /** Shown when a previous attempt failed. */
  error?: string | null;
  /**
   * `?mode=test` (DECISIONS-W2 D-W2-1): the connect call goes through the
   * console's admin proxy, so draft agents can be called from here. The only
   * chrome is the slim bar above the card.
   */
  testMode?: boolean;
  /** `/console/agents/<id>` for the test bar's "Back to editor" link. */
  backHref?: string;
  /** `NEXT_PUBLIC_LKAP_PRIVACY_URL`, when the deployment sets one. */
  privacyUrl?: string;
}

export function PreCallCard({
  agent,
  participantName,
  onParticipantNameChange,
  onStart,
  devices,
  onCheckMicrophone,
  onSelectInput,
  error,
  testMode,
  backHref,
  privacyUrl,
}: PreCallCardProps) {
  const items = expectations(agent.name, agent.capabilities);
  const requesting = devices.status === "requesting";
  const denied = devices.status === "denied";

  return (
    <SessionCardScreen
      top={testMode ? <TestModeBar backHref={backHref} /> : undefined}
    >
      <SessionCard width="wide" data-testid="pre-call-card">
        <form
          className="grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]"
          onSubmit={(event) => {
            event.preventDefault();
            onStart();
          }}
        >
          {/* Stage well */}
          <div className="bg-stage text-stage-foreground flex h-40 flex-col items-center justify-center gap-4 p-6 text-center md:h-auto md:items-start md:text-left">
            <StateMeter state="idle" size="lg" bars={5} />
            <div>
              <h1 className="text-[1.75rem] leading-[2.125rem] font-semibold tracking-[-0.02em] text-balance">
                {agent.name}
              </h1>
              {agent.description && (
                <p className="text-muted-foreground mt-2 text-base text-pretty">
                  {agent.description}
                </p>
              )}
            </div>
            <ul className="text-muted-foreground hidden gap-2 text-sm md:grid">
              {items.map((item) => (
                <li key={item.text} className="flex items-start gap-2">
                  <Icon
                    as={EXPECTATION_ICON[item.icon]}
                    size="md"
                    className="mt-0.5"
                  />
                  <span>{item.text}</span>
                </li>
              ))}
            </ul>
          </div>

          {/* Form */}
          <div className="flex flex-col gap-5 p-5 md:p-6">
            <ul className="text-muted-foreground grid gap-2 text-sm md:hidden">
              {items.map((item) => (
                <li key={item.text} className="flex items-start gap-2">
                  <Icon
                    as={EXPECTATION_ICON[item.icon]}
                    size="md"
                    className="mt-0.5"
                  />
                  <span>{item.text}</span>
                </li>
              ))}
            </ul>

            <div className="grid gap-1.5">
              <Label htmlFor="participant-name" className="text-sm">
                Your name
              </Label>
              <Input
                id="participant-name"
                name="participant-name"
                autoComplete="name"
                className="h-10 text-base"
                value={participantName}
                maxLength={64}
                onChange={(event) => onParticipantNameChange(event.target.value)}
                placeholder="Guest"
              />
            </div>

            <div className="grid gap-2">
              <span className="text-sm font-medium">Microphone</span>

              {devices.inputs.length > 1 && (
                <Select
                  value={devices.selectedId}
                  onValueChange={onSelectInput}
                >
                  <SelectTrigger
                    aria-label="Microphone input"
                    className="h-10 w-full text-base"
                  >
                    <SelectValue placeholder="Default microphone" />
                  </SelectTrigger>
                  <SelectContent>
                    {devices.inputs.map((device) => (
                      <SelectItem key={device.deviceId} value={device.deviceId}>
                        {device.label || "Microphone"}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}

              <div className="flex items-center gap-3">
                {devices.status === "granted" && (
                  <StateMeter
                    state="listening"
                    size="md"
                    level={devices.level}
                  />
                )}
                <p
                  data-testid="mic-status"
                  aria-live="polite"
                  className={
                    denied || devices.status === "unsupported"
                      ? "text-danger-text text-sm"
                      : "text-muted-foreground text-sm"
                  }
                >
                  {MIC_STATUS_MESSAGE[devices.status]}
                </p>
              </div>

              {denied && (
                <p className="text-muted-foreground text-sm">
                  {micPermissionHint(
                    typeof navigator === "undefined" ? "" : navigator.userAgent,
                  )}
                </p>
              )}

              {(devices.status === "idle" ||
                devices.status === "requesting") && (
                <Button
                  type="button"
                  variant="secondary"
                  size="lg"
                  className="w-fit"
                  disabled={requesting}
                  onClick={onCheckMicrophone}
                >
                  <Icon as={MicIcon} size="md" />
                  {requesting ? "Waiting for permission…" : "Check microphone"}
                </Button>
              )}
              {denied && (
                <Button
                  type="button"
                  variant="secondary"
                  size="lg"
                  className="w-fit"
                  onClick={() => window.location.reload()}
                >
                  Reload
                </Button>
              )}
            </div>

            {error && (
              <p
                role="alert"
                data-testid="pre-call-error"
                className="bg-danger-soft text-danger-text rounded-md px-3 py-2 text-sm"
              >
                {error}
              </p>
            )}

            <div>
              <Button
                type="submit"
                variant="brand"
                size="xl"
                className="w-full"
                disabled={requesting}
              >
                <Icon as={PhoneIcon} size="xl" />
                {requesting ? "Waiting for permission…" : "Start call"}
              </Button>
              <p className="text-muted-foreground mt-3 text-xs">
                Your microphone is on during the call. You can mute or hang up
                any time.
                {privacyUrl && (
                  <>
                    {" "}
                    <a
                      href={privacyUrl}
                      className="underline underline-offset-4"
                    >
                      Privacy
                    </a>
                  </>
                )}
              </p>
            </div>
          </div>
        </form>
      </SessionCard>
    </SessionCardScreen>
  );
}
