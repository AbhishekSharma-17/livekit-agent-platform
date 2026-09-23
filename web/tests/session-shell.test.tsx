import * as React from "react";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SessionShell } from "@/components/session/session-shell";
import { sessionLayoutModel } from "@/components/session/session-layout";

afterEach(cleanup);

function renderShell(
  props: Partial<React.ComponentProps<typeof SessionShell>> = {},
) {
  return render(
    <SessionShell
      layout="side"
      panelTitle="Claim notebook"
      agentName="Ada"
      agentState="listening"
      stage={<div>stage body</div>}
      transcript={<div>transcript body</div>}
      panel={<div>panel body</div>}
      controls={<div>controls body</div>}
      {...props}
    />,
  );
}

/**
 * UI_UX_SPEC §5.3. The breakpoint behaviour lives in class strings (no
 * `matchMedia`), so the model is asserted directly and the DOM test covers
 * what the shell renders around it.
 */
describe("sessionLayoutModel", () => {
  it("puts the panel first on tablets in the wide layout only", () => {
    expect(sessionLayoutModel("wide").order).toEqual({
      stage: { mobile: 1, tablet: 2 },
      panel: { mobile: 2, tablet: 1 },
    });
    expect(sessionLayoutModel("side").order).toEqual({
      stage: { mobile: 1, tablet: 1 },
      panel: { mobile: 2, tablet: 2 },
    });
  });

  it("gives each layout its desktop grid", () => {
    expect(sessionLayoutModel("side").grid).toContain(
      "lg:grid-cols-[minmax(0,1fr)_400px]",
    );
    expect(sessionLayoutModel("wide").grid).toContain(
      "lg:grid-cols-[340px_minmax(0,1fr)]",
    );
  });

  it("keeps the mobile stage a strip in wide and a 45vh stage in side", () => {
    expect(sessionLayoutModel("wide").stage).toContain("min-h-[72px]");
    expect(sessionLayoutModel("side").stage).toContain("h-[45vh]");
  });

  it("fixes the controls to the safe area below lg and unpins them at lg", () => {
    const controls = sessionLayoutModel("side").controls;
    expect(controls).toContain("fixed");
    expect(controls).toContain("pb-[env(safe-area-inset-bottom,0px)]");
    expect(controls).toContain("lg:static");
  });

  it("makes the transcript a bottom sheet below lg and a column at lg", () => {
    const transcript = sessionLayoutModel("side").transcript;
    expect(transcript).toContain("fixed");
    expect(transcript).toContain("h-[60vh]");
    expect(transcript).toContain("data-[open=false]:pointer-events-none");
    expect(transcript).toContain("lg:static");
  });
});

describe("SessionShell", () => {
  it("renders every region and labels the layout", () => {
    renderShell();
    expect(screen.getByTestId("session-shell").getAttribute("data-layout")).toBe(
      "side",
    );
    expect(screen.getByText("stage body")).toBeTruthy();
    expect(screen.getByText("panel body")).toBeTruthy();
    expect(screen.getByText("transcript body")).toBeTruthy();
    expect(screen.getByText("controls body")).toBeTruthy();
    expect(screen.getByTestId("session-controls")).toBeTruthy();
  });

  it("keeps DOM order stage → panel → transcript → controls in both layouts", () => {
    for (const layout of ["side", "wide"] as const) {
      cleanup();
      const { container } = renderShell({ layout });
      const ids = Array.from(
        container.querySelectorAll("[data-testid]"),
      )
        .map((node) => node.getAttribute("data-testid"))
        .filter((id) =>
          [
            "session-stage",
            "session-panel-column",
            "session-transcript",
            "session-controls",
          ].includes(id ?? ""),
        );
      expect(ids).toEqual([
        "session-stage",
        "session-panel-column",
        "session-transcript",
        "session-controls",
      ]);
    }
  });

  it("shows the live chip and the timer only while the agent is live", () => {
    renderShell({ agentState: "listening", elapsedMs: 252_000 });
    expect(screen.getByTestId("top-strip-elapsed").textContent).toBe("4:12");
    expect(screen.getByText("Live")).toBeTruthy();
  });

  it("shows a connection chip instead while connecting", () => {
    renderShell({ agentState: "connecting", elapsedMs: 1_000 });
    expect(screen.getByTestId("connection-chip").textContent).toMatch(
      /Connecting…/,
    );
    expect(screen.queryByTestId("top-strip-elapsed")).toBeNull();
  });

  it("opens and closes the transcript sheet from the strip chip", () => {
    const onTranscriptOpenChange = vi.fn();
    renderShell({ transcriptCount: 3, onTranscriptOpenChange });
    expect(screen.getByTestId("session-transcript").getAttribute("data-open")).toBe(
      "false",
    );
    fireEvent.click(screen.getByTestId("transcript-chip"));
    expect(onTranscriptOpenChange).toHaveBeenCalledWith(true);

    cleanup();
    renderShell({
      transcriptCount: 3,
      transcriptOpen: true,
      onTranscriptOpenChange,
    });
    expect(screen.getByTestId("session-transcript").getAttribute("data-open")).toBe(
      "true",
    );
    fireEvent.click(screen.getByRole("button", { name: /hide transcript/i }));
    expect(onTranscriptOpenChange).toHaveBeenLastCalledWith(false);
  });

  it("renders the test bar and the panel status chip when given", () => {
    renderShell({
      testBar: <div data-testid="test-bar-slot">test</div>,
      panelStatus: <span>Claim open</span>,
    });
    expect(screen.getByTestId("test-bar-slot")).toBeTruthy();
    expect(screen.getByText("Claim open")).toBeTruthy();
  });
});
