import * as React from "react";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EndOfCallCard } from "@/components/session/end-of-call-card";

afterEach(cleanup);

/** UI_UX_SPEC §5.6. */
describe("EndOfCallCard", () => {
  it("says who and how long, and offers another call", () => {
    const onRestart = vi.fn();
    render(
      <EndOfCallCard agentName="Ada" durationMs={252_000} onRestart={onRestart} />,
    );
    expect(screen.getByText("Call ended")).toBeTruthy();
    expect(
      screen.getByText(/You talked with Ada for 4 min 12 s\./),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /start another call/i }));
    expect(onRestart).toHaveBeenCalledTimes(1);
  });

  it("adds the way back to the editor in test mode only", () => {
    render(
      <EndOfCallCard
        agentName="Ada"
        durationMs={1_000}
        onRestart={vi.fn()}
        testMode
        backHref="/console/agents/agent-1"
      />,
    );
    // Exactly one way back: the card's secondary action (§5.6). The test-mode
    // bar still marks the call as a test but carries no second link here.
    const links = screen.getAllByRole("link", { name: /back to editor/i });
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute("href")).toBe("/console/agents/agent-1");
    expect(screen.getByTestId("test-mode-bar").textContent).toMatch(
      /Test call · this agent is a draft/,
    );

    cleanup();
    render(
      <EndOfCallCard
        agentName="Ada"
        durationMs={1_000}
        onRestart={vi.fn()}
        backHref="/console/agents/agent-1"
      />,
    );
    expect(screen.queryByRole("link", { name: /back to editor/i })).toBeNull();
  });
});
