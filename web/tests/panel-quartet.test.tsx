import * as React from "react";

import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { AgentPublicOut, BlockSpec } from "@/contracts/lkap-contracts";
import { Block } from "@/panels/blocks";
import { stepsFromFlowProgress } from "@/panels/blocks/steps";
import {
  FIXTURE_LAYOUT,
  fixtureAssetUrls,
  fixtureUiState,
} from "@/panels/blocks/__fixtures__";
import choicesFixture from "@/panels/blocks/__fixtures__/choices.json";
import type { PanelProps } from "@/panels/registry";
import { nodeToolOptions } from "@/components/console/flow/tool-options";

/**
 * V5-12: the console block quartet — `choices`, `details`, `markdown`,
 * `steps`. `panel-blocks.test.tsx` (V2-11) covers the original twelve; this
 * file covers the four V5-08/V5-12 additions and the `flow_progress` →
 * `steps` renderer aliasing (D-V5-33).
 */

beforeAll(() => {
  Element.prototype.scrollIntoView = function scrollIntoView() {};
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

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

/* -------------------------------------------------------------------------- */

describe("choices block", () => {
  const spec = specOf("choices");

  it("renders its fixture: the prompt and every option, hint and tone included", () => {
    render(<Block spec={spec} {...panelProps()} />);
    expect(screen.getByText("Was anyone injured?")).toBeTruthy();
    expect(screen.getByRole("button", { name: "No" })).toBeTruthy();
    const serious = screen.getByRole("button", { name: "Yes, serious injuries" });
    expect(serious.getAttribute("title")).toBe("We will call you back first");
    // The option group has an accessible name (the prompt), not just visible text.
    expect(screen.getByRole("group", { name: "Was anyone injured?" })).toBeTruthy();
  });

  it("submits a single-choice answer the moment it's tapped, as block_submit", async () => {
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    render(<Block spec={spec} {...panelProps({ perform })} />);
    fireEvent.click(screen.getByRole("button", { name: "No" }));
    await waitFor(() =>
      expect(perform).toHaveBeenCalledWith({
        action: "block_submit",
        payload: { block_id: "injured", values: { selected: ["no"] } },
      }),
    );
  });

  it("collects every tap first for a multi-answer question, then sends them together", async () => {
    const multiState = fixtureUiState({
      injured: {
        prompt: "Which rooms were affected?",
        options: [
          { id: "kitchen", label: "Kitchen" },
          { id: "hallway", label: "Hallway" },
        ],
        multi: true,
        selected: [],
        reveal: null,
        status: "requested",
        submitted_at: null,
      },
    });
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    render(<Block spec={spec} {...panelProps({ state: multiState, perform })} />);
    expect(perform).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Kitchen" }));
    fireEvent.click(screen.getByRole("button", { name: "Hallway" }));
    expect(perform).not.toHaveBeenCalled(); // taps alone don't answer a multi question
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() =>
      expect(perform).toHaveBeenCalledWith({
        action: "block_submit",
        payload: { block_id: "injured", values: { selected: ["kitchen", "hallway"] } },
      }),
    );
  });

  it("dismisses with block_submit {cancelled: true}", async () => {
    const perform = vi.fn(async () => ({ ok: true, payload: {} }));
    render(<Block spec={spec} {...panelProps({ perform })} />);
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    await waitFor(() =>
      expect(perform).toHaveBeenCalledWith({ action: "block_submit", payload: { block_id: "injured", cancelled: true } }),
    );
  });

  it("shows a spoken answer (resolve_choice) as submitted, with no submit() call from the browser", () => {
    const perform = vi.fn();
    const state = fixtureUiState({
      injured: { ...choicesFixture, status: "submitted", selected: ["minor"], submitted_at: 1_777_000_500 },
    });
    render(<Block spec={spec} {...panelProps({ state, perform })} />);
    expect(screen.getByText("Yes, minor injuries")).toBeTruthy();
    expect(perform).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "No" })).toBeNull();
  });

  it("reads a stored-but-out-of-range answer as 'no answer' (ask #43)", () => {
    const state = fixtureUiState({
      injured: { ...choicesFixture, status: "submitted", selected: [] },
    });
    render(<Block spec={spec} {...panelProps({ state })} />);
    expect(screen.getByText("No answer recorded")).toBeTruthy();
  });

  it("shows a quiz reveal: correct/incorrect tones and the explanation", () => {
    const state = fixtureUiState({
      injured: {
        ...choicesFixture,
        status: "submitted",
        selected: ["minor"],
        reveal: { correct: ["no"], explanation: "The claimant confirmed nobody was hurt." },
      },
    });
    render(<Block spec={spec} {...panelProps({ state })} />);
    expect(screen.getByText("The claimant confirmed nobody was hurt.")).toBeTruthy();
    expect(screen.getByText(/Correct answer/)).toBeTruthy();
  });

  it("has an idle empty state and a cancelled state", () => {
    const { rerender } = render(
      <Block spec={spec} {...panelProps({ state: fixtureUiState({ injured: { status: "idle" } }) })} />,
    );
    expect(screen.getByText("The agent will ask a question here when it has one.")).toBeTruthy();
    rerender(<Block spec={spec} {...panelProps({ state: fixtureUiState({ injured: { status: "cancelled" } }) })} />);
    expect(screen.getByText("You dismissed this without answering.")).toBeTruthy();
  });
});

/* -------------------------------------------------------------------------- */

describe("details block", () => {
  const spec = specOf("details");

  it("renders its fixture with typed formatting: money, a bare date, a badge, a null value", () => {
    render(<Block spec={spec} {...panelProps()} />);
    const block = screen.getByTestId("block-details");
    expect(within(block).getByText("CLM-20931")).toBeTruthy();
    expect(within(block).getByText("2026-09-21")).toBeTruthy(); // bare date, no timezone shift
    expect(within(block).getByText("$4,200.00")).toBeTruthy();
    expect(within(block).getByText("First notice")).toBeTruthy(); // badge chip
    expect(within(block).getByText("Not yet")).toBeTruthy(); // phone: null
  });

  it("marks a toned row with a dot, never a badge type twice", () => {
    render(<Block spec={spec} {...panelProps()} />);
    const stage = screen.getByTestId("block-details").querySelector('[data-key="stage"]');
    // "badge" carries its tone on the chip itself; no extra dot.
    expect(stage?.querySelector('[data-slot="details-tone-dot"]')).toBeNull();
  });

  it("uses two columns from config.columns", () => {
    render(<Block spec={spec} {...panelProps()} />);
    expect(document.querySelector('[data-slot="block-details"]')?.className).toContain("sm:grid-cols-2");
  });

  it("has an empty state", () => {
    render(<Block spec={spec} {...panelProps({ state: fixtureUiState({ claim: { items: [] } }) })} />);
    expect(screen.getByText("No details yet.")).toBeTruthy();
  });

  it("renders phone and email rows as tel:/mailto: links", () => {
    const state = fixtureUiState({
      claim: {
        items: [
          { key: "phone", label: "Call-back number", value: "+1 555 0100", type: "phone" },
          { key: "email", label: "Email", value: "priya@example.com", type: "email" },
        ],
      },
    });
    render(<Block spec={spec} {...panelProps({ state })} />);
    const phone = screen.getByRole("link", { name: "+1 555 0100" });
    expect(phone.getAttribute("href")).toBe("tel:+1 555 0100");
    const email = screen.getByRole("link", { name: "priya@example.com" });
    expect(email.getAttribute("href")).toBe("mailto:priya@example.com");
  });
});

/* -------------------------------------------------------------------------- */

describe("markdown block", () => {
  const spec = specOf("markdown");

  async function renderMarkdown(overrides: Partial<PanelProps> & { spec?: BlockSpec } = {}) {
    const { spec: specOverride, ...panelOverrides } = overrides;
    const result = render(<Block spec={specOverride ?? spec} {...panelProps(panelOverrides)} />);
    // A generous timeout: this is a lazy `React.lazy` import (streamdown's
    // own chunk), so it can take longer than testing-library's 1s default
    // under a loaded CI/dev machine.
    await waitFor(() => expect(screen.getByTestId("block-markdown").getAttribute("data-loading")).toBeNull(), {
      timeout: 10_000,
    });
    return result;
  }

  it("renders its fixture (heading, list) and strips the embedded <script>", async () => {
    const { container } = await renderMarkdown();
    expect(screen.getByText("Your recap")).toBeTruthy(); // the block's own state.title
    expect(screen.getByText("What happens next")).toBeTruthy();
    expect(screen.getByText(/loss adjuster calls you/)).toBeTruthy();
    expect(container.textContent).not.toContain("never rendered");
    expect(container.querySelector("script")).toBeNull();
  });

  it("never renders a clickable link when allow_links is off (the default)", async () => {
    const state = fixtureUiState({
      recap: { markdown: "See [the policy](https://example.com/policy) for details.", title: null, updated_at: null },
    });
    await renderMarkdown({ state });
    expect(screen.getByText(/the policy/)).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("renders a safe link (new tab) only when allow_links is on, and drops an unsafe scheme", async () => {
    const linkSpec: BlockSpec = { id: "recap", type: "markdown", title: null, config: { allow_links: true } };
    const state = fixtureUiState({
      recap: {
        markdown: "[Open the policy](https://example.com/policy) or [nope](javascript:alert(1))",
        title: null,
        updated_at: null,
      },
    });
    await renderMarkdown({ state, spec: linkSpec });
    const link = screen.getByRole("link", { name: "Open the policy" });
    expect(link.getAttribute("href")).toBe("https://example.com/policy");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
    // The unsafe-scheme link's text still shows, unwrapped — never a control.
    expect(link.parentElement?.textContent).toContain("nope");
    expect(screen.queryByRole("link", { name: "nope" })).toBeNull();
  });

  it("only shows an image that resolves to a known session asset, never an arbitrary URL", async () => {
    const state = fixtureUiState({
      recap: {
        markdown: "![the stove](frame-stove)\n\n![tracker](https://evil.test/tracker.png)",
        title: null,
        updated_at: null,
      },
    });
    await renderMarkdown({ state, assets: fixtureAssetUrls() });
    const img = screen.getByAltText("the stove");
    expect(img.getAttribute("src")).toMatch(/^data:image\/svg\+xml/);
    expect(screen.queryByAltText("tracker")).toBeNull();
  });

  it("has an empty state with no markdown", async () => {
    const state = fixtureUiState({ recap: { markdown: "", title: null, updated_at: null } });
    await renderMarkdown({ state });
    expect(screen.getByText(/hasn.t written anything here yet/)).toBeTruthy();
  });
});

/* -------------------------------------------------------------------------- */

describe("steps block", () => {
  const spec = specOf("steps");

  it("renders its fixture: every step's status, a note, and a timestamp", () => {
    render(<Block spec={spec} {...panelProps()} />);
    const el = screen.getByTestId("block-steps");
    expect(within(el).getByText("Welcome")).toBeTruthy();
    expect(within(el).getByText("Kitchen fire, stove top")).toBeTruthy();
    const rows = el.querySelectorAll('[data-slot="block-step"]');
    expect([...rows].map((r) => r.getAttribute("data-status"))).toEqual(["done", "done", "active", "pending"]);
  });

  it("hides notes when config.show_notes is false", () => {
    const noNotes: BlockSpec = { id: "progress", type: "steps", title: "Progress", config: { show_notes: false } };
    render(<Block spec={noNotes} {...panelProps()} />);
    expect(screen.queryByText("Kitchen fire, stove top")).toBeNull();
  });

  it("has an empty state", () => {
    render(<Block spec={spec} {...panelProps({ state: fixtureUiState({ progress: { steps: [] } }) })} />);
    expect(screen.getByText("No steps yet.")).toBeTruthy();
  });

  describe("stepsFromFlowProgress (D-V5-33)", () => {
    it("marks every visited node done, the current node active, and passes the flow's own label through", () => {
      expect(
        stepsFromFlowProgress({ current_node: "confirm", label: "Confirm", path: ["collect", "confirm"], disposition: null }),
      ).toEqual({
        steps: [
          { id: "collect", label: "collect", status: "done" },
          { id: "confirm", label: "Confirm", status: "active" },
        ],
        current: "confirm",
      });
    });

    it("marks the current node done and reports no current step once the flow ends", () => {
      expect(
        stepsFromFlowProgress({ current_node: "done", label: "Done", path: ["collect", "done"], disposition: "resolved" }),
      ).toEqual({
        steps: [
          { id: "collect", label: "collect", status: "done" },
          { id: "done", label: "Done", status: "done" },
        ],
        current: null,
      });
    });

    it("degrades to an empty timeline for malformed input", () => {
      expect(stepsFromFlowProgress(null)).toEqual({ steps: [], current: null });
      expect(stepsFromFlowProgress({})).toEqual({ steps: [], current: null });
    });
  });

  it("a `custom` flow_progress block renders identically to a `steps` block with the same data (D-V5-33)", () => {
    const equivalentSteps = {
      steps: [
        { id: "collect", label: "collect", status: "done" },
        { id: "confirm", label: "Confirm", status: "active" },
      ],
      current: "confirm",
    };
    const stepsSpec: BlockSpec = { id: "a", type: "steps", title: "Progress", config: {} };
    const { container: viaSteps } = render(
      <Block spec={stepsSpec} {...panelProps({ state: fixtureUiState({ a: equivalentSteps }) })} />,
    );
    const stepsHtml = viaSteps.querySelector('[data-slot="block-steps"]')?.outerHTML;
    cleanup();

    const customSpec: BlockSpec = { id: "a", type: "custom", title: "Progress", config: { kind: "flow_progress" } };
    const mirror = { current_node: "confirm", label: "Confirm", path: ["collect", "confirm"], variables: {}, disposition: null };
    const { container: viaCustom } = render(
      <Block spec={customSpec} {...panelProps({ state: fixtureUiState({ a: mirror }) })} />,
    );
    const customHtml = viaCustom.querySelector('[data-slot="block-steps"]')?.outerHTML;

    expect(stepsHtml).toBeTruthy();
    expect(customHtml).toBe(stepsHtml);
  });
});

/* -------------------------------------------------------------------------- */

describe("set_steps tool gating follows a steps block's config.source (ask #43)", () => {
  function options(source?: string) {
    return nodeToolOptions({
      builtinDisabled: [],
      httpRequestEnabled: false,
      camera: false,
      screenShare: false,
      blocks: [{ type: "steps", config: source ? { source } : {} }],
      packToolNames: [],
      toolIds: [],
      toolNamesById: {},
    }).map((option) => option.name);
  }

  it("offers set_steps for a manual (or unset) steps block, not a flow-driven one", () => {
    expect(options(undefined)).toContain("set_steps");
    expect(options("manual")).toContain("set_steps");
    expect(options("flow")).not.toContain("set_steps");
  });

  it("still offers update_block regardless of a steps block's source", () => {
    expect(options("flow")).toContain("update_block");
  });
});

/* -------------------------------------------------------------------------- */

describe("kb_citations click-through (R-V5-5, ask #39)", () => {
  const spec = specOf("kb_citations");

  it("shows a locator line when the citation carries page/heading_path", () => {
    const state = fixtureUiState({
      sources: {
        items: [
          {
            chunk_id: "kb-home-7#3",
            filename: "home-policy-2026.pdf",
            score: 0.91,
            text: "Fire damage to the kitchen is covered up to the rebuild value.",
            page: 4,
            heading_path: ["Deductibles", "Wind and hail"],
          },
        ],
      },
    });
    render(<Block spec={spec} {...panelProps({ state })} />);
    expect(screen.getByText("page 4 · Deductibles › Wind and hail")).toBeTruthy();
  });

  it("sends block_action open_citation on tap, by chunk_id", async () => {
    const perform = vi.fn(async () => ({ opened: "document", block_id: "sources", page: 1 }));
    render(<Block spec={spec} {...panelProps({ perform })} />);
    fireEvent.click(screen.getByRole("button", { name: "home-policy-2026.pdf" }));
    await waitFor(() =>
      expect(perform).toHaveBeenCalledWith({
        action: "block_action",
        payload: { block_id: "sources", name: "open_citation", data: { chunk_id: "kb-home-7#3" } },
      }),
    );
    // The worker opened it in a document block itself; no dialog needed here.
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("previews the passage in a dialog when the agent answers opened: false", async () => {
    const perform = vi.fn(async () => ({
      opened: false,
      reason: "no_document_block",
      citation: {
        chunk_id: "kb-home-7#3",
        filename: "home-policy-2026.pdf",
        score: 0.91,
        text: "Fire damage to the kitchen is covered up to the rebuild value.",
        page: 4,
        heading_path: ["Deductibles"],
      },
    }));
    render(<Block spec={spec} {...panelProps({ perform })} />);
    fireEvent.click(screen.getByRole("button", { name: "home-policy-2026.pdf" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("home-policy-2026.pdf")).toBeTruthy();
    expect(within(dialog).getByText("page 4 · Deductibles")).toBeTruthy();
    expect(within(dialog).getByText("Fire damage to the kitchen is covered up to the rebuild value.")).toBeTruthy();
  });

  it("opens nothing for a stale tap (unknown_citation, no citation returned)", async () => {
    const perform = vi.fn(async () => ({ opened: false, reason: "unknown_citation" }));
    render(<Block spec={spec} {...panelProps({ perform })} />);
    fireEvent.click(screen.getByRole("button", { name: "claims-faq.md" }));
    await waitFor(() => expect(perform).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
