import dagre from "@dagrejs/dagre";

import { kindOf, UNCONNECTABLE_KINDS, type FlowDraft } from "./flow-model";

/**
 * "Tidy layout" for the flow canvas (dagre, top → bottom). Imported only by
 * the lazily loaded canvas chunk. Global and QA nodes take no edges, so they
 * are parked in a column to the left of the graph.
 */

export const NODE_WIDTH = 220;
export const NODE_HEIGHT = 72;

export function autoLayout(flow: FlowDraft): FlowDraft {
  const graph = new dagre.graphlib.Graph();
  graph.setGraph({ rankdir: "TB", nodesep: 48, ranksep: 72, marginx: 0, marginy: 0 });
  graph.setDefaultEdgeLabel(() => ({}));
  const connectable = flow.nodes.filter((node) => !UNCONNECTABLE_KINDS.includes(kindOf(node)));
  for (const node of connectable) graph.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  for (const edge of flow.edges) {
    if (graph.hasNode(edge.source) && graph.hasNode(edge.target)) graph.setEdge(edge.source, edge.target);
  }
  dagre.layout(graph);

  let minX = Infinity;
  for (const node of connectable) minX = Math.min(minX, graph.node(node.id)?.x ?? 0);
  const parkedX = (Number.isFinite(minX) ? minX : 0) - NODE_WIDTH * 1.5 - 48;
  let parkedY = 0;

  return {
    ...flow,
    nodes: flow.nodes.map((node) => {
      if (UNCONNECTABLE_KINDS.includes(kindOf(node))) {
        const position: [number, number] = [Math.round(parkedX), parkedY];
        parkedY += NODE_HEIGHT + 32;
        return { ...node, position };
      }
      const laid = graph.node(node.id);
      if (!laid) return node;
      return { ...node, position: [Math.round(laid.x - NODE_WIDTH / 2), Math.round(laid.y - NODE_HEIGHT / 2)] };
    }),
  };
}
