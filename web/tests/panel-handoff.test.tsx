import * as React from "react";

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AgentPublicOut, BlockSpec, HandoffBlockState } from "@/contracts/lkap-contracts";
import { Block } from "@/panels/blocks";
import { BLOCK_FIXTURE_STATES, FIXTURE_LAYOUT, fixtureAssetUrls, fixtureUiState } from "@/panels/blocks/__fixtures__";
import type { PanelProps } from "@/panels/registry";

/**
 * `handoff` block (V5-32 -> V5-36): the caller-facing state machine
 * `transfer_call` drives (`requested -> connecting -> connected | timeout |
 * ended`). No LiveKit room is needed here (unlike `video`/`upload`/`captions`)
 * — the same reason it is not in `LAZY_BLOCK_TYPES`.
 */

function agent(): AgentPublicOut {
  return {
    id: "a-1",
    slug: "claims",
    name: "Maya",
    description: "",
    pipeline_mode: "cascaded",
    ui_panel_id: "composite",
    capabilities: {},
    panel: FIXTURE_LAYOUT as AgentPublicOut["panel"],
  };
}

function panelProps(overrides: Partial<PanelProps> = {}): PanelProps {
  return {
    state: fixtureUiState(),
    assets: fixtureAssetUrls(),
    agent: agent(),
    sessionId: "s-1",
    perform: vi.fn(async () => ({ ok: true, payload: {} })),
    transcript: [],
    connectionState: "connected",
    ...overrides,
  };
}

const SPEC = FIXTURE_LAYOUT.blocks.find((b) => b.type === "handoff") as BlockSpec;
const FIXTURE_STATE = BLOCK_FIXTURE_STATES.handoff as HandoffBlockState;

function stateWith(patch: Partial<HandoffBlockState>) {
  return fixtureUiState({ handoff: { ...FIXTURE_STATE, ...patch } });
}

function specWith(config: Record<string, unknown>): BlockSpec {
  return { ...SPEC, config: { ...(SPEC.config as Record<string, unknown>), ...config } };
}

describe("handoff block", () => {
  it("the fixture (connecting/warm) matches #211: 'Calling <target> first…'", () => {
    // FIXTURE_STATE itself, unpatched — the exact fixture the ask pins.
    expect(FIXTURE_STATE.status).toBe("connecting");
    expect(FIXTURE_STATE.mode).toBe("warm");
    render(<Block spec={SPEC} {...panelProps()} />);
    expect(screen.getByRole("status").textContent).toBe("Calling Claims desk first…");
  });

  it("idle: nothing has happened yet, no live-region text", () => {
    render(<Block spec={SPEC} {...panelProps({ state: stateWith({ status: "idle", mode: null, target: null }) })} />);
    expect(screen.getByText(/will show you here if it hands you over/)).toBeTruthy();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("requested: names the destination", () => {
    render(<Block spec={SPEC} {...panelProps({ state: stateWith({ status: "requested", target: "Front desk" }) })} />);
    expect(screen.getByRole("status").textContent).toBe("Handing you over to Front desk.");
  });

  it("requested: a missing target still reads in plain words", () => {
    render(<Block spec={SPEC} {...panelProps({ state: stateWith({ status: "requested", target: null }) })} />);
    expect(screen.getByRole("status").textContent).toBe("Handing you over to a person.");
  });

  it("connecting/cold: says it is connecting directly, not calling first", () => {
    render(<Block spec={SPEC} {...panelProps({ state: stateWith({ status: "connecting", mode: "cold" }) })} />);
    expect(screen.getByRole("status").textContent).toBe("Connecting you to Claims desk…");
  });

  it("connected: shows the person's name when show_agent_name is on", () => {
    render(
      <Block
        spec={SPEC}
        {...panelProps({ state: stateWith({ status: "connected", agent_name: "Priya", target: "Claims desk" }) })}
      />,
    );
    expect(screen.getByRole("status").textContent).toBe("You're talking to Priya.");
  });

  it("connected: falls back to the destination label when show_agent_name is off", () => {
    render(
      <Block
        spec={specWith({ show_agent_name: false })}
        {...panelProps({ state: stateWith({ status: "connected", agent_name: "Priya", target: "Claims desk" }) })}
      />,
    );
    expect(screen.getByRole("status").textContent).toBe("You're talking to Claims desk.");
  });

  it("connected: falls back to 'a person' with neither a name nor a target", () => {
    render(<Block spec={SPEC} {...panelProps({ state: stateWith({ status: "connected", agent_name: null, target: null }) })} />);
    expect(screen.getByRole("status").textContent).toBe("You're talking to a person.");
  });

  it("timeout: shows the agent's plain reason and says the agent is back", () => {
    render(<Block spec={SPEC} {...panelProps({ state: stateWith({ status: "timeout", reason: "Nobody answered." }) })} />);
    expect(screen.getByRole("status").textContent).toBe("Nobody answered. You're back with the agent.");
  });

  it("timeout: falls back to a plain default reason when the worker sends none", () => {
    render(<Block spec={SPEC} {...panelProps({ state: stateWith({ status: "timeout", reason: null }) })} />);
    expect(screen.getByRole("status").textContent).toBe("Nobody answered. You're back with the agent.");
  });

  it("ended: says the call was handed over", () => {
    render(<Block spec={SPEC} {...panelProps({ state: stateWith({ status: "ended" }) })} />);
    expect(screen.getByRole("status").textContent).toBe("Your call was handed over.");
  });

  it("shows the queue position only while waiting, and only when show_queue is on", () => {
    const { rerender } = render(
      <Block spec={SPEC} {...panelProps({ state: stateWith({ status: "requested", queue_position: 2 }) })} />,
    );
    expect(screen.getByText("You're number 2 in the queue.")).toBeTruthy();

    // Not shown once connected, even with a queue_position left over.
    rerender(<Block spec={SPEC} {...panelProps({ state: stateWith({ status: "connected", queue_position: 2 }) })} />);
    expect(screen.queryByText(/in the queue/)).toBeNull();

    // Not shown when the config turns it off.
    rerender(
      <Block
        spec={specWith({ show_queue: false })}
        {...panelProps({ state: stateWith({ status: "requested", queue_position: 2 }) })}
      />,
    );
    expect(screen.queryByText(/in the queue/)).toBeNull();
  });

  it("reads 'next' for a zero queue position", () => {
    render(<Block spec={SPEC} {...panelProps({ state: stateWith({ status: "requested", queue_position: 0 }) })} />);
    expect(screen.getByText("You're next in the queue.")).toBeTruthy();
  });

  it("keeps one persistent live region across a status change (a re-mounted region announces nothing)", () => {
    const { rerender } = render(
      <Block spec={SPEC} {...panelProps({ state: stateWith({ status: "requested", target: "Claims desk" }) })} />,
    );
    const region = screen.getByRole("status");
    expect(region.getAttribute("aria-live")).toBe("polite");

    rerender(<Block spec={SPEC} {...panelProps({ state: stateWith({ status: "connecting", mode: "warm", target: "Claims desk" }) })} />);
    expect(screen.getByRole("status")).toBe(region);
    expect(region.textContent).toBe("Calling Claims desk first…");
  });
});
