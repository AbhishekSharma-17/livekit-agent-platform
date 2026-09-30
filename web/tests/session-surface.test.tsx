import * as React from "react";
import { readFileSync } from "node:fs";
import path from "node:path";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentPublicOut } from "@/contracts/lkap-contracts";
import { ConnectionBanner } from "@/components/session/connection-banner";
import { EndOfCallCard } from "@/components/session/end-of-call-card";
import { PreCallCard } from "@/components/session/pre-call-card";
import { SessionCard } from "@/components/session/session-card";
import { SessionShell } from "@/components/session/session-shell";
import { StageView } from "@/components/session/stage-view";

afterEach(cleanup);

vi.mock("@livekit/components-react", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@livekit/components-react")>()),
  VideoTrack: () => <video />,
}));

const AGENT: AgentPublicOut = {
  id: "agent-1",
  slug: "ada",
  name: "Ada",
  description: "Takes claims.",
  ui_panel_id: "generic",
  panel: { panel_id: "generic", layout: "side", blocks: [] },
  pipeline_mode: "cascaded",
  capabilities: { camera: false, screen_share: false, chat_input: false },
};

/** S7 (docs/ui/AUDIT.md §6, decision D3): the caller page on the design system. */
describe("session layout toaster", () => {
  it("uses the app's toast policy: no richColors, no top-center override", () => {
    const source = readFileSync(path.join(__dirname, "../src/app/(session)/layout.tsx"), "utf8");
    const toaster = source.match(/<Toaster[^>]*\/>/)?.[0] ?? "";
    expect(toaster).not.toBe("");
    expect(toaster).not.toMatch(/richColors/);
    expect(toaster).not.toMatch(/position=/);
  });
});

describe("SessionCard", () => {
  it("is a spec card: hairline and the 12 px radius, no shadow", () => {
    render(<SessionCard data-testid="card">body</SessionCard>);
    const card = screen.getByTestId("card");
    expect(card.className).toContain("rounded-lg");
    expect(card.className).not.toMatch(/\bshadow-/);
  });
});

describe("ConnectionBanner", () => {
  it("says the call is reconnecting and what happens next, with the shared spinner", () => {
    render(<ConnectionBanner agentState="reconnecting" />);
    const banner = screen.getByTestId("connection-banner");
    expect(banner.textContent).toMatch(/Reconnecting…/);
    expect(banner.textContent).toMatch(/continues when it.s back/);
    expect(banner.querySelector('[data-slot="spinner"]')).not.toBeNull();
    expect(banner.querySelector(".lucide")).toBeNull();
    expect(screen.getByRole("status").contains(banner)).toBe(true);
  });

  it("says so when the browser is offline mid-call, before LiveKit notices", () => {
    render(<ConnectionBanner agentState="listening" offline />);
    const banner = screen.getByTestId("connection-banner");
    expect(banner.textContent).toMatch(/You.re offline\. Reconnecting…/);
    expect(banner.textContent).toMatch(/continues when you.re back online/);
  });

  it("stays quiet while online, and once the call has ended", () => {
    const { rerender } = render(<ConnectionBanner agentState="listening" />);
    expect(screen.queryByTestId("connection-banner")).toBeNull();
    rerender(<ConnectionBanner agentState="ended" offline />);
    expect(screen.queryByTestId("connection-banner")).toBeNull();
  });
});

describe("StageView failure overlay", () => {
  it("has one alert, and puts the primary Try again last", () => {
    render(<StageView agentState="failed" agentName="Ada" onRetry={vi.fn()} onLeave={vi.fn()} />);
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    const buttons = screen.getAllByRole("button").map((button) => button.textContent);
    expect(buttons).toEqual(["Leave", "Try again"]);
    expect(screen.getByRole("button", { name: /try again/i }).getAttribute("data-variant")).toBe("primary");
  });

  it("gives a next step when LiveKit sent no reasons", () => {
    render(<StageView agentState="failed" agentName="Ada" onRetry={vi.fn()} />);
    expect(screen.getByRole("alert").textContent).toMatch(/try again/i);
  });
});

describe("SessionShell transcript sheet", () => {
  function renderShell(props: Partial<React.ComponentProps<typeof SessionShell>> = {}) {
    return render(
      <SessionShell
        layout="side"
        panelTitle="Panel"
        agentName="Ada"
        agentState="listening"
        stage={<div />}
        transcript={<div>line</div>}
        panel={<div />}
        controls={<div />}
        {...props}
      />,
    );
  }

  it("closes on Escape while open", () => {
    const onTranscriptOpenChange = vi.fn();
    renderShell({ transcriptOpen: true, transcriptCount: 1, onTranscriptOpenChange });
    fireEvent.keyDown(screen.getByTestId("session-transcript"), { key: "Escape" });
    expect(onTranscriptOpenChange).toHaveBeenCalledWith(false);
  });

  it("announces the transcript as a log", () => {
    renderShell();
    expect(screen.getByRole("log").textContent).toBe("line");
  });

  it("gives the phone controls 48 px targets", () => {
    renderShell({ transcriptCount: 2, transcriptOpen: true });
    expect(screen.getByTestId("transcript-chip").className).toContain("min-h-12");
    expect(screen.getByRole("button", { name: /hide transcript/i }).className).toContain("size-12");
  });
});

describe("PreCallCard states", () => {
  function renderCard(props: Partial<React.ComponentProps<typeof PreCallCard>> = {}) {
    return render(
      <PreCallCard
        agent={AGENT}
        participantName=""
        onParticipantNameChange={vi.fn()}
        onStart={vi.fn()}
        devices={{ status: "idle", level: 0, inputs: [] }}
        onCheckMicrophone={vi.fn()}
        onSelectInput={vi.fn()}
        {...props}
      />,
    );
  }

  it("says so when the browser is offline", () => {
    renderCard({ offline: true });
    expect(screen.getByTestId("pre-call-offline").textContent).toMatch(/offline/);
    cleanup();
    renderCard();
    expect(screen.queryByTestId("pre-call-offline")).toBeNull();
  });

  it("shows the last attempt's error as a danger alert", () => {
    renderCard({ error: "All lines are busy right now. Try again in a moment." });
    expect(screen.getByRole("alert").getAttribute("data-testid")).toBe("pre-call-error");
  });

  it("has exactly one primary action, Start call", () => {
    renderCard();
    const primaries = screen.getAllByRole("button").filter((button) => button.getAttribute("data-variant") === "primary");
    expect(primaries.map((button) => button.textContent)).toEqual(["Start call"]);
  });
});

describe("EndOfCallCard focus", () => {
  it("moves focus to its heading so the end of the call is announced", () => {
    render(<EndOfCallCard agentName="Ada" durationMs={5_000} onRestart={vi.fn()} />);
    expect(document.activeElement).toBe(screen.getByRole("heading", { level: 1 }));
  });
});
