"use client";

/**
 * Pre-connect screen for `/s/[slug]`.
 *
 * Nothing has been connected yet at this point: this is also the browser's
 * user gesture, which is what lets the microphone and audio playback start.
 */
import * as React from "react";

import { MessageSquareTextIcon, MicIcon, MonitorUpIcon, VideoIcon } from "lucide-react";

import type { AgentPublicOut } from "@/contracts/lkap-contracts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export interface PreCallCardProps {
  agent: AgentPublicOut;
  participantName: string;
  onParticipantNameChange: (value: string) => void;
  onStart: () => void;
  /** Shown when a previous attempt failed. */
  error?: string | null;
  /**
   * `?mode=test` (DECISIONS-W2 D-W2-1): the connect call is going through the
   * console's admin proxy, so draft agents can be called from here.
   */
  testMode?: boolean;
}

export function PreCallCard({
  agent,
  participantName,
  onParticipantNameChange,
  onStart,
  error,
  testMode,
}: PreCallCardProps) {
  const capabilities = agent.capabilities;

  return (
    <main className="flex min-h-dvh items-center justify-center p-6">
      <form
        className="border-border/60 bg-card/40 w-full max-w-md rounded-2xl border p-6 shadow-xl"
        onSubmit={(event) => {
          event.preventDefault();
          onStart();
        }}
      >
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <Badge variant="secondary" className="text-[0.7rem]">
            {agent.pipeline_mode === "realtime" ? "Realtime voice" : "Voice agent"}
          </Badge>
          {testMode && (
            <Badge variant="outline" className="text-[0.7rem]">
              Test mode — draft agents allowed
            </Badge>
          )}
        </div>
        <h1 className="text-2xl font-semibold tracking-tight">{agent.name}</h1>
        {agent.description && (
          <p className="text-muted-foreground mt-2 text-sm leading-relaxed">
            {agent.description}
          </p>
        )}

        <ul className="text-muted-foreground mt-5 flex flex-wrap gap-x-4 gap-y-2 text-xs">
          <li className="flex items-center gap-1.5">
            <MicIcon className="size-3.5" aria-hidden /> Microphone
          </li>
          {capabilities.camera && (
            <li className="flex items-center gap-1.5">
              <VideoIcon className="size-3.5" aria-hidden /> Camera
            </li>
          )}
          {capabilities.screen_share && (
            <li className="flex items-center gap-1.5">
              <MonitorUpIcon className="size-3.5" aria-hidden /> Screen share
            </li>
          )}
          {capabilities.chat_input && (
            <li className="flex items-center gap-1.5">
              <MessageSquareTextIcon className="size-3.5" aria-hidden /> Chat
            </li>
          )}
        </ul>

        <div className="mt-6 space-y-2">
          <label
            htmlFor="participant-name"
            className="text-muted-foreground text-xs font-medium"
          >
            Your name
          </label>
          <Input
            id="participant-name"
            name="participant-name"
            autoComplete="name"
            value={participantName}
            maxLength={64}
            onChange={(event) => onParticipantNameChange(event.target.value)}
            placeholder="Guest"
          />
        </div>

        {error && (
          <p role="alert" className="mt-4 text-sm text-red-300">
            {error}
          </p>
        )}

        <Button type="submit" size="lg" className="mt-6 w-full">
          Start call
        </Button>
        <p className="text-muted-foreground/70 mt-3 text-center text-[0.7rem]">
          Your microphone starts on. You can mute or end the call at any time.
        </p>
      </form>
    </main>
  );
}
