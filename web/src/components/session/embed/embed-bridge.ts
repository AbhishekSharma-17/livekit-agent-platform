/**
 * `postMessage` bridge from the embedded `/s/[slug]?embed=1` page to
 * `widget.js`'s parent-page listener (UI_UX_SPEC-V2-AMENDMENTS §2.6).
 *
 * Targets `"*"` deliberately: the parent's own origin is whatever
 * third-party site chose to embed this page, which this page cannot know in
 * advance (that is exactly what `allowed_origins`/the embed CSP already
 * gate — see `middleware.ts`). `widget.js` is the side that validates
 * `event.origin` before trusting a message, matching the standard
 * cross-origin `postMessage` pattern (the sender cannot restrict who's
 * allowed to *listen*, only the receiver can restrict who it *trusts*).
 */
export type EmbedBridgeMessage =
  | { type: "open" }
  | { type: "close" }
  | { type: "state"; state: "connecting" | "connected" | "ended" };

export function postToEmbedParent(message: EmbedBridgeMessage): void {
  if (typeof window === "undefined" || window.parent === window) return;
  window.parent.postMessage({ source: "lkap-embed", ...message }, "*");
}
