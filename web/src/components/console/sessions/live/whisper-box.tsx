"use client";

/**
 * "Guide the agent" (V5-38, `docs/v5/_asks.md` #251): `POST
 * /v1/sessions/{id}/whisper` — written guidance the agent sees and the caller
 * never does. Independent of the tab's own listen-in connection
 * (`listen-session.ts`): the whisper goes straight through the api to the
 * room's agent participant, so it works even if this browser's own audio
 * link is still connecting or has dropped, as long as the session is live.
 *
 * The card's "audit-friendly confirmation before the first whisper": a
 * `ConfirmDialog` (never a second window) gates the first send in this tab;
 * once the supervisor has seen and accepted that copy once, later sends in
 * the same tab go straight through — every one is still audit-logged
 * server-side (`session.whisper`) and shown in `LiveTimeline` (`live-tab.tsx`)
 * once it lands.
 */
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Textarea } from "@/components/ui/textarea";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { Field } from "@/components/shared/field";
import { useSessionWhisper } from "@/components/console/lib/api-hooks";
import { ApiError } from "@/lib/api";

/** Matches the api's `SessionWhisperIn.text` (`api/src/lkap_api/routers/sessions.py::MAX_WHISPER_CHARS`). */
export const MAX_WHISPER_CHARS = 1000;

export function WhisperBox({ sessionId }: { sessionId: string }) {
  const [text, setText] = React.useState("");
  const [replyNow, setReplyNow] = React.useState(false);
  const [hasConfirmed, setHasConfirmed] = React.useState(false);
  const [notice, setNotice] = React.useState<{ tone: "success" | "danger"; message: string } | null>(null);
  const whisper = useSessionWhisper(sessionId);

  const trimmed = text.trim();
  const canSend = trimmed.length > 0 && trimmed.length <= MAX_WHISPER_CHARS && !whisper.isPending;

  const send = React.useCallback(async () => {
    setNotice(null);
    try {
      await whisper.mutateAsync({ text: trimmed, reply_now: replyNow });
      setText("");
      setReplyNow(false);
      setNotice({ tone: "success", message: "Sent to the agent." });
    } catch (error) {
      const message =
        error instanceof ApiError && error.code === "no_agent"
          ? "No agent is in the room right now — the guidance wasn't delivered."
          : errorMessage(error);
      setNotice({ tone: "danger", message });
    }
  }, [trimmed, replyNow, whisper]);

  const confirmAndSend = React.useCallback(async () => {
    setHasConfirmed(true);
    await send();
  }, [send]);

  return (
    <div data-slot="live-whisper-box" className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <div>
        <h3 className="text-sm font-medium text-foreground">Guide the agent</h3>
        <p className="text-muted-foreground text-xs">
          Written guidance only the agent sees — the caller never hears or sees it. Every message is logged.
        </p>
      </div>
      <Field label="Message to the agent" htmlFor="live-whisper-text" hint={`${trimmed.length}/${MAX_WHISPER_CHARS}`}>
        <Textarea
          id="live-whisper-text"
          value={text}
          onChange={(event) => setText(event.target.value.slice(0, MAX_WHISPER_CHARS))}
          placeholder="e.g. Offer the premium plan"
          rows={3}
          disabled={whisper.isPending}
        />
      </Field>
      <label className="flex items-center gap-2 text-sm text-foreground">
        <Checkbox
          checked={replyNow}
          onCheckedChange={(checked) => setReplyNow(checked === true)}
          disabled={whisper.isPending}
        />
        Have the agent say this right now
      </label>
      {/* One persistent live region — a conditionally-mounted one announces nothing on change. */}
      <div role="status" aria-live="polite" className="min-h-[1.25rem] text-xs">
        {notice?.tone === "success" ? <span className="text-success-text">{notice.message}</span> : null}
      </div>
      {notice?.tone === "danger" ? <ErrorBanner message={notice.message} /> : null}
      <div className="flex justify-end">
        {hasConfirmed ? (
          <Button type="button" onClick={() => void send()} disabled={!canSend}>
            {whisper.isPending ? "Sending…" : "Send"}
          </Button>
        ) : (
          <ConfirmDialog
            trigger={
              <Button type="button" disabled={!canSend}>
                Send
              </Button>
            }
            title="Send this to the agent?"
            description="The caller won't hear or see this. It's written guidance for the agent, and this workspace logs every whisper sent from here."
            confirmLabel="Send"
            destructive={false}
            onConfirm={confirmAndSend}
          />
        )}
      </div>
    </div>
  );
}
