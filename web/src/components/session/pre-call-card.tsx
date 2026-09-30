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
 *
 * Built from the shared primitives (docs/ui/DESIGN-SYSTEM.md section 6):
 * `Field` for the name (marked optional; it falls back to "Guest"), the
 * `Select`, one primary button (Start call, last), and `Alert`s for the last
 * attempt's error and for being offline (section 8.8). Caller actions reach
 * 48 px on phones.
 */
import * as React from "react";
import {
  MessageSquareTextIcon,
  MicIcon,
  MonitorUpIcon,
  PhoneIcon,
  RefreshCwIcon,
  VideoIcon,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import type { AgentPublicOut } from "@/contracts/lkap-contracts";
import { Field } from "@/components/shared/field";
import { Icon } from "@/components/shared/icon";
import { StateMeter } from "@/components/shared/state-meter";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
import { PHONE_TOUCH_TARGET } from "@/components/session/session-layout";
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

const subscribeNever = () => () => {};

/**
 * The visitor's user agent, or `""` on the server *and* during hydration, so
 * the per-browser denied hint renders the generic fallback in both places and
 * only switches to the browser-specific text after hydration (no mismatch).
 */
function useUserAgent(): string {
  return React.useSyncExternalStore(
    subscribeNever,
    () => navigator.userAgent,
    () => "",
  );
}

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
  /** The browser reports no network (`navigator.onLine`); says so above Start. */
  offline?: boolean;
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
  offline = false,
}: PreCallCardProps) {
  const items = expectations(agent.name, agent.capabilities);
  const requesting = devices.status === "requesting";
  const denied = devices.status === "denied";
  const userAgent = useUserAgent();

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
              <h1 className="text-display font-semibold tracking-[-0.025em] text-balance">
                {agent.name}
              </h1>
              {agent.description && (
                <p className="text-text-secondary mt-2 text-pretty">
                  {agent.description}
                </p>
              )}
            </div>
            <ul className="text-text-secondary text-body hidden gap-2 md:grid">
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
            <ul className="text-text-secondary text-body grid gap-2 md:hidden">
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

            <Field label="Your name" htmlFor="participant-name" optional>
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
            </Field>

            <div className="grid gap-2">
              <span className="text-label font-medium">Microphone</span>

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
                      ? "text-destructive-text text-body"
                      : "text-text-secondary text-body"
                  }
                >
                  {MIC_STATUS_MESSAGE[devices.status]}
                </p>
              </div>

              {denied && (
                <p className="text-text-secondary text-body">
                  {micPermissionHint(userAgent)}
                </p>
              )}

              {(devices.status === "idle" ||
                devices.status === "requesting") && (
                <Button
                  type="button"
                  variant="secondary"
                  size="lg"
                  className={`w-fit ${PHONE_TOUCH_TARGET}`}
                  busy={requesting}
                  busyLabel="Waiting for permission…"
                  onClick={onCheckMicrophone}
                >
                  <Icon as={MicIcon} size="md" />
                  Check microphone
                </Button>
              )}
              {denied && (
                <Button
                  type="button"
                  variant="secondary"
                  size="lg"
                  className={`w-fit ${PHONE_TOUCH_TARGET}`}
                  onClick={() => window.location.reload()}
                >
                  <Icon as={RefreshCwIcon} size="md" />
                  Reload
                </Button>
              )}
            </div>

            {offline && (
              <Alert tone="warning" data-testid="pre-call-offline" className="text-body">
                You&rsquo;re offline. You can start the call once you&rsquo;re back online.
              </Alert>
            )}

            {error && (
              <Alert tone="danger" data-testid="pre-call-error" className="text-body">
                {error}
              </Alert>
            )}

            <div>
              <Button
                type="submit"
                variant="primary"
                size="xl"
                className={`w-full ${PHONE_TOUCH_TARGET}`}
                disabled={requesting}
              >
                <Icon as={PhoneIcon} size="xl" />
                {requesting ? "Waiting for permission…" : "Start call"}
              </Button>
              <p className="text-text-secondary text-caption mt-3">
                Your microphone is on during the call. You can mute or hang up
                any time.
                {privacyUrl && (
                  <>
                    {" "}
                    <a
                      href={privacyUrl}
                      className="text-brand hover:text-brand-hover underline underline-offset-3"
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
