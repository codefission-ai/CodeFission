import { useCallback, useMemo, useRef, useState } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  BackgroundVariant,
  useReactFlow,
  type Node,
  type Edge,
  type NodeChange,
  type NodeDimensionChange,
  PanOnScrollMode,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import TreeNode from "./TreeNode";
import { useStore, actions, type CNode } from "../store";
import { layoutTree } from "../layout";

const nodeTypes = { tree: TreeNode };

function getAncestorPath(nodes: Record<string, CNode>, selectedId: string | null): Set<string> {
  const edgeIds = new Set<string>();
  if (!selectedId) return edgeIds;
  let cur = nodes[selectedId];
  while (cur?.parent_id) {
    edgeIds.add(`${cur.parent_id}-${cur.id}`);
    cur = nodes[cur.parent_id];
  }
  return edgeIds;
}

function getDescendantCount(nodes: Record<string, CNode>, id: string): number {
  let count = 0;
  const stack = [...(nodes[id]?.children_ids || [])];
  while (stack.length) {
    const cid = stack.pop()!;
    const child = nodes[cid];
    if (!child) continue;
    count++;
    stack.push(...child.children_ids);
  }
  return count;
}

function getHiddenIds(nodes: Record<string, CNode>, collapsedSubtrees: Record<string, boolean>): Set<string> {
  const hidden = new Set<string>();
  for (const id of Object.keys(collapsedSubtrees)) {
    if (!collapsedSubtrees[id] || !nodes[id]) continue;
    const stack = [...(nodes[id].children_ids || [])];
    while (stack.length) {
      const cid = stack.pop()!;
      if (hidden.has(cid)) continue;
      const child = nodes[cid];
      if (!child) continue;
      hidden.add(cid);
      stack.push(...child.children_ids);
    }
  }
  return hidden;
}

function buildFlow(
  nodes: Record<string, CNode>,
  expandedNodes: Record<string, boolean>,
  collapsedSubtrees: Record<string, boolean>,
  measured: Record<string, { width: number; height: number }>,
  ready: boolean,
  selectedNodeId: string | null,
) {
  const list = Object.values(nodes);
  const root = list.find((n) => !n.parent_id);
  if (!root) return { flowNodes: [] as Node[], flowEdges: [] as Edge[] };

  const hiddenIds = getHiddenIds(nodes, collapsedSubtrees);

  const hasMeasured = Object.keys(measured).length > 0;
  const { positions } = layoutTree(
    nodes,
    expandedNodes,
    hasMeasured ? measured : undefined,
    collapsedSubtrees,
  );

  const visibleList = list.filter((n) => !hiddenIds.has(n.id));

  const flowNodes: Node[] = visibleList.map((n) => ({
    id: n.id,
    type: "tree",
    position: positions[n.id] || { x: 0, y: 0 },
    data: {
      node: n,
      descendantCount: collapsedSubtrees[n.id] ? getDescendantCount(nodes, n.id) : 0,
    },
    style: { opacity: ready ? 1 : 0 },
  }));

  const pathEdges = getAncestorPath(nodes, selectedNodeId);

  const flowEdges: Edge[] = visibleList
    .filter((n) => n.parent_id && !hiddenIds.has(n.parent_id!))
    .map((n) => {
      const edgeId = `${n.parent_id}-${n.id}`;
      const onPath = pathEdges.has(edgeId);
      return {
        id: edgeId,
        source: n.parent_id!,
        target: n.id,
        style: {
          stroke: onPath ? "rgba(0,0,0,0.45)" : "rgba(0,0,0,0.10)",
          strokeWidth: onPath ? 2 : 1,
        },
      };
    });

  return { flowNodes, flowEdges };
}

export default function Canvas() {
  const currentTreeId = useStore((s) => s.currentTreeId);
  return (
    <ReactFlowProvider key={currentTreeId}>
      <CanvasInner />
    </ReactFlowProvider>
  );
}

function CanvasInner() {
  const nodes = useStore((s) => s.nodes);
  const expandedNodes = useStore((s) => s.expandedNodes);
  const collapsedSubtrees = useStore((s) => s.collapsedSubtrees);
  const selectedNodeId = useStore((s) => s.selectedNodeId);

  const measuredRef = useRef<Record<string, { width: number; height: number }>>({});
  const [layoutVersion, setLayoutVersion] = useState(0);
  const [ready, setReady] = useState(false);
  const prevPositionsRef = useRef<Record<string, { x: number; y: number }>>({});

  const heightTimerRef = useRef<ReturnType<typeof setTimeout>>(0 as never);

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    let needsLayout = false;
    let heightChanged = false;
    for (const change of changes) {
      if (change.type === "dimensions" && (change as NodeDimensionChange).dimensions) {
        const dim = (change as NodeDimensionChange).dimensions!;
        const prev = measuredRef.current[change.id];
        if (!prev || prev.width !== dim.width) needsLayout = true;
        if (prev && prev.width === dim.width && prev.height !== dim.height) heightChanged = true;
        if (!prev || prev.width !== dim.width || prev.height !== dim.height) {
          measuredRef.current[change.id] = { width: dim.width, height: dim.height };
        }
      }
    }
    if (needsLayout) {
      clearTimeout(heightTimerRef.current);
      setLayoutVersion((v) => v + 1);
      setReady(true);
    } else if (heightChanged) {
      clearTimeout(heightTimerRef.current);
      heightTimerRef.current = setTimeout(() => {
        setLayoutVersion((v) => v + 1);
      }, 200);
    }
  }, []);

  const { flowNodes, flowEdges } = useMemo(
    () => buildFlow(nodes, expandedNodes, collapsedSubtrees, measuredRef.current, ready, selectedNodeId),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [nodes, expandedNodes, collapsedSubtrees, layoutVersion, ready, selectedNodeId],
  );

  // Compensate viewport when layout shifts the selected node (e.g. sibling added)
  const reactFlowInstance = useReactFlow();
  const newPositions: Record<string, { x: number; y: number }> = {};
  for (const fn of flowNodes) newPositions[fn.id] = fn.position;

  if (selectedNodeId && ready) {
    const prev = prevPositionsRef.current[selectedNodeId];
    const curr = newPositions[selectedNodeId];
    if (prev && curr && (prev.x !== curr.x || prev.y !== curr.y)) {
      const dx = curr.x - prev.x;
      const dy = curr.y - prev.y;
      if (Math.abs(dx) > 1 || Math.abs(dy) > 1) {
        const vp = reactFlowInstance.getViewport();
        reactFlowInstance.setViewport(
          { x: vp.x - dx * vp.zoom, y: vp.y - dy * vp.zoom, zoom: vp.zoom },
          { duration: 0 },
        );
      }
    }
  }
  prevPositionsRef.current = newPositions;

  if (flowNodes.length === 0) {
    return <div className="canvas-empty">Create a tree to get started</div>;
  }

  return (
    <ReactFlow
      nodes={flowNodes}
      edges={flowEdges}
      nodeTypes={nodeTypes}
      onNodesChange={onNodesChange}
      onPaneClick={() => actions.selectNode(null)}
      fitView
      minZoom={0.3}
      maxZoom={2}
      zoomOnScroll={true}
      zoomOnPinch={true}
      panOnScroll={true}
      panOnScrollMode={PanOnScrollMode.Free}
      nodesDraggable={false}
      nodesConnectable={false}
      proOptions={{ hideAttribution: true }}
    >
      <Background variant={BackgroundVariant.Dots} color="#d0d0d6" gap={20} />
      <ZoomControls />
    </ReactFlow>
  );
}

const isMac = /Mac|iPhone|iPad|iPod/.test(navigator.platform);
const modKey = isMac ? "⌘" : "Ctrl";

function ZoomControls() {
  const { zoomIn, zoomOut, fitView } = useReactFlow();
  return (
    <div className="zoom-controls">
      <button className="has-tooltip" onClick={() => zoomIn({ duration: 150 })}>
        +<span className="tooltip">Zoom in <kbd>{modKey}+Scroll</kbd></span>
      </button>
      <button className="has-tooltip" onClick={() => zoomOut({ duration: 150 })}>
        −<span className="tooltip">Zoom out <kbd>{modKey}+Scroll</kbd></span>
      </button>
      <button className="has-tooltip" onClick={() => fitView({ duration: 150 })}>
        ⊡<span className="tooltip">Fit view</span>
      </button>
    </div>
  );
}
