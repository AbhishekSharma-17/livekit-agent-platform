/**
 * `TokenSource.custom(...)` over `POST /v1/agents/{id_or_slug}/text-sessions`
 * (V2-18, CONTRACTS-V2 §3.4) — a byte-for-byte mirror of
 * `lib/livekit.ts::createConnectTokenSource`'s freeze/inflight logic, pointed
 * at the text-session endpoint instead of `/connect`. Kept in this package
 * (`components/session/embed/**`, V2-18's own directory) rather than added to
 * `lib/livekit.ts`, which V2-18 does not own.
 *
 * One token source == one attempt == one `sessionId`, exactly as the voice
 * session page's token source works: the console **Test chat** drawer and the
 * embed's text-mode layout each create a fresh one per attempt.
 */
import { TokenSource } from "livekit-client";

import type { ConnectRequest, ConnectResponse } from "@/contracts/lkap-contracts";
import {
  ConnectError,
  errorFromPayload,
  publicApiBaseUrl,
  type SessionAccess,
} from "@/lib/livekit";

const DEFAULT_ACCESS: SessionAccess = { viaConsole: false };

/**
 * `POST /v1/agents/{id_or_slug}/text-sessions`, or — in test mode — through the
 * console's admin proxy (`/api/console/agents/{id_or_slug}/text-sessions`),
 * the same way `?mode=test` already reaches `/connect` (DECISIONS-W2 D-W2-1).
 */
export async function fetchTextSession(
  slug: string,
  request: ConnectRequest,
  access: SessionAccess = DEFAULT_ACCESS,
): Promise<ConnectResponse> {
  const url = access.viaConsole
    ? `/api/console/agents/${encodeURIComponent(slug)}/text-sessions`
    : `${publicApiBaseUrl()}/v1/agents/${encodeURIComponent(slug)}/text-sessions`;
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    cache: "no-store",
  });
  const payload: unknown = await response.json().catch(() => undefined);
  if (!response.ok) throw errorFromPayload(response.status, payload);
  return payload as ConnectResponse;
}

export interface TextSessionTokenSourceHandlers {
  onDetails?: (details: ConnectResponse) => void;
  onError?: (error: unknown) => void;
}

export interface TextSessionTokenSource {
  tokenSource: ReturnType<typeof TokenSource.custom>;
  freeze: () => void;
}

/** See `createConnectTokenSource`'s docstring for the freeze/inflight rationale
 * (DECISIONS-W2 D-W2-2a) — identical here, just against `/text-sessions`. */
export function createTextSessionTokenSource(
  slug: string,
  handlers: TextSessionTokenSourceHandlers = {},
  access: SessionAccess = DEFAULT_ACCESS,
): TextSessionTokenSource {
  type Credentials = { serverUrl: string; participantToken: string };

  let frozen = false;
  let last: Credentials | null = null;
  let inflight: Promise<Credentials> | null = null;

  const tokenSource = TokenSource.custom(async (options) => {
    if (frozen) {
      if (inflight) return inflight;
      if (last) return last;
      throw new ConnectError(0, "session_closed", "This session has ended.");
    }

    const request = (async (): Promise<Credentials> => {
      try {
        const details = await fetchTextSession(
          slug,
          {
            participant_name: options.participantName ?? "Guest",
            participant_identity: options.participantIdentity ?? null,
            participant_metadata: {},
          },
          access,
        );
        handlers.onDetails?.(details);
        return {
          serverUrl: details.serverUrl,
          participantToken: details.participantToken,
        };
      } catch (error) {
        handlers.onError?.(error);
        throw error;
      }
    })();

    inflight = request;
    try {
      last = await request;
      frozen = true;
      return last;
    } finally {
      if (inflight === request) inflight = null;
    }
  });

  return {
    tokenSource,
    freeze: () => {
      if (last !== null || inflight !== null) frozen = true;
    },
  };
}
