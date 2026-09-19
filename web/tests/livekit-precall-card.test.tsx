import * as React from "react";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentPublicOut } from "@/contracts/lkap-contracts";
import { PreCallCard } from "@/components/session/pre-call-card";

afterEach(cleanup);

const AGENT: AgentPublicOut = {
  id: "agent-1",
  slug: "smoke-generic",
  name: "Smoke Generic",
  description: "A draft agent used for smoke testing.",
  ui_panel_id: "generic",
  pipeline_mode: "cascaded",
  capabilities: { camera: false, screen_share: false, chat_input: false },
};

/**
 * DECISIONS-W2 D-W2-1 item 3: the pre-call card shows a "Test mode" badge
 * whenever `/s/[slug]?mode=test` is in play, so a visitor can tell the call
 * is going through the console's admin proxy rather than the public route.
 */
describe("PreCallCard test mode badge", () => {
  it("shows the test-mode badge when testMode is true", () => {
    render(
      <PreCallCard
        agent={AGENT}
        participantName="Guest"
        onParticipantNameChange={vi.fn()}
        onStart={vi.fn()}
        testMode
      />,
    );

    expect(screen.getByText(/test mode/i)).toBeTruthy();
  });

  it("does not show the badge for a normal (published) call", () => {
    render(
      <PreCallCard
        agent={AGENT}
        participantName="Guest"
        onParticipantNameChange={vi.fn()}
        onStart={vi.fn()}
      />,
    );

    expect(screen.queryByText(/test mode/i)).toBeNull();
  });
});
