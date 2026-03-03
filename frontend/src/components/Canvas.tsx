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

const nodeTypes = { tree: TreeNode };

function layoutTree(nodes: Record<string, CNode>) {
  const list = Object.values(nodes);
  const root = list.find((n) => !n.parent_id);
  if (!root) return { flowNodes: [] as Node[], flowEdges: [] as Edge[] };

  const children: Record<string, string[]> = {};
  for (const n of list) {
    if (n.parent_id) {
      (children[n.parent_id] ??= []).push(n.id);
    }
  }

  const width = (id: string): number => {
    const c = children[id];
    return c ? c.reduce((s, cid) => s + width(cid), 0) : 1;
  };

  const pos: Record<string, { x: number; y: number }> = {};
  const X = 180, Y = 90;

  const place = (id: string, x: number, y: number) => {
    pos[id] = { x, y };
    const c = children[id] || [];
    const total = c.reduce((s, cid) => s + width(cid), 0);
    let cx = x - ((total - 1) * X) / 2;
    for (const cid of c) {
      const w = width(cid);
      place(cid, cx + ((w - 1) * X) / 2, y + Y);
      cx += w * X;
    }
  };
  place(root.id, 400, 40);

  const flowNodes: Node[] = list.map((n) => ({
    id: n.id,
    type: "tree",
    position: pos[n.id] || { x: 0, y: 0 },
    data: { node: n },
  }));

  const flowEdges: Edge[] = list
    .filter((n) => n.parent_id)
    .map((n) => ({
      id: `${n.parent_id}-${n.id}`,
      source: n.parent_id!,
      target: n.id,
      style: { stroke: "#ccc" },
    }));

  return { flowNodes, flowEdges };
}

export default function Canvas() {
  const nodes = useStore((s) => s.nodes);
  const { flowNodes, flowEdges } = useMemo(() => layoutTree(nodes), [nodes]);

  if (flowNodes.length === 0) {
    return <div className="canvas-empty">Create a tree to get started</div>;
  }

  return (
    <ReactFlow
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
      <Background variant={BackgroundVariant.Dots} color="#ddd" gap={20} />
    </ReactFlow>
  );
}
