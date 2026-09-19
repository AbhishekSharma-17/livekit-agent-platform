/**
 * Session-surface LiveKit glue: the public connect call, the `TokenSource` the
 * session page hands to `useSession()`, and the agent ↔ UI topic constants.
 *
 * The browser normally only ever talks to the **public** connect endpoint
 * (`POST /v1/agents/{id_or_slug}/connect`, CONTRACTS §7) at
 * `NEXT_PUBLIC_API_BASE_URL` (CONTRACTS §3). No admin token, no secrets.
 *
 * The one exception is **test mode** (DECISIONS-W2 D-W2-1): `/s/[slug]?mode=test`
 * routes the same connect call through the console's server-side admin proxy
 * (`src/app/api/console/[...path]/route.ts`) instead, so a draft/unpublished
 * agent can still be called. This still never puts the admin token in the
 * client bundle — the proxy attaches `X-Admin-Token` server-side and this
 * module only ever fetches the *relative* `/api/console/...` path.
 *
 * DECISIONS-W2 D-W2-2(a): `createConnectTokenSource` performs exactly one
 * `POST /connect` per `TokenSource` instance — it freezes itself as soon as
 * the first call resolves, not only on unmount. Consequence: after a *full*
 * reconnect (e.g. a page refresh reusing the same mounted component tree,
 * which does not happen today but would if that ever changes) the same
 * token/room would be replayed rather than minting a new session; if the
 * agent job has already closed (`close_on_disconnect`), the room is gone and
 * the page shows "Call ended" — the visitor starts a new call, which creates
 * a fresh `TokenSource` (and thus a new `sessionId`) via `SessionExperience`'s
 * attempt counter. This is the intended MVP behaviour: one attempt == one
 * session.
 */
import { ConnectionState, TokenSource } from "livekit-client";

import type {
  AgentOut,
  AgentPublicOut,
  ConnectRequest,
  ConnectResponse,
} from "@/contracts/lkap-contracts";

/* -------------------------------------------------------------------------- */
/* Topics and RPC methods (CONTRACTS §10 `lkap_contracts.ui_protocol.TOPICS`)  */
/* -------------------------------------------------------------------------- */

export const TOPIC_UI_STATE = "lkap.ui.state";
export const TOPIC_UI_ACTIVITY = "lkap.ui.activity";
export const TOPIC_UI_ASSET = "lkap.ui.asset";
export const RPC_UI_REQUEST = "lkap.ui.request";
export const RPC_AGENT_ACTION = "lkap.agent.action";

/* -------------------------------------------------------------------------- */
/* Public API access                                                          */
/* -------------------------------------------------------------------------- */

export class ConnectError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "ConnectError";
  }

  /** True when the agent exists but is not published (CONTRACTS §7). */
  get isForbidden(): boolean {
    return this.status === 403;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }
}

export function publicApiBaseUrl(): string {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!base) {
    throw new ConnectError(
      0,
      "missing_api_base_url",
      "NEXT_PUBLIC_API_BASE_URL is not configured.",
    );
  }
  return base.replace(/\/+$/, "");
}

/** Exported so `livekit-server.ts` can raise the same `ConnectError` shape. */
export function errorFromPayload(status: number, payload: unknown): ConnectError {
  if (
    typeof payload === "object" &&
    payload !== null &&
    "error" in payload &&
    typeof (payload as { error: unknown }).error === "object" &&
    (payload as { error: unknown }).error !== null
  ) {
    const body = (payload as { error: { code?: unknown; message?: unknown } })
      .error;
    return new ConnectError(
      status,
      typeof body.code === "string" ? body.code : "unknown_error",
      typeof body.message === "string" ? body.message : "Request failed.",
    );
  }
  return new ConnectError(status, "unknown_error", `Request failed (${status}).`);
}

/**
 * `GET /v1/agents/{id_or_slug}` without a token — returns `AgentPublicOut` for
 * published agents. Used for the pre-connect card, so a failure is not fatal.
 */
export async function fetchPublicAgent(
  slug: string,
  init?: RequestInit,
): Promise<AgentPublicOut> {
  const response = await fetch(
    `${publicApiBaseUrl()}/v1/agents/${encodeURIComponent(slug)}`,
    { ...init, cache: "no-store" },
  );
  const payload: unknown = await response.json().catch(() => undefined);
  if (!response.ok) throw errorFromPayload(response.status, payload);
  return payload as AgentPublicOut;
}

/**
 * How the browser reaches the connect endpoint (DECISIONS-W2 D-W2-1).
 *
 * `viaConsole: true` is test mode: the request goes to the *relative*
 * `/api/console/agents/{slug}/connect` (same origin, no base URL, no admin
 * token in this bundle — the server-side proxy attaches it) instead of the
 * public API. This is the same trust boundary as the rest of the console.
 */
export interface SessionAccess {
  viaConsole: boolean;
}

const DEFAULT_SESSION_ACCESS: SessionAccess = { viaConsole: false };

/**
 * `POST /v1/agents/{id_or_slug}/connect` (CONTRACTS §7), or — in test mode —
 * `POST /api/console/agents/{id_or_slug}/connect` via the admin proxy.
 */
export async function fetchConnect(
  slug: string,
  request: ConnectRequest,
  access: SessionAccess = DEFAULT_SESSION_ACCESS,
): Promise<ConnectResponse> {
  const url = access.viaConsole
    ? `/api/console/agents/${encodeURIComponent(slug)}/connect`
    : `${publicApiBaseUrl()}/v1/agents/${encodeURIComponent(slug)}/connect`;
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

/**
 * Maps the admin-only `AgentOut` down to the browser-safe `AgentPublicOut`
 * shape, for the test-mode pre-call card (which loads the agent via the
 * admin proxy server-side and must not leak the rest of `config`).
 */
export function toPublicAgent(agent: AgentOut): AgentPublicOut {
  return {
    id: agent.id,
    slug: agent.slug,
    name: agent.name,
    description: agent.description,
    ui_panel_id: agent.ui_panel_id,
    capabilities: agent.config.capabilities ?? {},
    pipeline_mode: agent.config.pipeline.mode ?? "cascaded",
  };
}

export interface ConnectTokenSourceHandlers {
  /** Called with the full `ConnectResponse` on every successful connect call. */
  onDetails?: (details: ConnectResponse) => void;
  /**
   * Called when the connect call fails. `useSession` calls the token source
   * from `prepareConnection()` on mount and swallows that rejection into a
   * `console.warn`, so this is the only reliable place to see the error.
   */
  onError?: (error: unknown) => void;
}

export interface ConnectTokenSource {
  tokenSource: ReturnType<typeof TokenSource.custom>;
  /**
   * Stop issuing new connect calls and replay the token already obtained.
   *
   * Harmless belt-and-braces: the source already freezes itself as soon as
   * the first connect call resolves (DECISIONS-W2 D-W2-2a), so by the time
   * `useSession`'s `end()`/unexpected-disconnect paths call this on unmount
   * it is normally a no-op. Kept so a source that somehow hasn't connected
   * yet (still in flight) still stops after that in-flight call settles.
   */
  freeze: () => void;
}

/**
 * `TokenSource.custom(...)` over the connect endpoint (public, or the console
 * proxy in test mode — see `SessionAccess`). The extra fields of
 * `ConnectResponse` (`sessionId`, `uiPanelId`, `agent`) are handed back
 * through `onDetails` because a `TokenSource` may only return
 * `{ serverUrl, participantToken }`.
 *
 * One token source == one attempt == one `sessionId`: the session page creates
 * a new one (by remounting) whenever the visitor reconnects.
 *
 * DECISIONS-W2 D-W2-2(a): the source freezes itself as soon as the *first*
 * connect call resolves — not only on unmount via `freeze()`. `useSession`'s
 * unexpected-disconnect handler force-refetches the token (`force: true`)
 * while the component is still mounted, and without freezing here that would
 * `POST /connect` again, minting a second, orphaned session row for the same
 * call. Once frozen, every subsequent call (forced or not) replays the same
 * credentials instead of hitting the network again.
 */
export function createConnectTokenSource(
  slug: string,
  handlers: ConnectTokenSourceHandlers = {},
  access: SessionAccess = DEFAULT_SESSION_ACCESS,
): ConnectTokenSource {
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
        const details = await fetchConnect(
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
    // A no-op until a connect has been attempted: freezing only exists to stop a
    // *second* POST /connect. React StrictMode (on by default in `next dev`) runs
    // the session effect's cleanup — which calls freeze() — before re-running the
    // effect on the same memoised source; freezing an unused source there would
    // make the real start() throw "session_closed" and never connect.
    freeze: () => {
      if (last !== null || inflight !== null) frozen = true;
    },
  };
}

/* -------------------------------------------------------------------------- */
/* Connection state                                                           */
/* -------------------------------------------------------------------------- */

/** The four states panels see (CONTRACTS §11 `PanelProps.connectionState`). */
export type PanelConnectionState =
  | "connecting"
  | "connected"
  | "reconnecting"
  | "disconnected";

export function toPanelConnectionState(
  state: ConnectionState,
): PanelConnectionState {
  switch (state) {
    case ConnectionState.Connected:
      return "connected";
    case ConnectionState.Connecting:
      return "connecting";
    case ConnectionState.Reconnecting:
    case ConnectionState.SignalReconnecting:
      return "reconnecting";
    default:
      return "disconnected";
  }
}

/** Human-readable message for anything thrown by the connect endpoint. */
export function describeConnectError(error: unknown): string {
  if (error instanceof ConnectError) {
    if (error.isForbidden) {
      return "This agent is not published yet, so it cannot take calls.";
    }
    if (error.isNotFound) {
      return "No agent exists at this address.";
    }
    return error.message;
  }
  // Network/DNS failures surface as opaque `fetch failed` errors; don't leak
  // transport detail into the visitor-facing card.
  return "Could not reach the agent service. Please try again in a moment.";
}
