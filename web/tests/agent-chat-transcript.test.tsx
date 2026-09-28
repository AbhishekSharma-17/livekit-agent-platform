import * as React from "react";

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReceivedMessage } from "@livekit/components-react";

import { AgentChatTranscript } from "@/components/agents-ui/agent-chat-transcript";

/**
 * S6-8 (security review, ask #81): the transcript used to render every message
 * through a bare `<Streamdown>` — the only panel surface not already on the
 * shared `SafeMarkdown` helper (`@/lib/safe-markdown`, V6-10 ask #333). That let
 * a transcript message (the model's own output, or the caller's own text on a
 * text session) show a third-party image or a clickable link. This file pins
 * the fix: `AgentChatTranscript` now renders through `SafeMarkdown` with no
 * `assets` and links off, exactly like the transcript block's own strictest
 * settings.
 *
 * jsdom has no `ResizeObserver`; `@shadcn/react/message-scroller` (the scroll
 * machinery this component wraps) feature-detects it and simply skips the
 * auto-scroll behaviour without one — nothing to stub here.
 */
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function message(text: string): ReceivedMessage {
  return {
    id: "m1",
    timestamp: 1777000000000,
    message: text,
    from: { isLocal: false } as ReceivedMessage["from"],
  } as ReceivedMessage;
}

describe("AgentChatTranscript — SafeMarkdown, not a bare Streamdown", () => {
  it("renders no <img> for a third-party image in a transcript message", () => {
    render(<AgentChatTranscript messages={[message("Here you go: ![x](https://third.party/x.png)")]} />);
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("renders no clickable link for an https link in a transcript message", () => {
    render(<AgentChatTranscript messages={[message("See [the policy](https://example.com/policy) for details.")]} />);
    expect(screen.getByText(/the policy/)).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("strips an embedded <script> the same way every other panel surface does", () => {
    const { container } = render(
      <AgentChatTranscript messages={[message("Before <script>window.x = 1</script> after")]} />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText(/Before/)).toBeTruthy();
  });

  it("still shows plain message text (the transcript keeps working)", () => {
    render(<AgentChatTranscript messages={[message("Plain text, no markdown at all.")]} />);
    expect(screen.getByText("Plain text, no markdown at all.")).toBeTruthy();
  });
});
