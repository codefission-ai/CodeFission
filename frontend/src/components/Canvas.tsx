import { useCallback, useMemo, useRef, useState } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  BackgroundVariant,
  useReactFlow,
  useInternalNode,
  BaseEdge,
  MarkerType,
  applyNodeChanges,
  type Node,
  type Edge,
  type EdgeProps,
  type NodeChange,
  type NodeDimensionChange,
  PanOnScrollMode,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import TreeNode from "./TreeNode";
import { useStore, actions, type CNode } from "../store";
import { layoutTree } from "../layout";

function QuoteEdge({ source, target, markerEnd }: EdgeProps) {
  const sourceNode = useInternalNode(source);
  const targetNode = useInternalNode(target);
  if (!sourceNode || !targetNode) return null;

  const sw = sourceNode.measured?.width ?? 0;
  const sh = sourceNode.measured?.height ?? 0;
  const tw = targetNode.measured?.width ?? 0;
  const th = targetNode.measured?.height ?? 0;

  // Node centers
  const sx = (sourceNode.internals?.positionAbsolute?.x ?? 0) + sw / 2;
  const sy = (sourceNode.internals?.positionAbsolute?.y ?? 0) + sh / 2;
  const tx = (targetNode.internals?.positionAbsolute?.x ?? 0) + tw / 2;
  const ty = (targetNode.internals?.positionAbsolute?.y ?? 0) + th / 2;

  const dx = tx - sx;
  const dy = ty - sy;
  if (dx === 0 && dy === 0) return null;

  // Intersection with source rectangle edge
  const sT = Math.min(
    dx !== 0 ? (sw / 2) / Math.abs(dx) : Infinity,
    dy !== 0 ? (sh / 2) / Math.abs(dy) : Infinity,
  );
  // Intersection with target rectangle edge
  const tT = Math.min(
    dx !== 0 ? (tw / 2) / Math.abs(dx) : Infinity,
    dy !== 0 ? (th / 2) / Math.abs(dy) : Infinity,
  );

  const path = `M ${sx + dx * sT} ${sy + dy * sT} L ${tx - dx * tT} ${ty - dy * tT}`;
  return <BaseEdge path={path} markerEnd={markerEnd} />;
}

function NoteNode({ id, data }: { id: string; data: { text?: string; onTextChange?: (id: string, text: string) => void } }) {
  const [text, setText] = useState(data.text ?? "");
  const taRef = useRef<HTMLTextAreaElement>(null);

  return (
    <div className="sticky-note">
      <div className="sticky-note-drag-handle" />
      <textarea
        ref={taRef}
        className="sticky-note-input nopan nodrag"
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          data.onTextChange?.(id, e.target.value);
        }}
        placeholder="Write a note..."
      />
    </div>
  );
}

const nodeTypes = { tree: TreeNode, note: NoteNode };
const edgeTypes = { quote: QuoteEdge };

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

  // Quote edges: dashed arrows from quoted nodes → quoting node
  for (const n of visibleList) {
    if (!n.quoted_node_ids || n.quoted_node_ids.length === 0) continue;
    for (const qid of n.quoted_node_ids) {
      if (hiddenIds.has(qid) || !nodes[qid]) continue;
      flowEdges.push({
        id: `quote-${qid}-${n.id}`,
        source: qid,
        target: n.id,
        type: "quote",
        className: "quote-edge",
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: "#3b82f6",
          width: 16,
          height: 16,
        },
      });
    }
  }

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

  const layoutTimerRef = useRef<ReturnType<typeof setTimeout>>(0 as never);

  // Track note nodes separately so ReactFlow owns their position during drag
  const [noteNodes, setNoteNodes] = useState<Node[]>([]);

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    let needsLayout = false;
    let heightChanged = false;
    const noteChanges: NodeChange[] = [];
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
      // Let ReactFlow handle note node position/dimension changes natively
      if ("id" in change && (change as any).id?.startsWith?.("note-")) {
        noteChanges.push(change);
      }
    }
    if (noteChanges.length > 0) {
      setNoteNodes((nds) => applyNodeChanges(noteChanges, nds));
    }
    if (needsLayout) {
      clearTimeout(heightTimerRef.current);
      clearTimeout(layoutTimerRef.current);
      if (!ready) {
        setLayoutVersion((v) => v + 1);
        setReady(true);
      } else {
        layoutTimerRef.current = setTimeout(() => {
          setLayoutVersion((v) => v + 1);
        }, 50);
      }
    } else if (heightChanged) {
      clearTimeout(heightTimerRef.current);
      heightTimerRef.current = setTimeout(() => {
        setLayoutVersion((v) => v + 1);
      }, 400);
    }
  }, [ready]);

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
          { duration: 350 },
        );
      }
    }
  }
  prevPositionsRef.current = newPositions;

  // Stable ref callback so note data objects don't change on re-render
  const noteTextRef = useRef<Record<string, string>>({});
  const onNoteTextChange = useCallback((id: string, text: string) => {
    noteTextRef.current[id] = text;
  }, []);

  const addNote = useCallback(() => {
    const vp = reactFlowInstance.getViewport();
    const x = (-vp.x + window.innerWidth / 2) / vp.zoom - 75;
    const y = (-vp.y + window.innerHeight / 2) / vp.zoom - 50;
    setNoteNodes((prev) => [...prev, {
      id: `note-${Date.now()}`,
      type: "note",
      position: { x, y },
      data: { text: "", onTextChange: onNoteTextChange },
      draggable: true,
    }]);
  }, [reactFlowInstance, onNoteTextChange]);

  // Merge tree nodes + note nodes
  const allNodes = useMemo(() => [...flowNodes, ...noteNodes], [flowNodes, noteNodes]);

  if (flowNodes.length === 0) {
    return <div className="canvas-empty">Create a tree to get started</div>;
  }

  return (
    <ReactFlow
      nodes={allNodes}
      edges={flowEdges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      onNodesChange={onNodesChange}
      onPaneClick={() => actions.selectNode(null)}
      fitView={!ready}
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
      <ZoomControls onAddNote={addNote} />
    </ReactFlow>
  );
}

const isMac = /Mac|iPhone|iPad|iPod/.test(navigator.platform);
const modKey = isMac ? "⌘" : "Ctrl";

function ZoomControls({ onAddNote }: { onAddNote: () => void }) {
  const { zoomIn, zoomOut, fitView } = useReactFlow();
  return (
    <div className="zoom-controls">
      <button className="has-tooltip" onClick={onAddNote}>
        ▪<span className="tooltip">Add note</span>
      </button>
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
