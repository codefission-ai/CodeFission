import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { createPortal } from "react-dom";
import { useStore, actions, type FileQuote } from "../store";
import { send, WS } from "../ws";

interface TreeDir {
  name: string;
  path: string;
  dirs: TreeDir[];
  files: string[];
}

function buildFileTree(paths: string[]): TreeDir {
  const root: TreeDir = { name: "", path: "", dirs: [], files: [] };
  for (const p of paths) {
    const parts = p.split("/");
    let node = root;
    for (let i = 0; i < parts.length - 1; i++) {
      let child = node.dirs.find((d) => d.name === parts[i]);
      if (!child) {
        child = { name: parts[i], path: parts.slice(0, i + 1).join("/"), dirs: [], files: [] };
        node.dirs.push(child);
      }
      node = child;
    }
    node.files.push(p);
  }
  return root;
}

let _quoteId = 0;

function QuoteTreeNode({ dir, onQuote, depth }: {
  dir: TreeDir;
  onQuote: (type: "file" | "folder", path: string) => void;
  depth: number;
}) {
  return (
    <>
      {dir.dirs.map((d) => (
        <div key={d.path}>
          <div className="qb-item" style={{ paddingLeft: depth * 14 + 8 }}>
            <span className="qb-item-name">{d.name}/</span>
            <button className="qb-add-btn" onClick={() => onQuote("folder", d.path)} title="Quote entire folder">Quote</button>
          </div>
          <QuoteTreeNode dir={d} onQuote={onQuote} depth={depth + 1} />
        </div>
      ))}
      {dir.files.map((f) => {
        const name = f.split("/").pop()!;
        return (
          <div key={f} className="qb-item" style={{ paddingLeft: depth * 14 + 8 }}>
            <span className="qb-item-name">{name}</span>
            <button className="qb-add-btn" onClick={() => onQuote("file", f)} title="Quote this file">Quote</button>
          </div>
        );
      })}
    </>
  );
}

interface Props {
  nodeId: string;
  nodeLabel: string;
  onClose: () => void;
}

export default function QuoteBrowser({ nodeId, nodeLabel, onClose }: Props) {
  const files = useStore((s) => s.nodeFiles[nodeId] || []);
  const diff = useStore((s) => s.nodeDiffs[nodeId]);
  const pendingQuotes = useStore((s) => s.pendingQuotes);
  const [tab, setTab] = useState<"files" | "diff">("files");
  const overlayRef = useRef<HTMLDivElement>(null);
  const diffRef = useRef<HTMLPreElement>(null);

  const nodeQuotes = pendingQuotes.filter((q) => q.nodeId === nodeId);

  // Fetch files and diff on mount
  useEffect(() => {
    send({ type: WS.GET_NODE_FILES, node_id: nodeId });
    send({ type: WS.GET_NODE_DIFF, node_id: nodeId });
  }, [nodeId]);

  // Keyboard + wheel blocking
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    const el = overlayRef.current;
    const stopWheel = (e: WheelEvent) => e.stopPropagation();
    if (el) el.addEventListener("wheel", stopWheel, { passive: false });
    return () => {
      window.removeEventListener("keydown", onKey);
      if (el) el.removeEventListener("wheel", stopWheel);
    };
  }, [onClose]);

  const addQuote = useCallback((type: FileQuote["type"], path?: string, content?: string) => {
    const label = type === "diff"
      ? `diff selection`
      : `${path}${type === "folder" ? "/" : ""}`;
    actions.addFileQuote({
      id: `fq-${++_quoteId}`,
      nodeId,
      type,
      path,
      content,
      label,
    });
  }, [nodeId, nodeLabel]);

  // Ref for addQuote so DOM event handlers always get latest
  const addQuoteRef = useRef(addQuote);
  addQuoteRef.current = addQuote;

  // Text selection quoting on diff (DOM-based)
  useEffect(() => {
    const el = diffRef.current;
    if (!el || tab !== "diff") return;

    const btn = document.createElement("button");
    btn.className = "selection-quote-btn";
    btn.textContent = "Quote";
    btn.style.display = "none";
    document.body.appendChild(btn);

    let selectedText = "";
    const hideBtn = () => { btn.style.display = "none"; };

    const onMouseUp = () => {
      const sel = window.getSelection();
      const text = sel?.toString().trim();
      if (!text || !sel?.rangeCount) { hideBtn(); return; }
      const range = sel.getRangeAt(0);
      if (!el.contains(range.commonAncestorContainer)) { hideBtn(); return; }
      selectedText = text;
      const rect = range.getBoundingClientRect();
      btn.style.left = `${rect.left + rect.width / 2}px`;
      btn.style.top = `${rect.top - 4}px`;
      btn.style.display = "";
    };

    const onMouseDown = (e: MouseEvent) => {
      if (btn.contains(e.target as Node)) return;
      hideBtn();
    };

    const onBtnMouseDown = (e: MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
    };

    const onBtnClick = (e: MouseEvent) => {
      e.stopPropagation();
      if (selectedText) {
        addQuoteRef.current("diff", undefined, selectedText);
      }
      hideBtn();
      window.getSelection()?.removeAllRanges();
    };

    el.addEventListener("mouseup", onMouseUp);
    el.addEventListener("mousedown", onMouseDown);
    btn.addEventListener("mousedown", onBtnMouseDown);
    btn.addEventListener("click", onBtnClick);

    return () => {
      el.removeEventListener("mouseup", onMouseUp);
      el.removeEventListener("mousedown", onMouseDown);
      btn.removeEventListener("mousedown", onBtnMouseDown);
      btn.removeEventListener("click", onBtnClick);
      btn.remove();
    };
  }, [tab, diff]);

  const fileTree = useMemo(() => buildFileTree(files), [files]);

  return createPortal(
    <div ref={overlayRef} className="qb-overlay" onClick={onClose}>
      <div className="qb-modal" onClick={(e) => e.stopPropagation()}>
        <div className="qb-header">
          <div className="qb-tabs">
            <button className={`qb-tab ${tab === "files" ? "active" : ""}`} onClick={() => setTab("files")}>Files</button>
            <button className={`qb-tab ${tab === "diff" ? "active" : ""}`} onClick={() => setTab("diff")}>Diff</button>
          </div>
          <span className="qb-title">Quote from: {nodeLabel}</span>
          <button className="qb-close" onClick={onClose}>&times;</button>
        </div>
        <div className="qb-body">
          {tab === "files" && (
            <div className="qb-files">
              {files.length === 0 ? (
                <div className="qb-placeholder">No files</div>
              ) : (
                <QuoteTreeNode dir={fileTree} onQuote={addQuote} depth={0} />
              )}
            </div>
          )}
          {tab === "diff" && (
            <div className="qb-diff">
              {diff === undefined ? (
                <div className="qb-placeholder">Loading diff...</div>
              ) : diff === "" ? (
                <div className="qb-placeholder">No changes</div>
              ) : (<>
                <div className="qb-hint">Select text and click Quote to add a diff selection</div>
                <pre ref={diffRef} className="qb-diff-text">
                  {diff.split("\n").map((line, i) => {
                    const cls = line.startsWith("+") && !line.startsWith("+++")
                      ? "qb-line-add"
                      : line.startsWith("-") && !line.startsWith("---")
                        ? "qb-line-del"
                        : line.startsWith("@@")
                          ? "qb-line-hunk"
                          : "";
                    return <span key={i} className={cls}>{line}{"\n"}</span>;
                  })}
                </pre>
              </>)}
            </div>
          )}
        </div>
        {nodeQuotes.length > 0 && (
          <div className="qb-quoted">
            {nodeQuotes.map((q) => (
              <span key={q.id} className="quote-chip">
                <span className="quote-chip-label">
                  {q.type === "folder" ? "\u{1F4C1}" : q.type === "file" ? "\u{1F4C4}" : "\u{1F4CB}"} {q.path || "diff"}
                </span>
                <button className="quote-chip-remove" onClick={() => actions.removeFileQuote(q.id)}>&times;</button>
              </span>
            ))}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
