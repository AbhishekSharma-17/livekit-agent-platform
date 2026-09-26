import { describe, expect, it } from "vitest";

import {
  fieldMessages,
  flowDraftIssues,
  groupIssues,
  hasErrors,
  insertMention,
  issueTarget,
  mentionQuery,
  normalizeFlow,
  removeNode,
  renameVariable,
  seedFlow,
  uniqueNodeId,
  type FlowDraft,
} from "@/components/console/flow/flow-model";
import { nodeToolOptions } from "@/components/console/flow/tool-options";

/** V2-16: the flow builder's pure model — structural checks, issue → node mapping, mentions, pickers. */

function intake(): FlowDraft {
  return normalizeFlow({
    nodes: [
      { id: "start", kind: "start", greeting: "Hi" },
      { id: "collect", kind: "agent", label: "Collect", instructions: "Ask for the name.", extract: ["name"] },
      { id: "done", kind: "end", disposition: "completed" },
      { id: "rules", kind: "global", instructions: "Be kind." },
    ],
    edges: [
      { id: "e1", source: "start", target: "collect", condition: "always" },
      { id: "e2", source: "collect", target: "done", condition: "The caller gave their name." },
    ],
    variables: [{ name: "name", type: "string" }],
  });
}

describe("flowDraftIssues", () => {
  it("accepts a well-formed flow and the seeded flow", () => {
    expect(flowDraftIssues(intake())).toEqual([]);
    expect(hasErrors(flowDraftIssues(seedFlow()))).toBe(false);
  });

  it("puts each structural problem on the node or edge that has it", () => {
    const flow = intake();
    flow.nodes.push({ id: "orphan", kind: "agent", instructions: "x" });
    flow.edges.push({ id: "e3", source: "done", target: "collect", condition: "never" });
    flow.edges.push({ id: "e4", source: "collect", target: "rules", condition: "x" });
    flow.nodes[1] = { ...flow.nodes[1], extract: ["name", "phone"] } as FlowDraft["nodes"][number];

    const issues = flowDraftIssues(flow);
    const grouped = groupIssues(issues, flow);

    expect(grouped.nodes.get("orphan")?.errors[0].message).toMatch(/not reachable/i);
    expect(grouped.nodes.get("collect")?.errors.map((issue) => issue.path)).toEqual(["flow.nodes[1].extract[1]"]);
    expect(grouped.edges.get("e3")?.errors[0].message).toMatch(/no outgoing/i);
    expect(grouped.edges.get("e4")?.errors[0].message).toMatch(/can't be connected/i);
  });

  it("flags a second start node on that node", () => {
    const flow = intake();
    flow.nodes.push({ id: "start_2", kind: "start" });
    const grouped = groupIssues(flowDraftIssues(flow), flow);
    expect(grouped.nodes.get("start_2")?.errors[0].message).toMatch(/exactly one start/i);
  });

  it("flags a missing start node at the flow level", () => {
    const issues = flowDraftIssues(normalizeFlow({ nodes: [{ id: "a", kind: "agent", instructions: "x" }], edges: [] }));
    const grouped = groupIssues(issues, normalizeFlow({ nodes: [{ id: "a", kind: "agent" }], edges: [] }));
    expect(grouped.general.map((issue) => issue.message)).toContain("A flow needs a start node.");
  });

  it("warns about an empty edge condition", () => {
    const flow = intake();
    flow.edges[1] = { ...flow.edges[1], condition: "" };
    const grouped = groupIssues(flowDraftIssues(flow), flow);
    expect(grouped.edges.get("e2")?.warnings).toHaveLength(1);
  });
});

describe("issue paths → canvas targets", () => {
  it("maps api (bracket) and editor-normalised (dotted) paths to the same node", () => {
    const flow = intake();
    expect(issueTarget("flow.nodes[1].tools[0]", flow)).toEqual({ kind: "node", id: "collect", field: "tools" });
    expect(issueTarget("flow.nodes.1.tools.0", flow)).toEqual({ kind: "node", id: "collect", field: "tools" });
    expect(issueTarget("flow.nodes[1].providers.llm", flow)).toEqual({
      kind: "node",
      id: "collect",
      field: "providers.llm",
    });
    expect(issueTarget("flow.edges[0].condition", flow)).toEqual({ kind: "edge", id: "e1", field: "condition" });
    expect(issueTarget("pipeline.llm", flow)).toBeNull();
  });

  it("gives the inspector a message per field", () => {
    const flow = intake();
    const grouped = groupIssues(
      [{ path: "flow.nodes[1].tools[0]", message: "unknown tool 'x'", severity: "error" }],
      flow,
    );
    expect(fieldMessages(grouped.nodes.get("collect"), flow)).toEqual({ errors: { tools: "unknown tool 'x'" }, warnings: {} });
  });
});

describe("editing helpers", () => {
  it("derives pattern-safe unique ids", () => {
    expect(uniqueNodeId("Collect details", [])).toBe("collect_details");
    expect(uniqueNodeId("Collect details", ["collect_details"])).toBe("collect_details_2");
    expect(uniqueNodeId("2nd step", [])).toBe("step_2nd_step");
  });

  it("removes a node with its edges and renames extracted variables", () => {
    const removed = removeNode(intake(), "collect");
    expect(removed.edges).toEqual([]);
    const renamed = renameVariable(intake(), "name", "full_name");
    expect(renamed.variables[0].name).toBe("full_name");
    expect((renamed.nodes[1] as { extract?: string[] }).extract).toEqual(["full_name"]);
  });
});

describe("@mention", () => {
  it("finds the query being typed and inserts a placeholder", () => {
    expect(mentionQuery("Confirm @na", 11)).toEqual({ start: 8, query: "na" });
    expect(mentionQuery("mail@example", 12)).toBeNull();
    expect(insertMention("Confirm @na please", 11, "name")).toEqual({
      text: "Confirm {{ name }} please",
      caret: 18,
    });
  });
});

describe("node tool pickers (R-V2-10)", () => {
  it("offer exactly the agent-level union", () => {
    const names = nodeToolOptions({
      builtinDisabled: ["push_note"],
      httpRequestEnabled: false,
      camera: false,
      screenShare: false,
      blocks: [{ type: "document" }],
      packToolNames: ["lookup_policy"],
      toolIds: ["t1"],
      toolNamesById: { t1: "crm", t2: "not_selected" },
    }).map((option) => option.name);

    expect(names).toEqual(
      expect.arrayContaining(["end_call", "update_block", "show_document", "lookup_policy", "crm"]),
    );
    for (const absent of ["push_note", "http_request", "pin_frame", "table_append", "not_selected"]) {
      expect(names).not.toContain(absent);
    }
  });
});
