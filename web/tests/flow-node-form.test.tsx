import { readFileSync } from "node:fs";
import path from "node:path";
import * as React from "react";

import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import { NodeForm } from "@/components/console/flow/node-form";
import type { AgentNode } from "@/contracts/lkap-contracts";
import { fieldsFromSchema, type JsonSchema } from "@/lib/schema-form";

/**
 * V2-16 acceptance: "node form renders every field of `AgentNode` from the
 * schema". The schema is the committed contract export
 * (`contracts/generated/schemas/AgentNode.schema.json`, the same
 * `model_json_schema` `GET /v1/flows/node-specs` serves), so a field added to
 * the Python model shows up here without a web change.
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

const NODE: AgentNode = {
  id: "collect",
  kind: "agent",
  label: "Collect details",
  position: [40, 120],
  instructions: "Ask for {{ name }}.",
  tools: ["end_call"],
  kb_ids: [],
  extract: ["name"],
  allow_interruptions: null,
  providers: {},
  max_turns: 3,
};

function renderForm(onChange = vi.fn()) {
  render(
    <NodeForm
      node={NODE}
      schema={AGENT_NODE_SCHEMA}
      onChange={onChange}
      variables={[
        { name: "name", type: "string", description: "caller name" },
        { name: "phone", type: "phone" },
      ]}
      toolOptions={[
        { name: "end_call", label: "End call", group: "Built-in" },
        { name: "lookup_policy", label: "lookup_policy", group: "Pack" },
      ]}
      kbOptions={[{ value: "kb1", label: "Policies" }]}
      providerOptions={[{ id: "livekit-inference-llm", label: "LiveKit Inference LLM", kind: "llm" }]}
      errors={{ tools: "unknown tool 'x'" }}
    />,
  );
  return onChange;
}

describe("schema → fields", () => {
  it("maps every AgentNode property to a control kind", () => {
    const kinds = Object.fromEntries(fieldsFromSchema(AGENT_NODE_SCHEMA).map((field) => [field.name, field.kind]));
    expect(Object.keys(kinds).sort()).toEqual(Object.keys(AGENT_NODE_SCHEMA.properties ?? {}).sort());
    expect(kinds).toMatchObject({
      id: "text",
      kind: "const",
      label: "text",
      instructions: "text",
      tools: "string-list",
      max_turns: "integer",
      allow_interruptions: "boolean",
      position: "json",
      providers: "json",
    });
  });
});

describe("NodeForm", () => {
  it("renders a labelled control for every field of AgentNode", () => {
    renderForm();
    for (const name of Object.keys(AGENT_NODE_SCHEMA.properties ?? {})) {
      const container = document.querySelector(`[data-field-name="${name}"]`);
      expect(container, `field ${name}`).not.toBeNull();
      expect(container?.querySelector("label")?.textContent?.trim()).toBeTruthy();
    }
    expect(screen.getByLabelText("Id")).toHaveProperty("readOnly", true);
    expect(screen.getByLabelText("Kind")).toHaveProperty("value", "agent");
    expect(screen.getByLabelText("Name")).toHaveProperty("value", "Collect details");
    expect(screen.getByLabelText("Position")).toHaveProperty("value", "x 40 · y 120");
    expect(screen.getByLabelText("Max turns in this step")).toHaveProperty("value", "3");
    expect(screen.getByRole("combobox", { name: "Instructions" })).toHaveProperty("value", "Ask for {{ name }}.");
  });

  it("lists only the agent's tools and shows the field's issue", () => {
    renderForm();
    const tools = document.querySelector('[data-field-name="tools"]') as HTMLElement;
    expect(within(tools).getByRole("checkbox", { name: /End call/ }).getAttribute("data-state")).toBe("checked");
    expect(within(tools).getByRole("checkbox", { name: /lookup_policy/ }).getAttribute("data-state")).toBe("unchecked");
    expect(within(tools).getByText("unknown tool 'x'")).toBeTruthy();
    const extract = document.querySelector('[data-field-name="extract"]') as HTMLElement;
    expect(within(extract).getAllByRole("checkbox")).toHaveLength(2);
  });

  it("writes edits back as a node", () => {
    const onChange = renderForm();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Collect" } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ id: "collect", label: "Collect" }));

    const tools = document.querySelector('[data-field-name="tools"]') as HTMLElement;
    fireEvent.click(within(tools).getByRole("checkbox", { name: /lookup_policy/ }));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ tools: ["end_call", "lookup_policy"] }));
  });

  it("inserts a variable with @", () => {
    function Harness() {
      const [node, setNode] = React.useState<AgentNode>({ ...NODE, instructions: "" });
      return (
        <>
          <NodeForm
            node={node}
            schema={AGENT_NODE_SCHEMA}
            onChange={(next) => setNode(next as AgentNode)}
            variables={[{ name: "name" }, { name: "phone", type: "phone" }]}
            toolOptions={[]}
            kbOptions={[]}
            providerOptions={[]}
          />
          <output data-testid="instructions">{node.instructions}</output>
        </>
      );
    }
    render(<Harness />);
    const instructions = screen.getByRole("combobox", { name: "Instructions" }) as HTMLTextAreaElement;
    fireEvent.change(instructions, { target: { value: "Call them @ph" } });
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual(["{{ phone }}phone"]);
    fireEvent.keyDown(instructions, { key: "Enter" });
    expect(screen.getByTestId("instructions").textContent).toBe("Call them {{ phone }}");
  });
});
