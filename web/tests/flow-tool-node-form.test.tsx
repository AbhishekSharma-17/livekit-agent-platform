import { readFileSync } from "node:fs";
import path from "node:path";
import * as React from "react";

import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import { NodeForm } from "@/components/console/flow/node-form";
import type { ToolNode } from "@/contracts/lkap-contracts";
import type { JsonSchema } from "@/lib/schema-form";

/**
 * V6-19 (ask #117): the flow editor's `tool` node inspector — the node card
 * and ADDABLE list were the V6-17 stop-gap (`flow-model.test.ts`'s "tool
 * steps" describe block); this covers what V6-19 finishes: the tool picker
 * (the agent's attached rows only), the server-tool sub-picker for an MCP
 * row, argument templates (ctx/var only), bindings, and the outcome edges.
 */

const nativeMatches = Element.prototype.matches;
beforeAll(() => {
  Element.prototype.matches = function matches(this: Element, selector: string) {
    if (selector === ":popover-open" || selector === ":modal") return false;
    return nativeMatches.call(this, selector);
  };
  // jsdom has no layout, so Radix's Select scrolls the highlighted item into
  // view on open — a no-op here (`console-mcp-presets.test.tsx`'s pattern).
  Element.prototype.scrollIntoView = vi.fn();
});
afterAll(() => {
  Element.prototype.matches = nativeMatches;
});

const TOOL_NODE_SCHEMA = JSON.parse(
  readFileSync(path.resolve(__dirname, "../../contracts/generated/schemas/ToolNode.schema.json"), "utf8"),
) as JsonSchema;

const NODE: ToolNode = {
  id: "lookup",
  kind: "tool",
  label: "Look up the policy",
  position: [0, 0],
  tool: "record_lookup",
  mcp_tool: null,
  arguments: {},
  bindings: [],
  timeout_s: 10,
  on: { ok: "e_ok" },
};

function renderForm(node: ToolNode = NODE, onChange = vi.fn()) {
  render(
    <NodeForm
      node={node}
      schema={TOOL_NODE_SCHEMA}
      onChange={onChange}
      variables={[{ name: "policy_number", type: "string" }]}
      toolOptions={[]}
      toolStepOptions={[
        { name: "record_lookup", label: "record_lookup", kind: "dataset", mcpToolNames: null, argumentNames: ["policy_number"] },
        { name: "crm_server", label: "crm_server", kind: "mcp", mcpToolNames: ["get_account", "get_orders"], argumentNames: [] },
      ]}
      kbOptions={[]}
      providerOptions={[]}
      edgeOptions={[
        { id: "e_ok", label: "Tell them" },
        { id: "e_error", label: "Apologise" },
      ]}
    />,
  );
  return onChange;
}

describe("ToolNode inspector", () => {
  it("offers only the agent's attached rows, no built-ins", () => {
    renderForm();
    fireEvent.click(screen.getByLabelText("Tool"));
    const listbox = screen.getByRole("listbox");
    expect(within(listbox).getByText("record_lookup")).toBeTruthy();
    expect(within(listbox).getByText("crm_server")).toBeTruthy();
    expect(within(listbox).queryByText("end_call")).toBeNull();
  });

  it("shows a server-tool picker only for an MCP row, seeded from its allowed/cached tools", () => {
    renderForm({ ...NODE, tool: "crm_server", mcp_tool: null });
    expect(screen.getByLabelText("Server tool")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Server tool"));
    const listbox = screen.getByRole("listbox");
    expect(within(listbox).getByText("get_account")).toBeTruthy();
    expect(within(listbox).getByText("get_orders")).toBeTruthy();
  });

  it("hides the server-tool picker for a non-MCP row", () => {
    renderForm();
    expect(screen.queryByLabelText("Server tool")).toBeNull();
  });

  it("reseeds arguments and clears mcp_tool when the tool changes", () => {
    const onChange = renderForm({ ...NODE, tool: "crm_server", mcp_tool: "get_account", arguments: { foo: "bar" } });
    fireEvent.click(screen.getByLabelText("Tool"));
    fireEvent.click(screen.getByRole("option", { name: "record_lookup" }));
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ tool: "record_lookup", mcp_tool: null, arguments: { policy_number: "" } }),
    );
  });

  it("flags a bare {{ name }} argument template — a tool step has no arguments of its own", () => {
    renderForm({ ...NODE, arguments: { policy_number: "{{ policy_number }}" } });
    expect(screen.getByText(/is not a value this step can fill/)).toBeTruthy();
  });

  it("accepts a var. placeholder with no issue shown", () => {
    renderForm({ ...NODE, arguments: { policy_number: "{{ var.policy_number }}" } });
    expect(screen.queryByText(/is not a value this step can fill/)).toBeNull();
  });

  it("renders the outcomes with plain-word labels and assigns an edge", () => {
    const onChange = renderForm({ ...NODE, on: {} });
    expect(screen.getByText("Found")).toBeTruthy();
    expect(screen.getByText("Nothing found")).toBeTruthy();
    expect(screen.getByText("Failed")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Found"));
    fireEvent.click(screen.getByRole("option", { name: "Tell them" }));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ on: { ok: "e_ok" } }));
  });

  it("renders the bindings editor", () => {
    renderForm();
    expect(screen.getByText("Put the result on the panel")).toBeTruthy();
  });
});
