import { afterEach, describe, expect, it, vi } from "vitest";

import { postToEmbedParent } from "@/components/session/embed/embed-bridge";

describe("postToEmbedParent", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("posts a tagged message to window.parent when framed", () => {
    const postMessage = vi.fn();
    // jsdom's `window.parent` is read-only (equals `window` at top level); stub it
    // to simulate being inside an iframe, the way widget.js's embed actually is.
    vi.spyOn(window, "parent", "get").mockReturnValue({ postMessage } as unknown as Window);

    postToEmbedParent({ type: "state", state: "connected" });

    expect(postMessage).toHaveBeenCalledWith({ source: "lkap-embed", type: "state", state: "connected" }, "*");
  });

  it("does nothing when not framed (window.parent === window)", () => {
    const postMessage = vi.fn();
    window.postMessage = postMessage;

    postToEmbedParent({ type: "close" });

    expect(postMessage).not.toHaveBeenCalled();
  });
});
