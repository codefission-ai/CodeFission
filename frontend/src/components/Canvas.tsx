import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  BackgroundVariant,
  useReactFlow,
  useStoreApi,
  MarkerType,
  applyNodeChanges,
  type Node,
  type Edge,
  type EdgeProps,
  type NodeChange,
  type NodeDimensionChange,
  PanOnScrollMode,
  NodeResizeControl,
  Handle,
  Position,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import TreeNode from "./TreeNode";
import { useStore, actions, type CNode, type FileQuote } from "../store";
import { send, WS } from "../ws";
import { layoutTree } from "../layout";

interface StickyNote {
  id: string;
  text: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

// Module-level registry so onNodeDrag can imperatively update edge SVG paths
// without going through React's render cycle at all.
const quoteEdgeRegistry = new Map<
  string,
  { el: SVGPathElement; source: string; target: string }
>();

function computeQuotePath(
  sx: number, sy: number, sw: number, sh: number,
  tx: number, ty: number,
) {
  const x1 = sx + sw / 2, y1 = sy + sh;
  const x2 = tx, y2 = ty;
  const midY = (y1 + y2) / 2;
  return `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`;
}

function QuoteEdge({ source, target, markerEnd }: EdgeProps) {
  const pathRef = useRef<SVGPathElement>(null);
  const store = useStoreApi();

  const getPath = useCallback(() => {
    const { nodeLookup } = store.getState();
    const sn = nodeLookup.get(source);
    const tn = nodeLookup.get(target);
    if (!sn || !tn) return "";
    return computeQuotePath(
      sn.internals?.positionAbsolute?.x ?? 0,
      sn.internals?.positionAbsolute?.y ?? 0,
      sn.measured?.width ?? 0,
      sn.measured?.height ?? 0,
      (tn.internals?.positionAbsolute?.x ?? 0) + (tn.measured?.width ?? 0) / 2,
      tn.internals?.positionAbsolute?.y ?? 0,
    );
  }, [source, target, store]);

  // Register SVG path element so onNodeDrag can update it imperatively
  useEffect(() => {
    const el = pathRef.current;
    if (el) quoteEdgeRegistry.set(`${source}\0${target}`, { el, source, target });
    return () => { quoteEdgeRegistry.delete(`${source}\0${target}`); };
  }, [source, target]);

  // Store subscription handles non-drag updates (layout changes, new nodes, etc.)
  useEffect(() => {
    const update = () => {
      if (pathRef.current) pathRef.current.setAttribute("d", getPath());
    };
    update();
    return store.subscribe(update);
  }, [store, getPath]);

  return (
    <path
      ref={pathRef}
      fill="none"
      stroke="#3b82f6"
      strokeWidth={1.5}
      strokeDasharray="6 3"
      markerEnd={markerEnd as string}
    />
  );
}

function NoteNode({ id, data }: { id: string; data: { text?: string; onTextChange?: (id: string, text: string) => void; onDuplicate?: (id: string) => void } }) {
  const [text, setText] = useState(data.text ?? "");
  const taRef = useRef<HTMLTextAreaElement>(null);

  const selectedHasInput = useStore((s) => {
    if (!s.selectedNodeId) return false;
    const sel = s.nodes[s.selectedNodeId];
    if (!sel) return false;
    if (!sel.parent_id) return true;
    return !!s.expandedNodes[s.selectedNodeId] && !s.streaming[s.selectedNodeId];
  });
  const pendingQuotes = useStore((s) => s.pendingQuotes);
  const quotesFromThis = pendingQuotes.filter((q) => q.nodeId === id).length;
  const isQuoted = quotesFromThis > 0;
  const canQuote = selectedHasInput;

  // Check if any tree node references this note in quoted_node_ids (sent quote)
  const isReferenced = useStore((s) => Object.values(s.nodes).some((n) => n.quoted_node_ids?.includes(id)));
  const locked = isQuoted || isReferenced;

  const onWheel = useCallback((e: React.WheelEvent) => {
    if (e.ctrlKey || e.metaKey) return;
    e.stopPropagation();
  }, []);

  return (
    <div className={`sticky-note ${locked ? "sticky-note-locked" : ""}`}>
      <NodeResizeControl minWidth={120} minHeight={80} position="bottom-right" className="sticky-note-resize-ctrl" />
      <div className="sticky-note-drag-handle">
        {locked && (
          <button
            className="note-action-btn nopan nodrag"
            title="Duplicate & edit"
            onMouseDown={(e) => { e.preventDefault(); e.stopPropagation(); }}
            onClick={(e) => { e.stopPropagation(); data.onDuplicate?.(id); }}
          >⧉</button>
        )}
        {canQuote && (
          <button
            className={`note-quote-btn nopan nodrag ${isQuoted ? "quoted" : ""}`}
            onMouseDown={(e) => { e.preventDefault(); e.stopPropagation(); }}
            onClick={(e) => {
              e.stopPropagation();
              if (isQuoted) {
                const toRemove = useStore.getState().pendingQuotes.filter((q: FileQuote) => q.nodeId === id);
                toRemove.forEach((q: FileQuote) => actions.removeFileQuote(q.id));
              } else {
                actions.addFileQuote({
                  id: `fq-${Date.now()}`,
                  nodeId: id,
                  type: "note",
                  content: text,
                  label: text.slice(0, 20) || "Note",
                });
              }
            }}
          >
            {isQuoted ? "Quoted ✓" : "Quote"}
          </button>
        )}
      </div>
      <textarea
        ref={taRef}
        className="sticky-note-input nopan nodrag"
        value={text}
        readOnly={locked}
        onWheel={onWheel}
        onChange={(e) => {
          if (locked) return;
          setText(e.target.value);
          data.onTextChange?.(id, e.target.value);
        }}
        placeholder="Write a note..."
      />
      <Handle type="source" position={Position.Bottom} style={{ visibility: "hidden" }} />
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
      if (hiddenIds.has(qid) || (!nodes[qid] && !qid.startsWith("note-"))) continue;
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
  const currentTreeId = useStore((s) => s.currentTreeId);
  const treeNotes = useStore((s) => s.trees.find((t) => t.id === s.currentTreeId)?.notes ?? "[]");
  const [noteNodes, setNoteNodes] = useState<Node[]>([]);
  const notesInitRef = useRef(false);

  const saveNotesRef = useRef<() => void>(() => {});

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
      // Save position/size changes for notes
      if (noteChanges.some((c) => c.type === "position" || c.type === "dimensions")) {
        saveNotesRef.current();
      }
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

  // Stable ref callbacks so note data objects don't change on re-render
  const noteTextRef = useRef<Record<string, string>>({});
  const onNoteTextChange = useCallback((id: string, text: string) => {
    noteTextRef.current[id] = text;
    saveNotesRef.current();
  }, []);

  const onNoteDuplicate = useCallback((sourceId: string) => {
    setNoteNodes((prev) => {
      const src = prev.find((n) => n.id === sourceId);
      if (!src) return prev;
      const newId = `note-${Date.now()}`;
      const srcText = noteTextRef.current[sourceId] ?? "";
      noteTextRef.current[newId] = srcText;
      return [...prev, {
        id: newId,
        type: "note" as const,
        position: { x: src.position.x + 30, y: src.position.y + 30 },
        data: { text: srcText, onTextChange: onNoteTextChange, onDuplicate: onNoteDuplicateRef.current },
        draggable: true,
        style: { width: (src.style?.width as number) ?? 180, height: (src.style?.height as number) ?? 140 },
      }];
    });
    setTimeout(() => saveNotesRef.current(), 100);
  }, [onNoteTextChange]);
  const onNoteDuplicateRef = useRef(onNoteDuplicate);
  onNoteDuplicateRef.current = onNoteDuplicate;

  // Load notes from tree on mount
  useEffect(() => {
    if (notesInitRef.current) return;
    notesInitRef.current = true;
    try {
      const saved: StickyNote[] = JSON.parse(treeNotes);
      if (saved.length > 0) {
        const loaded: Node[] = saved.map((n) => {
          noteTextRef.current[n.id] = n.text;
          return {
            id: n.id,
            type: "note" as const,
            position: { x: n.x, y: n.y },
            data: { text: n.text, onTextChange: onNoteTextChange, onDuplicate: onNoteDuplicateRef.current },
            draggable: true,
            style: { width: n.width, height: n.height },
          };
        });
        setNoteNodes(loaded);
      }
    } catch {}
  }, [treeNotes, onNoteTextChange]);

  // Save notes to backend (debounced)
  const saveTimerRef = useRef<ReturnType<typeof setTimeout>>(0 as never);
  const saveNotesDebounced = useCallback(() => {
    clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      if (!currentTreeId) return;
      setNoteNodes((cur) => {
        const data: StickyNote[] = cur.map((n) => ({
          id: n.id,
          text: noteTextRef.current[n.id] ?? "",
          x: n.position.x,
          y: n.position.y,
          width: (n.style?.width as number) ?? 180,
          height: (n.style?.height as number) ?? 140,
        }));
        send({ type: WS.UPDATE_TREE_SETTINGS, tree_id: currentTreeId, notes: JSON.stringify(data) });
        return cur;
      });
    }, 800);
  }, [currentTreeId]);
  saveNotesRef.current = saveNotesDebounced;

  const addNote = useCallback(() => {
    const vp = reactFlowInstance.getViewport();
    const x = (-vp.x + window.innerWidth / 2) / vp.zoom - 75;
    const y = (-vp.y + window.innerHeight / 2) / vp.zoom - 50;
    setNoteNodes((prev) => [...prev, {
      id: `note-${Date.now()}`,
      type: "note",
      position: { x, y },
      data: { text: "", onTextChange: onNoteTextChange, onDuplicate: onNoteDuplicateRef.current },
      draggable: true,
      style: { width: 180, height: 140 },
    }]);
    // Save after adding
    setTimeout(() => saveNotesDebounced(), 100);
  }, [reactFlowInstance, onNoteTextChange, saveNotesDebounced]);

  // Merge tree nodes + note nodes
  const allNodes = useMemo(() => [...flowNodes, ...noteNodes], [flowNodes, noteNodes]);

  // Imperatively update quote edge SVG paths during drag — runs synchronously
  // in the mouse-event handler, so the arrow and node move in the same paint frame.
  const rfStoreApi = useStoreApi();
  const onNodeDrag = useCallback((_: React.MouseEvent, node: Node) => {
    const { nodeLookup } = rfStoreApi.getState();
    quoteEdgeRegistry.forEach(({ el, source, target }) => {
      if (source !== node.id && target !== node.id) return;
      const sn = nodeLookup.get(source);
      const tn = nodeLookup.get(target);
      if (!sn || !tn) return;
      const sx = source === node.id ? node.position.x : (sn.internals?.positionAbsolute?.x ?? 0);
      const sy = source === node.id ? node.position.y : (sn.internals?.positionAbsolute?.y ?? 0);
      const sw = sn.measured?.width ?? 0;
      const sh = sn.measured?.height ?? 0;
      const tx_ = target === node.id ? node.position.x + (tn.measured?.width ?? 0) / 2
        : (tn.internals?.positionAbsolute?.x ?? 0) + (tn.measured?.width ?? 0) / 2;
      const ty_ = target === node.id ? node.position.y
        : (tn.internals?.positionAbsolute?.y ?? 0);
      el.setAttribute("d", computeQuotePath(sx, sy, sw, sh, tx_, ty_));
    });
  }, [rfStoreApi]);

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
      onNodeDrag={onNodeDrag}
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
        🗒<span className="tooltip">Add note</span>
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
