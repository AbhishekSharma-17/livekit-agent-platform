import { readFileSync } from "node:fs";
import path from "node:path";

import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";

import type { AnyFlowNode } from "@/components/console/flow/flow-model";
import { NodeForm } from "@/components/console/flow/node-form";
import { flowListsKnowledge, kbScopeFor, kbTag, type KbScope } from "@/components/console/flow/use-node-options";
import type { JsonSchema } from "@/lib/schema-form";

/**
 * V4-11 / R-V4-29: a flow that lists no knowledge base searches all of the
 * agent's from every step, so the node form says what a step actually
 * searches — everything (inherited), the Global node's picks plus its own, or
 * nothing — and the canvas tag is the same computation.
 */

const nativeMatches = Element.prototype.matches;
beforeAll(() => {
  Element.prototype.matches = function matches(this: Element, selector: string) {
    if (selector === ":popover-open" || selector === ":modal") return false;
    return nativeMatches.call(this, selector);
  };
});
afterAll(() => {
  Element.prototype.matches = nativeMatches;
});

const AGENT_NODE_SCHEMA = JSON.parse(
  readFileSync(path.resolve(__dirname, "../../contracts/generated/schemas/AgentNode.schema.json"), "utf8"),
) as JsonSchema;

const AGENT_KBS = ["kb_hours", "kb_prices"];
const KB_OPTIONS = [
  { value: "kb_hours", label: "Opening hours" },
  { value: "kb_prices", label: "Price list" },
];

function flow({ global = [], ask = [], other = [] }: { global?: string[]; ask?: string[]; other?: string[] }): AnyFlowNode[] {
  return [
    { id: "start", kind: "start" },
    { id: "g", kind: "global", label: "Global", kb_ids: global },
    { id: "ask", kind: "agent", label: "Ask", instructions: "Ask.", kb_ids: ask },
    { id: "other", kind: "agent", label: "Other", instructions: "Other.", kb_ids: other },
    { id: "done", kind: "end" },
  ];
}

function node(nodes: AnyFlowNode[], id: string): AnyFlowNode {
  const found = nodes.find((item) => item.id === id);
  if (!found) throw new Error(`no node ${id}`);
  return found;
}

function renderKbField(target: AnyFlowNode, kbScope: KbScope | null, kbOptions = KB_OPTIONS): HTMLElement {
  render(
    <NodeForm
      node={target}
      schema={AGENT_NODE_SCHEMA}
      onChange={vi.fn()}
      variables={[]}
      toolOptions={[]}
      kbOptions={kbOptions}
      providerOptions={[]}
      kbScope={kbScope}
    />,
  );
  return document.querySelector('[data-field-name="kb_ids"]') as HTMLElement;
}

describe("kbScopeFor", () => {
  it("inherits every agent knowledge base when no node lists one", () => {
    const nodes = flow({});
    expect(flowListsKnowledge(nodes)).toBe(false);
    for (const id of ["g", "ask", "other"]) {
      expect(kbScopeFor(node(nodes, id), nodes, AGENT_KBS)).toEqual({ state: "inherits", ids: AGENT_KBS, fromGlobal: [] });
    }
    expect(kbScopeFor(node(nodes, "done"), nodes, AGENT_KBS)).toBeNull();
    expect(kbTag(kbScopeFor(node(nodes, "ask"), nodes, AGENT_KBS))).toBe("KB: all");
    // No knowledge bases on the agent: nothing to tag.
    expect(kbTag(kbScopeFor(node(nodes, "ask"), nodes, []))).toBeNull();
  });

  it("adds the Global node's picks to every step once the flow lists one", () => {
    const nodes = flow({ global: ["kb_hours"], ask: ["kb_prices"] });
    expect(kbScopeFor(node(nodes, "ask"), nodes, AGENT_KBS)).toEqual({
      state: "scoped",
      ids: ["kb_hours", "kb_prices"],
      fromGlobal: ["kb_hours"],
    });
    expect(kbScopeFor(node(nodes, "other"), nodes, AGENT_KBS)).toEqual({
      state: "scoped",
      ids: ["kb_hours"],
      fromGlobal: ["kb_hours"],
    });
    expect(kbTag(kbScopeFor(node(nodes, "ask"), nodes, AGENT_KBS))).toBe("KB: 2");
  });

  it("leaves a step with nothing listed searching nothing when another step lists one", () => {
    const nodes = flow({ ask: ["kb_prices"] });
    expect(kbScopeFor(node(nodes, "other"), nodes, AGENT_KBS)).toEqual({ state: "none", ids: [], fromGlobal: [] });
    expect(kbTag(kbScopeFor(node(nodes, "other"), nodes, AGENT_KBS))).toBe("KB: none");
    // A pick the agent no longer has counts as a narrowing but is never searched.
    const stale = flow({ ask: ["kb_gone"] });
    expect(kbScopeFor(node(stale, "ask"), stale, AGENT_KBS)).toEqual({ state: "none", ids: [], fromGlobal: [] });
  });
});

describe("NodeForm knowledge field", () => {
  it("describes how a step's knowledge is chosen", () => {
    const nodes = flow({});
    const field = renderKbField(node(nodes, "ask"), kbScopeFor(node(nodes, "ask"), nodes, AGENT_KBS));
    expect(
      within(field).getByText(
        "A step searches what the Global node lists plus what is picked here; when no step lists anything, every step searches all of the agent's knowledge bases.",
      ),
    ).toBeTruthy();
  });

  it("says the step inherits all of the agent's knowledge bases when the flow lists none", () => {
    const nodes = flow({});
    const field = renderKbField(node(nodes, "ask"), kbScopeFor(node(nodes, "ask"), nodes, AGENT_KBS));
    expect(
      within(field).getByText(
        "Inherits all 2 knowledge bases of this agent. Pick some here or on the Global node to narrow.",
      ),
    ).toBeTruthy();
    expect(within(field).getAllByRole("checkbox")).toHaveLength(2);
    expect(within(field).queryByText(/From Global/)).toBeNull();
  });

  it("keeps the attach hint when the agent has no knowledge bases", () => {
    const nodes = flow({});
    const field = renderKbField(node(nodes, "ask"), kbScopeFor(node(nodes, "ask"), nodes, []), []);
    expect(within(field).getByText("Attach knowledge bases to the agent first (Knowledge section).")).toBeTruthy();
    expect(within(field).queryByText(/Inherits/)).toBeNull();
  });

  it("shows the Global node's picks as read-only chips ahead of the step's own", () => {
    const nodes = flow({ global: ["kb_hours"], ask: ["kb_prices"] });
    const field = renderKbField(node(nodes, "ask"), kbScopeFor(node(nodes, "ask"), nodes, AGENT_KBS));
    const chips = within(field).getByRole("list", { name: "Knowledge bases from the Global node" });
    expect(within(chips).getAllByRole("listitem").map((item) => item.textContent)).toEqual(["From Global: Opening hours"]);
    const own = within(field).getByRole("group");
    expect(chips.compareDocumentPosition(own) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(own).getByRole("checkbox", { name: /Price list/ }).getAttribute("data-state")).toBe("checked");
    expect(within(field).queryByText(/Inherits/)).toBeNull();
  });

  it("says the step searches nothing when the flow lists knowledge elsewhere", () => {
    const nodes = flow({ ask: ["kb_prices"] });
    const field = renderKbField(node(nodes, "other"), kbScopeFor(node(nodes, "other"), nodes, AGENT_KBS));
    expect(within(field).getByText("This step searches no knowledge base.")).toBeTruthy();
    expect(screen.queryByText(/Inherits/)).toBeNull();
  });
});
