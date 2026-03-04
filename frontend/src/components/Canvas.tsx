import { useCallback, useMemo, useRef, useState } from "react";
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  type Node,
  type Edge,
  type NodeChange,
  type NodeDimensionChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import TreeNode from "./TreeNode";
import { useStore, type CNode } from "../store";
import { layoutTree } from "../layout";

const nodeTypes = { tree: TreeNode };

function buildFlow(
  nodes: Record<string, CNode>,
  expandedNodes: Record<string, boolean>,
  measured: Record<string, { width: number; height: number }>,
  ready: boolean,
) {
  const list = Object.values(nodes);
  const root = list.find((n) => !n.parent_id);
  if (!root) return { flowNodes: [] as Node[], flowEdges: [] as Edge[] };

  const hasMeasured = Object.keys(measured).length > 0;
  const { positions } = layoutTree(
    nodes,
    expandedNodes,
    hasMeasured ? measured : undefined,
  );

  const flowNodes: Node[] = list.map((n) => ({
    id: n.id,
    type: "tree",
    position: positions[n.id] || { x: 0, y: 0 },
    data: { node: n },
    style: { opacity: ready ? 1 : 0 },
  }));

  const flowEdges: Edge[] = list
    .filter((n) => n.parent_id)
    .map((n) => ({
      id: `${n.parent_id}-${n.id}`,
      source: n.parent_id!,
      target: n.id,
      style: { stroke: "rgba(255,255,255,0.12)" },
    }));

  return { flowNodes, flowEdges };
}

export default function Canvas() {
  const nodes = useStore((s) => s.nodes);
  const expandedNodes = useStore((s) => s.expandedNodes);
  const currentTreeId = useStore((s) => s.currentTreeId);

  const measuredRef = useRef<Record<string, { width: number; height: number }>>({});
  const [layoutVersion, setLayoutVersion] = useState(0);
  const [ready, setReady] = useState(false);

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    let changed = false;
    for (const change of changes) {
      if (change.type === "dimensions" && (change as NodeDimensionChange).dimensions) {
        const dim = (change as NodeDimensionChange).dimensions!;
        const prev = measuredRef.current[change.id];
        if (!prev || prev.width !== dim.width || prev.height !== dim.height) {
          measuredRef.current[change.id] = { width: dim.width, height: dim.height };
          changed = true;
        }
      }
    }
    if (changed) {
      setLayoutVersion((v) => v + 1);
      setReady(true);
    }
  }, []);

  const { flowNodes, flowEdges } = useMemo(
    () => buildFlow(nodes, expandedNodes, measuredRef.current, ready),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [nodes, expandedNodes, layoutVersion, ready],
  );

  if (flowNodes.length === 0) {
    return <div className="canvas-empty">Create a tree to get started</div>;
  }

  return (
    <ReactFlow
      key={currentTreeId}
      nodes={flowNodes}
      edges={flowEdges}
      nodeTypes={nodeTypes}
      onNodesChange={onNodesChange}
      fitView
      minZoom={0.3}
      maxZoom={2}
      nodesDraggable={false}
      nodesConnectable={false}
      proOptions={{ hideAttribution: true }}
    >
      <Background variant={BackgroundVariant.Dots} color="#2a2a30" gap={20} />
    </ReactFlow>
  );
}
