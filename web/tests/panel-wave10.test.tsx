import * as React from "react";
import { EventEmitter } from "node:events";

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RoomContext } from "@livekit/components-react";
import { ParticipantKind, RoomEvent, type Room } from "livekit-client";

import type {
  AgentPublicOut,
  BlockSpec,
  CardsBlockState,
  LinkBlockState,
  SlotsBlockState,
} from "@/contracts/lkap-contracts";
import { Block } from "@/panels/blocks";
import { workingOnLine } from "@/panels/blocks/activity";
import { BLOCK_FIXTURE_STATES, FIXTURE_LAYOUT, fixtureAssetUrls, fixtureUiState } from "@/panels/blocks/__fixtures__";
import { checkedHttpsUrl, hostAllowed, httpsUrlProblem, normalizeHost } from "@/panels/blocks/types";
import type { PanelProps } from "@/panels/registry";

/**
 * V5-43 -> V5-44: the `link`, `slots` and `cards` renderers (`docs/v5/PLAN-V5.md`
 * V5-44, `docs/v5/_asks.md` #310), plus the `activity` block's "working on"
 * line (V5-44's E2). Named `panel-wave10` for the wave both packages shipped
 * in, matching `panel-quartet.test.tsx` / `panel-handoff.test.tsx`'s
 * per-wave split.
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

function specOf(type: BlockSpec["type"]): BlockSpec {
  const spec = FIXTURE_LAYOUT.blocks.find((b) => b.type === type);
  if (!spec) throw new Error(type);
  return spec;
}

function specWith(type: BlockSpec["type"], config: Record<string, unknown>): BlockSpec {
  const spec = specOf(type);
  return { ...spec, config: { ...(spec.config as Record<string, unknown>), ...config } };
}

/* -------------------------------------------------------------------------- */
/* Host allowlist / safe-link checks (`types.ts`, mirrors ui_protocol.py)      */
/* -------------------------------------------------------------------------- */

describe("host allowlist checks (types.ts, mirrors lkap_contracts.ui_protocol)", () => {
  it("normalizeHost accepts a bare domain and a *.wildcard, lower-cased", () => {
    expect(normalizeHost("Example.com")).toBe("example.com");
    expect(normalizeHost("*.Example.com")).toBe("*.example.com");
    expect(normalizeHost("example.com.")).toBe("example.com");
  });

  it("normalizeHost refuses anything that is not a site name", () => {
    expect(normalizeHost("localhost")).toBeNull(); // no dot
    expect(normalizeHost("192.168.0.1")).toBeNull(); // numeric host
    expect(normalizeHost("https://example.com")).toBeNull(); // a scheme, not a site name
    expect(normalizeHost("example.com/path")).toBeNull(); // a path
    expect(normalizeHost("  ")).toBeNull();
  });

  it("hostAllowed matches an exact entry and a *.wildcard's sub-domains, never the bare domain from a wildcard", () => {
    expect(hostAllowed("example.com", ["example.com"])).toBe(true);
    expect(hostAllowed("pay.example.com", ["*.example.com"])).toBe(true);
    expect(hostAllowed("example.com", ["*.example.com"])).toBe(false);
    expect(hostAllowed("evil.com", ["example.com"])).toBe(false);
    expect(hostAllowed("example.com", [])).toBe(false);
  });

  it("httpsUrlProblem refuses javascript:, data: and plain http:", () => {
    expect(httpsUrlProblem("javascript:alert(1)")).toBeTruthy();
    expect(httpsUrlProblem("data:text/html,<script>1</script>")).toBeTruthy();
    expect(httpsUrlProblem("http://example.com")).toBeTruthy();
  });

  it("httpsUrlProblem refuses a host outside allowedHosts and accepts one inside it", () => {
    expect(httpsUrlProblem("https://evil.com/pay", ["example.com"])).toBeTruthy();
    expect(httpsUrlProblem("https://example.com/pay?ref=1", ["example.com"])).toBeNull();
    expect(httpsUrlProblem("https://pay.example.com", ["*.example.com"])).toBeNull();
  });

  it("httpsUrlProblem refuses a user name/password, and an IP literal", () => {
    expect(httpsUrlProblem("https://user:pass@example.com", ["example.com"])).toBeTruthy();
    expect(httpsUrlProblem("https://1.2.3.4/", ["1.2.3.4"])).toBeTruthy();
  });

  it("checkedHttpsUrl returns the url only when it passes, else null", () => {
    expect(checkedHttpsUrl("https://example.com/x", ["example.com"])).toBe("https://example.com/x");
    expect(checkedHttpsUrl("https://evil.com/x", ["example.com"])).toBeNull();
    expect(checkedHttpsUrl(null, ["example.com"])).toBeNull();
    expect(checkedHttpsUrl(undefined, ["example.com"])).toBeNull();
  });
});

/* -------------------------------------------------------------------------- */
/* link block                                                                  */
/* -------------------------------------------------------------------------- */

describe("link block", () => {
  const SPEC = specOf("link");
  const FIXTURE_STATE = BLOCK_FIXTURE_STATES.link as LinkBlockState;

  function stateWith(patch: Partial<LinkBlockState>) {
    return fixtureUiState({ payment: { ...FIXTURE_STATE, ...patch } });
  }

  it("idle: nothing sent yet", () => {
    render(<Block spec={SPEC} {...panelProps({ state: stateWith({ status: "idle", url: null }) })} />);
    expect(screen.getByText(/will show a link here/)).toBeTruthy();
  });

  it("pending: opens in a new tab, shows the site name and the status, and posts block_action 'opened' on click", async () => {
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    render(<Block spec={SPEC} {...panelProps({ perform, state: stateWith({}) })} />);
    const link = screen.getByRole("link", { name: /Pay the excess/ });
    expect(link.getAttribute("href")).toBe(FIXTURE_STATE.url);
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
    expect(link.getAttribute("rel")).toContain("noreferrer");
    expect(screen.getByText("example.com")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain("Waiting for you to open it");

    fireEvent.click(link);
    await waitFor(() =>
      expect(perform).toHaveBeenCalledWith({
        action: "block_action",
        payload: { block_id: "payment", name: "opened" },
      }),
    );
  });

  it("shows a QR code while pending/opened, and hides it once completed", async () => {
    const { rerender } = render(<Block spec={SPEC} {...panelProps({ state: stateWith({ status: "pending" }) })} />);
    expect(await screen.findByRole("img", { name: /QR code for example.com/ })).toBeTruthy();

    rerender(<Block spec={SPEC} {...panelProps({ state: stateWith({ status: "completed" }) })} />);
    expect(screen.queryByRole("img", { name: /QR code/ })).toBeNull();
    expect(screen.getByRole("status").textContent).toContain("Completed");
  });

  it("never renders a link whose host is not in allowed_hosts, even though the state carries one", () => {
    render(
      <Block
        spec={SPEC}
        {...panelProps({ state: stateWith({ url: "https://evil.com/pay?ref=1", status: "pending" }) })}
      />,
    );
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText(/can.t be shown safely/)).toBeTruthy();
  });

  it("never renders a javascript: or data: url even if it somehow reached the state", () => {
    render(<Block spec={SPEC} {...panelProps({ state: stateWith({ url: "javascript:alert(1)" }) })} />);
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("shows a note when the link was sent by text message", () => {
    render(<Block spec={SPEC} {...panelProps({ state: stateWith({ channel: "sms" }) })} />);
    expect(screen.getByText(/text message to your phone/)).toBeTruthy();
  });

  it("shows the expiry when the state carries one", () => {
    render(<Block spec={SPEC} {...panelProps({ state: stateWith({ expires_at: 1790001800 }) })} />);
    expect(screen.getByText(/Expires/)).toBeTruthy();
  });

  it("open_in: dialog opens a preview dialog with an 'Open in a new tab' fallback, instead of a plain link", async () => {
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    render(
      <Block spec={specWith("link", { open_in: "dialog" })} {...panelProps({ perform, state: stateWith({}) })} />,
    );
    expect(screen.queryByRole("link", { name: /Pay the excess/ })).toBeNull();
    const button = screen.getByRole("button", { name: /Pay the excess/ });
    fireEvent.click(button);
    await waitFor(() => expect(perform).toHaveBeenCalledTimes(1));
    const dialog = screen.getByRole("dialog");
    const iframe = dialog.querySelector("iframe");
    expect(iframe?.getAttribute("src")).toBe(FIXTURE_STATE.url);
    expect(iframe?.getAttribute("sandbox")).toContain("allow-forms");
    expect(iframe?.getAttribute("referrerpolicy")).toBe("no-referrer");
    const fallback = within(dialog).getByRole("link", { name: /Open in a new tab/ });
    expect(fallback.getAttribute("href")).toBe(FIXTURE_STATE.url);
  });
});

/* -------------------------------------------------------------------------- */
/* slots block                                                                 */
/* -------------------------------------------------------------------------- */

describe("slots block", () => {
  const SPEC = specOf("slots");
  const FIXTURE_STATE = BLOCK_FIXTURE_STATES.slots as SlotsBlockState;

  function stateWith(patch: Partial<SlotsBlockState>) {
    return fixtureUiState({ inspection: { ...FIXTURE_STATE, ...patch } });
  }

  it("idle: nothing offered yet", () => {
    render(<Block spec={SPEC} {...panelProps({ state: stateWith({ status: "idle", slots: [] }) })} />);
    expect(screen.getByText(/will offer times here/)).toBeTruthy();
  });

  it("requested: groups the fixture's three slots by day, in the block's own timezone", () => {
    render(<Block spec={SPEC} {...panelProps({ state: stateWith({}) })} />);
    expect(screen.getByText("Monday 5 October")).toBeTruthy();
    expect(screen.getByText("Tuesday 6 October")).toBeTruthy();
    expect(screen.getByText(/09:00.*10:00/)).toBeTruthy();
    // The times are stated as being in the block's timezone (Europe/London).
    expect(screen.getByText(/Europe\/London/)).toBeTruthy();
  });

  it("tapping a slot submits only {selected}, never start/end (the worker takes those from its own slots)", async () => {
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    render(<Block spec={SPEC} {...panelProps({ perform, state: stateWith({}) })} />);
    const button = screen.getByRole("button", { name: /09:00.*10:00/ });
    fireEvent.click(button);
    await waitFor(() =>
      expect(perform).toHaveBeenCalledWith({
        action: "block_submit",
        payload: { block_id: "inspection", values: { selected: "mon_am" } },
      }),
    );
  });

  it("a full slot (capacity 0) shows disabled, never omitted", () => {
    const slots = FIXTURE_STATE.slots?.map((slot) => (slot.id === "mon_pm" ? { ...slot, capacity: 0 } : slot));
    render(<Block spec={SPEC} {...panelProps({ state: stateWith({ slots }) })} />);
    const full = screen.getByRole("button", { name: /Full/ });
    expect(full.hasAttribute("disabled")).toBe(true);
  });

  it("submitted: shows the picked day and time", () => {
    render(<Block spec={SPEC} {...panelProps({ state: stateWith({ status: "submitted", selected: "tue_am" }) })} />);
    expect(screen.getByText(/Tuesday 6 October/)).toBeTruthy();
  });

  it("cancelled: says it was dismissed", () => {
    render(<Block spec={SPEC} {...panelProps({ state: stateWith({ status: "cancelled" }) })} />);
    expect(screen.getByText(/dismissed this without picking/)).toBeTruthy();
  });

  it("allow_custom shows a hint only when the config asks for it", () => {
    const { rerender } = render(<Block spec={SPEC} {...panelProps({ state: stateWith({}) })} />);
    expect(screen.queryByText(/tell the agent another time/)).toBeNull();
    rerender(
      <Block spec={specWith("slots", { allow_custom: true })} {...panelProps({ state: stateWith({}) })} />,
    );
    expect(screen.getByText(/tell the agent another time/)).toBeTruthy();
  });

  it("falls back to the viewer's zone without throwing when the state carries an unrecognised timezone", () => {
    expect(() =>
      render(<Block spec={SPEC} {...panelProps({ state: stateWith({ timezone: "Nowhere/Invalid" }) })} />),
    ).not.toThrow();
  });
});

/* -------------------------------------------------------------------------- */
/* cards block                                                                 */
/* -------------------------------------------------------------------------- */

describe("cards block", () => {
  const SPEC = specOf("cards");
  const FIXTURE_STATE = BLOCK_FIXTURE_STATES.cards as CardsBlockState;

  function stateWith(patch: Partial<CardsBlockState>) {
    return fixtureUiState({ plans: { ...FIXTURE_STATE, ...patch } });
  }

  it("no cards yet: the empty state", () => {
    render(<Block spec={SPEC} {...panelProps({ state: stateWith({ cards: [] }) })} />);
    expect(screen.getByText(/will show cards here/)).toBeTruthy();
  });

  it("renders both fixture cards with facts and the selected ring, and an allowed image_url loads lazily with no referrer", () => {
    render(<Block spec={SPEC} {...panelProps({ state: stateWith({}) })} />);
    expect(screen.getByText("Silver cover")).toBeTruthy();
    expect(screen.getByText("Gold cover")).toBeTruthy();
    expect(screen.getByText("£250")).toBeTruthy();
    expect(screen.getByText("Recommended")).toBeTruthy();
    const gold = screen.getByRole("button", { name: /Selected: Gold cover/ });
    expect(gold.closest("article")?.getAttribute("data-selected")).toBe("true");
    const image = screen.getByAltText("Gold cover") as HTMLImageElement;
    expect(image.getAttribute("src")).toBe("https://example.com/img/gold.png");
    expect(image.getAttribute("loading")).toBe("lazy");
    expect(image.getAttribute("referrerpolicy")).toBe("no-referrer");
  });

  it("never renders an image_url outside the block's image_hosts", () => {
    const cards = FIXTURE_STATE.cards?.map((card) =>
      card.id === "gold" ? { ...card, image_url: "https://evil.com/img.png" } : card,
    ) as CardsBlockState["cards"];
    render(<Block spec={SPEC} {...panelProps({ state: stateWith({ cards }) })} />);
    expect(screen.queryByAltText("Gold cover")).toBeNull();
  });

  it("an empty image_hosts config means asset pictures only, never an image_url", () => {
    const cards = FIXTURE_STATE.cards?.map((card) =>
      card.id === "gold" ? { ...card, image_url: "https://example.com/img/gold.png" } : card,
    ) as CardsBlockState["cards"];
    render(
      <Block
        spec={specWith("cards", { image_hosts: [] })}
        {...panelProps({ state: stateWith({ cards }) })}
      />,
    );
    expect(screen.queryByAltText("Gold cover")).toBeNull();
  });

  it("tapping a card sends block_action select with its card_id", async () => {
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    render(<Block spec={SPEC} {...panelProps({ perform, state: stateWith({}) })} />);
    fireEvent.click(screen.getByRole("button", { name: /Choose Silver cover/ }));
    await waitFor(() =>
      expect(perform).toHaveBeenCalledWith({
        action: "block_action",
        payload: { block_id: "plans", name: "select", data: { card_id: "silver" } },
      }),
    );
  });

  it("tapping a card's own action button sends that action's name, not 'select' — the two are siblings, never nested", async () => {
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    render(<Block spec={SPEC} {...panelProps({ perform, state: stateWith({}) })} />);
    fireEvent.click(screen.getByRole("button", { name: "Choose Gold" }));
    await waitFor(() =>
      expect(perform).toHaveBeenCalledWith({
        action: "block_action",
        payload: { block_id: "plans", name: "choose", data: { card_id: "gold" } },
      }),
    );
    expect(perform).toHaveBeenCalledTimes(1);
  });

  it("selectable: false renders no card-select button, but action buttons still work", () => {
    render(<Block spec={specWith("cards", { selectable: false })} {...panelProps({ state: stateWith({}) })} />);
    expect(screen.queryByRole("button", { name: /Choose Silver cover/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Choose Silver" })).toBeTruthy();
  });
});

/* -------------------------------------------------------------------------- */
/* activity block — the "working on" line (V5-44, plan §E2)                    */
/* -------------------------------------------------------------------------- */

describe("workingOnLine (pure)", () => {
  const running = { v: 1 as const, id: "t1", ts: 1, source: "s", label: "L", phase: "running" as const, headline: "Holding Thursday morning", urgent: false };
  const done = { v: 1 as const, id: "t2", ts: 2, source: "s", label: "L", phase: "done" as const, headline: "Found it", urgent: false };

  it("prefers the newest running tool's headline over the room state", () => {
    expect(workingOnLine("listening", [running])).toBe("Working on: Holding Thursday morning");
  });

  it("falls back to the room's own state in plain words when nothing is running", () => {
    expect(workingOnLine("listening", [done])).toBe("Listening");
    expect(workingOnLine("thinking", [])).toBe("Thinking");
    expect(workingOnLine("speaking", [])).toBe("Speaking");
  });

  it("shows nothing for a connecting/idle/unknown state or no room at all", () => {
    expect(workingOnLine("idle", [])).toBeNull();
    expect(workingOnLine("initializing", [])).toBeNull();
    expect(workingOnLine(null, [])).toBeNull();
  });
});

interface FakeAgentParticipant {
  kind: ParticipantKind;
  attributes: Record<string, string>;
}

function fakeAgentRoom(initial: string | null) {
  const emitter = new EventEmitter();
  const remoteParticipants = new Map<string, FakeAgentParticipant>();
  if (initial !== null) remoteParticipants.set("agent-1", { kind: ParticipantKind.AGENT, attributes: { "lk.agent.state": initial } });
  const room = {
    remoteParticipants,
    on: (event: string, listener: (...args: unknown[]) => void) => emitter.on(event, listener),
    off: (event: string, listener: (...args: unknown[]) => void) => emitter.off(event, listener),
  };
  function setState(next: string) {
    remoteParticipants.set("agent-1", { kind: ParticipantKind.AGENT, attributes: { "lk.agent.state": next } });
    emitter.emit(RoomEvent.ParticipantAttributesChanged, {}, {});
  }
  return { room: room as unknown as Room, setState };
}

describe("activity block — live agent_state (V5-44)", () => {
  const SPEC = specOf("activity");

  function stateNoRunningTool() {
    return { ...fixtureUiState(), activity: [] };
  }

  it("shows the room's agent_state once the fake room reports it, and updates on a fixture sequence", async () => {
    const { room, setState } = fakeAgentRoom("listening");
    render(
      <RoomContext.Provider value={room}>
        <Block spec={SPEC} {...panelProps({ state: stateNoRunningTool() })} />
      </RoomContext.Provider>,
    );
    expect(await screen.findByText("Listening")).toBeTruthy();

    act(() => setState("thinking"));
    expect(await screen.findByText("Thinking")).toBeTruthy();

    act(() => setState("speaking"));
    expect(await screen.findByText("Speaking")).toBeTruthy();
  });

  it("shows nothing extra outside a room (the console preview), only the activity list itself", async () => {
    render(<Block spec={SPEC} {...panelProps({ state: stateNoRunningTool() })} />);
    await waitFor(() => expect(screen.getByTestId("block-activity").getAttribute("data-loading")).toBeNull());
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("the running tool's headline wins over the room's state even inside a room", async () => {
    const { room } = fakeAgentRoom("listening");
    render(
      <RoomContext.Provider value={room}>
        <Block spec={SPEC} {...panelProps()} />
      </RoomContext.Provider>,
    );
    expect(await screen.findByText("Working on: Holding Thursday morning")).toBeTruthy();
  });
});
