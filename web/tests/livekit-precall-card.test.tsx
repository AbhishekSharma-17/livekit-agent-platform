import * as React from "react";

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentPublicOut } from "@/contracts/lkap-contracts";
import { PreCallCard } from "@/components/session/pre-call-card";
import type { MicDevices } from "@/hooks/use-mic-check";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const AGENT: AgentPublicOut = {
  id: "agent-1",
  slug: "smoke-generic",
  name: "Smoke Generic",
  description: "A draft agent used for smoke testing.",
  ui_panel_id: "generic",
  pipeline_mode: "cascaded",
  capabilities: { camera: false, screen_share: false, chat_input: false },
};

const IDLE: MicDevices = { status: "idle", level: 0, inputs: [] };

function card(props: Partial<React.ComponentProps<typeof PreCallCard>> = {}) {
  return (
    <PreCallCard
      agent={AGENT}
      participantName=""
      onParticipantNameChange={vi.fn()}
      onStart={vi.fn()}
      devices={IDLE}
      onCheckMicrophone={vi.fn()}
      onSelectInput={vi.fn()}
      {...props}
    />
  );
}

function renderCard(props: Partial<React.ComponentProps<typeof PreCallCard>> = {}) {
  return render(card(props));
}

/**
 * UI_UX_SPEC §5.1. The card takes its device state as a prop, so every state
 * is renderable without touching browser permissions.
 */
describe("PreCallCard device check", () => {
  it("never asks for permission on its own: idle offers the check", () => {
    const onCheckMicrophone = vi.fn();
    renderCard({ onCheckMicrophone });
    expect(screen.getByTestId("mic-status").textContent).toMatch(
      /We'll ask for your microphone when you start/,
    );
    fireEvent.click(screen.getByRole("button", { name: /check microphone/i }));
    expect(onCheckMicrophone).toHaveBeenCalledTimes(1);
  });

  it("disables Start while permission is pending", () => {
    const { container } = renderCard({
      devices: { ...IDLE, status: "requesting" },
    });
    const start = container.querySelector(
      'button[type="submit"]',
    ) as HTMLButtonElement;
    expect(start.disabled).toBe(true);
    expect(start.textContent).toMatch(/Waiting for permission/);
  });

  it("shows a live level meter once the microphone works", () => {
    const { container } = renderCard({
      devices: { ...IDLE, status: "granted", level: 0.6 },
    });
    expect(screen.getByTestId("mic-status").textContent).toBe(
      "Microphone works",
    );
    const meters = container.querySelectorAll(
      '[data-slot="state-meter"][data-level]',
    );
    expect(meters.length).toBe(1);
  });

  it("explains how to unblock a denied microphone and offers a reload", () => {
    renderCard({ devices: { ...IDLE, status: "denied" } });
    expect(screen.getByTestId("mic-status").textContent).toMatch(
      /Microphone is blocked/,
    );
    expect(screen.getByRole("button", { name: /reload/i })).toBeTruthy();
  });

  it("hydrates the denied hint without a mismatch, then shows the browser's steps", async () => {
    const denied = { devices: { ...IDLE, status: "denied" as const } };

    // Server render: no `navigator`, so the hint is the generic fallback.
    vi.stubGlobal("navigator", undefined);
    const html = renderToString(card(denied));
    vi.unstubAllGlobals();
    expect(html).toContain("Allow the microphone for this site");

    // Client: a Chrome UA is available from the very first render.
    vi.stubGlobal("navigator", {
      userAgent:
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    });
    const container = document.createElement("div");
    container.innerHTML = html;
    document.body.appendChild(container);
    const onRecoverableError = vi.fn();
    const root = await act(async () =>
      hydrateRoot(container, card(denied), { onRecoverableError }),
    );

    try {
      expect(onRecoverableError).not.toHaveBeenCalled();
      expect(container.textContent).toMatch(/In Chrome: the lock icon/);
    } finally {
      act(() => root.unmount());
      container.remove();
    }
  });

  it("says so when the browser cannot capture audio at all", () => {
    renderCard({ devices: { ...IDLE, status: "unsupported" } });
    expect(screen.getByTestId("mic-status").textContent).toMatch(
      /can't capture audio/,
    );
  });

  it("only offers an input picker when there is a choice", () => {
    renderCard({ devices: { ...IDLE, status: "granted" } });
    expect(screen.queryByLabelText(/microphone input/i)).toBeNull();

    cleanup();
    renderCard({
      devices: {
        status: "granted",
        level: 0,
        selectedId: "a",
        inputs: [
          { deviceId: "a", label: "Built-in", kind: "audioinput" },
          { deviceId: "b", label: "Headset", kind: "audioinput" },
        ] as unknown as MediaDeviceInfo[],
      },
    });
    expect(screen.getByLabelText(/microphone input/i)).toBeTruthy();
  });
});

describe("PreCallCard content", () => {
  it("starts the call from the form", () => {
    const onStart = vi.fn();
    renderCard({ onStart });
    fireEvent.click(screen.getByRole("button", { name: /start call/i }));
    expect(onStart).toHaveBeenCalledTimes(1);
  });

  it("derives 'what to expect' from the agent's capabilities", () => {
    renderCard({
      agent: {
        ...AGENT,
        capabilities: { camera: true, screen_share: false, chat_input: true },
      },
    });
    expect(
      screen.getAllByText(/speaks first and listens while you talk/).length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText(/turn on your camera/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/share your screen/)).toBeNull();
    expect(screen.getAllByText(/You can also type/).length).toBeGreaterThan(0);
  });

  it("defaults the name field to empty with a Guest placeholder", () => {
    renderCard();
    const input = screen.getByLabelText(/your name/i) as HTMLInputElement;
    expect(input.value).toBe("");
    expect(input.placeholder).toBe("Guest");
  });

  it("shows a previous attempt's error above Start", () => {
    renderCard({ error: "Could not reach the agent service." });
    expect(screen.getByTestId("pre-call-error").textContent).toMatch(
      /Could not reach the agent service/,
    );
  });
});

/**
 * DECISIONS-W2 D-W2-1 item 3 / UI_UX_SPEC §5.1: test mode is a slim bar with a
 * way back to the editor — never a "draft agents allowed" badge for visitors.
 */
describe("PreCallCard test mode", () => {
  it("shows the test bar and the editor link", () => {
    renderCard({ testMode: true, backHref: "/console/agents/agent-1" });
    const bar = screen.getByTestId("test-mode-bar");
    expect(bar.textContent).toMatch(/Test call · this agent is a draft/);
    expect(
      screen.getByRole("link", { name: /back to editor/i }).getAttribute("href"),
    ).toBe("/console/agents/agent-1");
    expect(screen.queryByText(/draft agents allowed/i)).toBeNull();
  });

  it("shows nothing of the sort for a normal (published) call", () => {
    renderCard();
    expect(screen.queryByTestId("test-mode-bar")).toBeNull();
  });
});
