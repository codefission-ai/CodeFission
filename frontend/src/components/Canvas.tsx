import { useMemo } from "react";
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  type Node,
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import TreeNode from "./TreeNode";
import { useStore, type CNode } from "../store";
import { layoutTree } from "../layout";

const nodeTypes = { tree: TreeNode };

function buildFlow(nodes: Record<string, CNode>, expandedNodes: Record<string, boolean>) {
  const list = Object.values(nodes);
  const root = list.find((n) => !n.parent_id);
  if (!root) return { flowNodes: [] as Node[], flowEdges: [] as Edge[] };

  const { positions } = layoutTree(nodes, expandedNodes);

  const flowNodes: Node[] = list.map((n) => ({
    id: n.id,
    type: "tree",
    position: positions[n.id] || { x: 0, y: 0 },
    data: { node: n },
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
  const { flowNodes, flowEdges } = useMemo(
    () => buildFlow(nodes, expandedNodes),
    [nodes, expandedNodes]
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
