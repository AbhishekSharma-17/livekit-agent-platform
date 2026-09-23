"use client";

import "@xyflow/react/dist/style.css";

import * as React from "react";
import {
  applyNodeChanges,
  Background,
  Controls,
  Handle,
  MarkerType,
  Panel,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Connection,
  type Edge,
  type Node,
  type NodeChange,
  type NodeProps,
} from "@xyflow/react";
import {
  AlertCircleIcon,
  BracesIcon,
  FlagIcon,
  GlobeIcon,
  LayoutGridIcon,
  MessageSquareIcon,
  PhoneForwardedIcon,
  PlayIcon,
  PlusIcon,
  StarIcon,
  Trash2Icon,
  type LucideIcon,
} from "lucide-react";

import { useSectionIssues } from "@/components/console/agents/editor/editor-context";
import { Icon } from "@/components/shared/icon";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import type { AgentOut, FlowEdge } from "@/contracts/lkap-contracts";
import type { JsonSchema } from "@/lib/schema-form";
import { useThemePreference } from "@/lib/theme";
import { cn } from "@/lib/utils";

import { useFlowValidation, useNodeSpecs } from "./api";
import { EdgeForm } from "./edge-form";
import {
  canHaveIncoming,
  canHaveOutgoing,
  fieldMessages,
  flowDraftIssues,
  groupIssues,
  hasErrors,
  issueTarget,
  kindOf,
  NODE_KIND_LABEL,
  newNode,
  removeNode,
  renameVariable,
  uniqueEdgeId,
  updateEdge,
  updateNode,
  type AnyFlowNode,
  type FlowDraft,
  type FlowIssue,
  type FlowNodeKind,
  type TargetIssues,
} from "./flow-model";
import { autoLayout, NODE_HEIGHT, NODE_WIDTH } from "./layout";
import { NodeForm } from "./node-form";
import { useFlowDraft } from "./use-flow-draft";
import { useNodeOptions } from "./use-node-options";
import { VariablesDialog } from "./variables-dialog";
import { VersionHistory } from "./version-history";
import { SkeletonRows } from "@/components/shared/loading-state";

/**
 * The flow canvas (V2-16): `@xyflow/react` v12 + dagre. This module — and
 * with it `@xyflow/react`, its CSS and dagre — is loaded only through
 * `next/dynamic` from `flow-section.tsx`, so it is never in the editor's main
 * chunk nor anywhere near the `/s/[slug]` session bundle.
 */

const KIND_ICON: Record<FlowNodeKind, LucideIcon> = {
  start: PlayIcon,
  agent: MessageSquareIcon,
  end: FlagIcon,
  transfer: PhoneForwardedIcon,
  global: GlobeIcon,
  qa: StarIcon,
};

const ADDABLE: FlowNodeKind[] = ["agent", "end", "transfer", "global", "qa"];

const DEBOUNCE_MS = 600;

type Selection = { kind: "node" | "edge"; id: string } | null;

interface FlowNodeData extends Record<string, unknown> {
  node: AnyFlowNode;
  issues: TargetIssues | undefined;
}

type FlowRfNode = Node<FlowNodeData, "flowNode">;

function subtitle(node: AnyFlowNode): string {
  switch (kindOf(node)) {
    case "start":
      return "greeting" in node && node.greeting ? `“${node.greeting}”` : "Uses the agent's greeting";
    case "agent":
      return ("instructions" in node && node.instructions) || "No instructions yet";
    case "end":
      return "disposition" in node && node.disposition ? `Disposition: ${node.disposition}` : "Ends the call";
    case "transfer":
      return "to" in node && node.to ? `To ${node.to}` : "No destination yet";
    case "global":
      return "Applies to every step";
    case "qa":
      return "Scores the call afterwards";
  }
}

function FlowNodeCard({ data, selected }: NodeProps<FlowRfNode>) {
  const { node, issues } = data;
  const kind = kindOf(node);
  const errors = issues?.errors.length ?? 0;
  const warnings = issues?.warnings.length ?? 0;
  const dotLabel = errors
    ? `${errors} ${errors === 1 ? "error" : "errors"}`
    : warnings
      ? `${warnings} ${warnings === 1 ? "warning" : "warnings"}`
      : null;
  return (
    <div
      data-flow-node={node.id}
      data-issue={errors ? "error" : warnings ? "warning" : undefined}
      style={{ width: NODE_WIDTH, minHeight: NODE_HEIGHT }}
      className={cn(
        "relative flex flex-col gap-1 rounded-md border bg-card px-3 py-2 text-card-foreground shadow-sm",
        kind === "global" || kind === "qa" ? "border-dashed border-border" : "border-border",
        selected && "ring-2 ring-ring ring-offset-1 ring-offset-background",
      )}
    >
      {canHaveIncoming(kind) ? <Handle type="target" position={Position.Top} className="!size-2.5" /> : null}
      <div className="flex items-center gap-1.5 text-[0.6875rem] font-medium tracking-wide text-muted-foreground uppercase">
        <Icon as={KIND_ICON[kind]} size="sm" />
        {NODE_KIND_LABEL[kind]}
        {dotLabel ? (
          <span
            role="img"
            aria-label={dotLabel}
            title={[...(issues?.errors ?? []), ...(issues?.warnings ?? [])].map((issue) => issue.message).join("\n")}
            className={cn("ml-auto size-2 rounded-full", errors ? "bg-danger" : "bg-warning")}
          />
        ) : null}
      </div>
      <div className="truncate text-sm font-medium">{node.label || node.id}</div>
      <div className="line-clamp-2 text-xs text-muted-foreground">{subtitle(node)}</div>
      {canHaveOutgoing(kind) ? <Handle type="source" position={Position.Bottom} className="!size-2.5" /> : null}
    </div>
  );
}

const NODE_TYPES = { flowNode: FlowNodeCard };

function toRfNodes(
  draft: FlowDraft,
  issues: Map<string, TargetIssues>,
  selection: Selection,
  previous: readonly FlowRfNode[] = [],
): FlowRfNode[] {
  const prior = new Map(previous.map((node) => [node.id, node]));
  return draft.nodes.map((node) => {
    const before = prior.get(node.id);
    const position = before?.dragging
      ? before.position
      : { x: Number(node.position?.[0] ?? 0), y: Number(node.position?.[1] ?? 0) };
    return {
      ...(before ?? {}),
      id: node.id,
      type: "flowNode" as const,
      position,
      data: { node, issues: issues.get(node.id) },
      selected: selection?.kind === "node" && selection.id === node.id,
      deletable: kindOf(node) !== "start",
    };
  });
}

function mergeIssues(...lists: readonly (readonly FlowIssue[])[]): FlowIssue[] {
  const seen = new Set<string>();
  const out: FlowIssue[] = [];
  for (const list of lists) {
    for (const issue of list) {
      const key = `${issue.path}|${issue.message}`;
      if (!seen.has(key)) {
        seen.add(key);
        out.push(issue);
      }
    }
  }
  return out;
}

export interface FlowCanvasProps {
  agent: AgentOut;
}

export function FlowCanvas(props: FlowCanvasProps) {
  return (
    <ReactFlowProvider>
      <FlowCanvasInner {...props} />
    </ReactFlowProvider>
  );
}

function FlowCanvasInner({ agent }: FlowCanvasProps) {
  const { draft, update } = useFlowDraft();
  const { resolvedTheme } = useThemePreference();
  const reactFlow = useReactFlow();
  const [selection, setSelection] = React.useState<Selection>(null);
  const [inspectorOpen, setInspectorOpen] = React.useState(false);
  const [variablesOpen, setVariablesOpen] = React.useState(false);

  // ---- issues: instant client checks, then the api's reference checks on the draft ----
  const clientIssues = React.useMemo(() => flowDraftIssues(draft), [draft]);
  const draftJson = React.useMemo(() => JSON.stringify(draft), [draft]);
  const [debounced, setDebounced] = React.useState<string | null>(null);
  React.useEffect(() => {
    if (hasErrors(clientIssues) || draft.nodes.length === 0) {
      setDebounced(null);
      return;
    }
    const timer = window.setTimeout(() => setDebounced(draftJson), DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [clientIssues, draft.nodes.length, draftJson]);
  const live = useFlowValidation(agent.id, debounced);
  const saved = useSectionIssues("flow");
  const serverIssues: FlowIssue[] = React.useMemo(() => {
    if (debounced !== null && live.data && !live.isPlaceholderData) {
      return (live.data.issues ?? []).map((issue) => ({ ...issue, severity: issue.severity ?? "error" }));
    }
    return saved.issues
      .filter((issue) => issue.path)
      .map((issue) => ({ path: issue.path as string, message: issue.message, severity: issue.severity }));
  }, [debounced, live.data, live.isPlaceholderData, saved.issues]);
  const issues = React.useMemo(() => mergeIssues(clientIssues, serverIssues), [clientIssues, serverIssues]);
  const grouped = React.useMemo(() => groupIssues(issues, draft), [issues, draft]);
  const errorCount = issues.filter((issue) => issue.severity === "error").length;
  const warningCount = issues.length - errorCount;

  // ---- nodes (kept in state so React Flow can store measurements) ----
  const [nodes, setNodes] = React.useState<FlowRfNode[]>(() => toRfNodes(draft, grouped.nodes, selection));
  React.useEffect(() => {
    setNodes((previous) => toRfNodes(draft, grouped.nodes, selection, previous));
  }, [draft, grouped.nodes, selection]);
  const edges: Edge[] = React.useMemo(
    () =>
      draft.edges.map((edge) => {
        const edgeIssues = grouped.edges.get(edge.id);
        const tone = edgeIssues?.errors.length ? "var(--danger)" : edgeIssues?.warnings.length ? "var(--warning)" : undefined;
        const text = edge.label || edge.condition || "";
        return {
          id: edge.id,
          source: edge.source,
          target: edge.target,
          label: text.length > 42 ? `${text.slice(0, 41)}…` : text,
          selected: selection?.kind === "edge" && selection.id === edge.id,
          markerEnd: { type: MarkerType.ArrowClosed },
          style: tone ? { stroke: tone } : undefined,
          labelBgPadding: [6, 3] as [number, number],
          labelBgBorderRadius: 4,
          ariaLabel: `Path from ${edge.source} to ${edge.target}${edgeIssues?.errors.length ? " (has errors)" : ""}`,
        };
      }),
    [draft.edges, grouped.edges, selection],
  );

  const onNodesChange = React.useCallback((changes: NodeChange<FlowRfNode>[]) => {
    setNodes((current) => applyNodeChanges(changes, current));
  }, []);

  const select = React.useCallback((next: Selection) => {
    setSelection(next);
    setInspectorOpen(next !== null);
  }, []);

  const connectable = React.useCallback(
    (connection: Connection | Edge) => {
      const source = draft.nodes.find((node) => node.id === connection.source);
      const target = draft.nodes.find((node) => node.id === connection.target);
      if (!source || !target || source.id === target.id) return false;
      return canHaveOutgoing(kindOf(source)) && canHaveIncoming(kindOf(target));
    },
    [draft.nodes],
  );

  const onConnect = React.useCallback(
    (connection: Connection) => {
      if (!connectable(connection)) return;
      const id = uniqueEdgeId(connection.source, connection.target, draft.edges.map((edge) => edge.id));
      update((current) => ({
        ...current,
        edges: [...current.edges, { id, source: connection.source, target: connection.target, condition: "", priority: 0 }],
      }));
      select({ kind: "edge", id });
    },
    [connectable, draft.edges, select, update],
  );

  function addNode(kind: FlowNodeKind) {
    const lowest = draft.nodes.reduce((max, node) => Math.max(max, Number(node.position?.[1] ?? 0)), 0);
    const node = newNode(
      kind,
      draft.nodes.map((existing) => existing.id),
      [0, draft.nodes.length ? lowest + NODE_HEIGHT + 96 : 0],
    );
    update((current) => ({ ...current, nodes: [...current.nodes, node] }));
    select({ kind: "node", id: node.id });
    window.setTimeout(() => void reactFlow.fitView({ padding: 0.2, maxZoom: 1, duration: 200 }), 50);
  }

  function tidy() {
    update((current) => autoLayout(current));
    window.setTimeout(() => void reactFlow.fitView({ padding: 0.2, maxZoom: 1, duration: 200 }), 50);
  }

  const selectedNode = selection?.kind === "node" ? draft.nodes.find((node) => node.id === selection.id) : undefined;
  const selectedEdge = selection?.kind === "edge" ? draft.edges.find((edge) => edge.id === selection.id) : undefined;
  const hasStart = draft.nodes.some((node) => kindOf(node) === "start");

  return (
    <div
      data-slot="flow-canvas"
      className="relative h-[calc(100dvh-var(--console-topbar-height,3rem)-var(--editor-header-height,0px)-7rem)] min-h-[520px] overflow-hidden rounded-lg border border-border bg-background"
    >
      <ReactFlow<FlowRfNode, Edge>
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        onNodesChange={onNodesChange}
        onNodeDragStop={(_event, _node, moved) => {
          const positions = new Map(moved.map((node) => [node.id, node.position]));
          update((current) => ({
            ...current,
            nodes: current.nodes.map((node) => {
              const position = positions.get(node.id);
              return position ? { ...node, position: [Math.round(position.x), Math.round(position.y)] } : node;
            }),
          }));
        }}
        onNodesDelete={(deleted) => {
          update((current) => deleted.reduce((acc, node) => removeNode(acc, node.id), current));
          select(null);
        }}
        onEdgesDelete={(deleted) => {
          const ids = new Set(deleted.map((edge) => edge.id));
          update((current) => ({ ...current, edges: current.edges.filter((edge) => !ids.has(edge.id)) }));
          select(null);
        }}
        onConnect={onConnect}
        isValidConnection={connectable}
        onNodeClick={(_event, node) => select({ kind: "node", id: node.id })}
        onEdgeClick={(_event, edge) => select({ kind: "edge", id: edge.id })}
        onPaneClick={() => select(null)}
        colorMode={resolvedTheme ?? "light"}
        fitView
        fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
        minZoom={0.2}
        proOptions={{ hideAttribution: true }}
        aria-label="Flow canvas"
      >
        <Background gap={20} size={1} />
        <Controls showInteractive={false} position="bottom-right" />
        <Panel position="top-left" className="flex flex-wrap items-center gap-2">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button type="button" size="sm" variant="outline">
                <Icon as={PlusIcon} />
                Add node
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start">
              {!hasStart ? (
                <DropdownMenuItem onSelect={() => addNode("start")}>
                  <Icon as={KIND_ICON.start} />
                  {NODE_KIND_LABEL.start}
                </DropdownMenuItem>
              ) : null}
              {ADDABLE.map((kind) => (
                <DropdownMenuItem key={kind} onSelect={() => addNode(kind)}>
                  <Icon as={KIND_ICON[kind]} />
                  {NODE_KIND_LABEL[kind]}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
          <Button type="button" size="sm" variant="outline" onClick={tidy}>
            <Icon as={LayoutGridIcon} />
            Tidy layout
          </Button>
          <Button type="button" size="sm" variant="outline" onClick={() => setVariablesOpen(true)}>
            <Icon as={BracesIcon} />
            Variables ({draft.variables.length})
          </Button>
          <VersionHistory agent={agent} variant="button" />
          <IssuesButton
            issues={issues}
            errorCount={errorCount}
            warningCount={warningCount}
            checking={live.isFetching}
            draft={draft}
            onPick={(issue) => {
              const target = issueTarget(issue.path, draft);
              if (target?.id && (target.kind === "node" || target.kind === "edge")) {
                select({ kind: target.kind, id: target.id });
              } else if (target?.kind === "variable") {
                setVariablesOpen(true);
              }
            }}
          />
        </Panel>
      </ReactFlow>

      <Sheet open={inspectorOpen && Boolean(selectedNode || selectedEdge)} onOpenChange={setInspectorOpen} modal={false}>
        <SheetContent
          side="right"
          className="w-full gap-0 overflow-y-auto p-0 sm:max-w-[400px]"
          onInteractOutside={(event) => event.preventDefault()}
        >
          {selectedNode ? (
            <NodeInspector
              key={selectedNode.id}
              agent={agent}
              node={selectedNode}
              draft={draft}
              issues={grouped.nodes.get(selectedNode.id)}
              onChange={(next) => update((current) => updateNode(current, selectedNode.id, next))}
              onDelete={() => {
                update((current) => removeNode(current, selectedNode.id));
                select(null);
              }}
              onManageVariables={() => setVariablesOpen(true)}
            />
          ) : selectedEdge ? (
            <EdgeInspector
              key={selectedEdge.id}
              edge={selectedEdge}
              draft={draft}
              issues={grouped.edges.get(selectedEdge.id)}
              onChange={(next) => update((current) => updateEdge(current, selectedEdge.id, next))}
              onDelete={() => {
                update((current) => ({ ...current, edges: current.edges.filter((edge) => edge.id !== selectedEdge.id) }));
                select(null);
              }}
            />
          ) : null}
        </SheetContent>
      </Sheet>

      <VariablesDialog
        open={variablesOpen}
        onOpenChange={setVariablesOpen}
        variables={draft.variables}
        onApply={(variables, renames) =>
          update((current) => {
            let next: FlowDraft = current;
            for (const [from, to] of Object.entries(renames)) next = renameVariable(next, from, to);
            return { ...next, variables };
          })
        }
      />
    </div>
  );
}

function IssuesButton({
  issues,
  errorCount,
  warningCount,
  checking,
  draft,
  onPick,
}: {
  issues: readonly FlowIssue[];
  errorCount: number;
  warningCount: number;
  checking: boolean;
  draft: FlowDraft;
  onPick: (issue: FlowIssue) => void;
}) {
  if (issues.length === 0) {
    return (
      <span className="text-xs text-muted-foreground" aria-live="polite">
        {checking ? "Checking…" : "No issues"}
      </span>
    );
  }
  const label = [
    errorCount ? `${errorCount} ${errorCount === 1 ? "error" : "errors"}` : null,
    warningCount ? `${warningCount} ${warningCount === 1 ? "warning" : "warnings"}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button type="button" size="sm" variant="outline" aria-label={`Flow issues: ${label}`}>
          <Icon as={AlertCircleIcon} className={errorCount ? "text-danger-text" : "text-warning-text"} />
          {label}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 p-1">
        <ul className="flex max-h-72 flex-col overflow-y-auto">
          {issues.map((issue) => {
            const target = issueTarget(issue.path, draft);
            const where =
              target?.kind === "node"
                ? (draft.nodes.find((node) => node.id === target.id)?.label ?? target.id)
                : target?.kind === "edge"
                  ? `Path ${target.id}`
                  : target?.kind === "variable"
                    ? `Variable ${target.id}`
                    : "Flow";
            return (
              <li key={`${issue.path}|${issue.message}`}>
                <button
                  type="button"
                  className="flex w-full flex-col items-start gap-0.5 rounded-sm px-2 py-1.5 text-left hover:bg-muted"
                  onClick={() => onPick(issue)}
                >
                  <span className="flex items-center gap-1.5 text-xs font-medium">
                    <span
                      aria-hidden="true"
                      className={cn("size-1.5 rounded-full", issue.severity === "error" ? "bg-danger" : "bg-warning")}
                    />
                    {where}
                  </span>
                  <span className="text-xs text-muted-foreground">{issue.message}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </PopoverContent>
    </Popover>
  );
}

function NodeInspector({
  agent,
  node,
  draft,
  issues,
  onChange,
  onDelete,
  onManageVariables,
}: {
  agent: AgentOut;
  node: AnyFlowNode;
  draft: FlowDraft;
  issues: TargetIssues | undefined;
  onChange: (next: AnyFlowNode) => void;
  onDelete: () => void;
  onManageVariables: () => void;
}) {
  const specs = useNodeSpecs();
  const options = useNodeOptions(agent);
  const kind = kindOf(node);
  const spec = specs.data?.nodes?.find((item) => item.kind === kind);
  return (
    <>
      <SheetHeader className="border-b border-border">
        <SheetTitle>{node.label || node.id}</SheetTitle>
        <SheetDescription>{NODE_KIND_LABEL[kind]}</SheetDescription>
      </SheetHeader>
      <div className="flex-1 p-4">
        {spec?.json_schema ? (
          <NodeForm
            node={node}
            schema={spec.json_schema as JsonSchema}
            onChange={onChange}
            variables={draft.variables}
            toolOptions={options.toolOptions}
            kbOptions={options.kbOptions}
            providerOptions={options.providerOptions}
            {...fieldMessages(issues, draft)}
            onManageVariables={onManageVariables}
          />
        ) : specs.isError ? (
          <p className="text-sm text-danger-text">Couldn&apos;t load the node form. Reload to try again.</p>
        ) : (
          <SkeletonRows label="Loading the node form" rows={4} rowClassName="h-9" />
        )}
      </div>
      {kind !== "start" ? (
        <SheetFooter className="border-t border-border">
          <Button type="button" variant="destructive" onClick={onDelete}>
            <Icon as={Trash2Icon} />
            Delete node
          </Button>
        </SheetFooter>
      ) : null}
    </>
  );
}

function EdgeInspector({
  edge,
  draft,
  issues,
  onChange,
  onDelete,
}: {
  edge: FlowEdge;
  draft: FlowDraft;
  issues: TargetIssues | undefined;
  onChange: (next: FlowEdge) => void;
  onDelete: () => void;
}) {
  const label = (id: string) => {
    const node = draft.nodes.find((item) => item.id === id);
    return node?.label || id;
  };
  return (
    <>
      <SheetHeader className="border-b border-border">
        <SheetTitle>Path</SheetTitle>
        <SheetDescription>When the model moves on, and what it says.</SheetDescription>
      </SheetHeader>
      <div className="flex-1 p-4">
        <EdgeForm
          edge={edge}
          sourceLabel={label(edge.source)}
          targetLabel={label(edge.target)}
          variables={draft.variables}
          onChange={onChange}
          {...fieldMessages(issues, draft)}
        />
      </div>
      <SheetFooter className="border-t border-border">
        <Button type="button" variant="destructive" onClick={onDelete}>
          <Icon as={Trash2Icon} />
          Delete path
        </Button>
      </SheetFooter>
    </>
  );
}
