import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { createPortal } from "react-dom";
import { useStore, actions, type FileQuote } from "../store";
import { send, WS } from "../ws";
import hljs from "highlight.js";

let _fqId = 0;

// ── File type detection ──────────────────────────────────────────────

const IMAGE_EXT = new Set(["png","jpg","jpeg","gif","svg","webp","ico","bmp","avif"]);
const VIDEO_EXT = new Set(["mp4","webm","mov","avi","mkv","ogg"]);
const AUDIO_EXT = new Set(["mp3","wav","ogg","flac","aac","m4a","wma"]);
const PDF_EXT = new Set(["pdf"]);

function extOf(path: string): string {
  const dot = path.lastIndexOf(".");
  return dot >= 0 ? path.slice(dot + 1).toLowerCase() : "";
}

type FileKind = "image" | "video" | "audio" | "pdf" | "text";

function fileKind(path: string): FileKind {
  const ext = extOf(path);
  if (IMAGE_EXT.has(ext)) return "image";
  if (VIDEO_EXT.has(ext)) return "video";
  if (AUDIO_EXT.has(ext)) return "audio";
  if (PDF_EXT.has(ext)) return "pdf";
  return "text";
}

// ── Syntax highlighting ──────────────────────────────────────────────

function highlightCode(content: string, filePath: string): string {
  const ext = extOf(filePath);
  // Map file extensions to highlight.js language names
  const langMap: Record<string, string> = {
    ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript",
    py: "python", rb: "ruby", rs: "rust", go: "go", java: "java",
    c: "c", cpp: "cpp", h: "c", hpp: "cpp", cs: "csharp",
    sh: "bash", bash: "bash", zsh: "bash", fish: "bash",
    json: "json", yaml: "yaml", yml: "yaml", toml: "ini",
    xml: "xml", html: "xml", htm: "xml", svg: "xml",
    css: "css", scss: "scss", less: "less",
    sql: "sql", md: "markdown", dockerfile: "dockerfile",
    makefile: "makefile", cmake: "cmake",
    kt: "kotlin", swift: "swift", dart: "dart",
    lua: "lua", r: "r", php: "php", pl: "perl",
    ex: "elixir", exs: "elixir", erl: "erlang",
    hs: "haskell", ml: "ocaml", clj: "clojure",
    tf: "hcl", vue: "xml", svelte: "xml",
  };
  // Also check filename (e.g. Dockerfile, Makefile)
  const filename = filePath.split("/").pop()?.toLowerCase() || "";
  const lang = langMap[ext] ||
    (filename === "dockerfile" ? "dockerfile" : "") ||
    (filename === "makefile" ? "makefile" : "") ||
    (filename.endsWith(".env") ? "bash" : "");

  if (lang) {
    try {
      return hljs.highlight(content, { language: lang }).value;
    } catch { /* fall through */ }
  }
  // Auto-detect
  try {
    const result = hljs.highlightAuto(content);
    if (result.relevance > 5) return result.value;
  } catch { /* fall through */ }
  // Escape HTML for plain text
  return content.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ── File tree grouping ───────────────────────────────────────────────

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

function DownloadBtn({ href, title, className }: { href: string; title: string; className?: string }) {
  return (
    <a
      href={href}
      title={title}
      className={`filebrowser-dl-btn ${className || ""}`}
      onClick={(e) => e.stopPropagation()}
      download
    >
      <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M8 2v9M4 8l4 4 4-4M2 14h12" />
      </svg>
    </a>
  );
}

function QuoteBtn({ onClick, title }: { onClick: (e: React.MouseEvent) => void; title: string }) {
  return (
    <button className="filebrowser-quote-btn" onClick={onClick} title={title}>
      Quote
    </button>
  );
}

function FileTreeNode({ dir, selectedFile, onSelect, nodeId, nodeLabel, depth, isSelfQuote }: {
  dir: TreeDir;
  selectedFile: string | null;
  onSelect: (path: string) => void;
  nodeId: string;
  nodeLabel: string;
  depth: number;
  isSelfQuote: boolean;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const toggleDir = useCallback((path: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  const addQuote = useCallback((type: FileQuote["type"], path: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (isSelfQuote) {
      const prefix = type === "folder" ? "Folder" : "File";
      actions.appendToInput(`${prefix}: ${path}${type === "folder" ? "/" : ""}`);
    } else {
      const label = `${path}${type === "folder" ? "/" : ""}`;
      actions.addFileQuote({
        id: `fq-${++_fqId}`,
        nodeId,
        type,
        path,
        label,
      });
    }
  }, [nodeId, nodeLabel, isSelfQuote]);

  return (
    <>
      {dir.dirs.map((d) => (
        <div key={d.path}>
          <div className="filebrowser-dir" style={{ paddingLeft: depth * 12 + 8 }} onClick={(e) => toggleDir(d.path, e)}>
            <span className="filebrowser-dir-arrow">{expanded.has(d.path) ? "▾" : "▸"}</span>
            <span className="filebrowser-dir-name">{d.name}/</span>
            <QuoteBtn onClick={(e) => addQuote("folder", d.path, e)} title={`Quote ${d.name}/`} />
            <DownloadBtn
              href={`/api/download-zip/${nodeId}?subpath=${encodeURIComponent(d.path)}`}
              title={`Download ${d.name}/ as zip`}
            />
          </div>
          {expanded.has(d.path) && (
            <FileTreeNode dir={d} selectedFile={selectedFile} onSelect={onSelect} nodeId={nodeId} nodeLabel={nodeLabel} depth={depth + 1} isSelfQuote={isSelfQuote} />
          )}
        </div>
      ))}
      {dir.files.map((f) => {
        const name = f.split("/").pop()!;
        const kind = fileKind(f);
        const icon = kind === "image" ? "🖼" : kind === "video" ? "🎬" : kind === "audio" ? "🎵" : kind === "pdf" ? "📄" : "";
        return (
          <div
            key={f}
            className={`filebrowser-file ${selectedFile === f ? "active" : ""}`}
            style={{ paddingLeft: depth * 12 + 8 }}
            onClick={() => onSelect(f)}
          >
            {icon && <span className="filebrowser-file-icon">{icon}</span>}
            <span className="filebrowser-file-name">{name}</span>
            <QuoteBtn onClick={(e) => addQuote("file", f, e)} title={`Quote ${name}`} />
            <DownloadBtn
              href={`/api/download/${nodeId}/${f}`}
              title={`Download ${name}`}
            />
          </div>
        );
      })}
    </>
  );
}

// ── Diff parser ──────────────────────────────────────────────────────

type DiffFileStatus = "modified" | "added" | "deleted" | "renamed" | "mode-change";

interface DiffFile {
  path: string;
  oldPath?: string;        // for renames
  status: DiffFileStatus;
  binary: boolean;
  additions: number;
  deletions: number;
  hunks: DiffHunk[];
  similarity?: number;     // rename similarity %
  oldMode?: string;
  newMode?: string;
}

interface DiffHunk {
  header: string;
  lines: DiffLine[];
}

interface DiffLine {
  type: "add" | "del" | "ctx";
  oldNum: number | null;
  newNum: number | null;
  text: string;
}

function parseDiff(raw: string): DiffFile[] {
  const files: DiffFile[] = [];
  const fileSections = raw.split(/^diff --git /m).filter(Boolean);

  for (const section of fileSections) {
    const lines = section.split("\n");
    const headerMatch = lines[0]?.match(/a\/(.+?) b\/(.+)/);
    const path = headerMatch ? headerMatch[2] : "unknown";

    let status: DiffFileStatus = "modified";
    let oldPath: string | undefined;
    let binary = false;
    let similarity: number | undefined;
    let oldMode: string | undefined;
    let newMode: string | undefined;
    let additions = 0;
    let deletions = 0;
    const hunks: DiffHunk[] = [];
    let currentHunk: DiffHunk | null = null;
    let oldLine = 0;
    let newLine = 0;

    for (let i = 1; i < lines.length; i++) {
      const line = lines[i];

      // Detect file status from metadata
      if (line.startsWith("new file mode")) {
        status = "added";
        newMode = line.slice("new file mode ".length);
        continue;
      }
      if (line.startsWith("deleted file mode")) {
        status = "deleted";
        oldMode = line.slice("deleted file mode ".length);
        continue;
      }
      if (line.startsWith("old mode ")) { oldMode = line.slice("old mode ".length); continue; }
      if (line.startsWith("new mode ")) {
        newMode = line.slice("new mode ".length);
        if (!oldPath && status === "modified") status = "mode-change";
        continue;
      }
      const simMatch = line.match(/^similarity index (\d+)%/);
      if (simMatch) { similarity = parseInt(simMatch[1], 10); continue; }
      if (line.startsWith("rename from ")) { oldPath = line.slice("rename from ".length); status = "renamed"; continue; }
      if (line.startsWith("rename to ")) continue;
      if (line.startsWith("Binary files")) { binary = true; continue; }
      if (line.startsWith("---") || line.startsWith("+++") || line.startsWith("index ")) continue;

      // Hunk header
      const hunkMatch = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)/);
      if (hunkMatch) {
        oldLine = parseInt(hunkMatch[1], 10);
        newLine = parseInt(hunkMatch[2], 10);
        currentHunk = { header: line, lines: [] };
        hunks.push(currentHunk);
        continue;
      }

      if (!currentHunk) continue;

      if (line.startsWith("+")) {
        additions++;
        currentHunk.lines.push({ type: "add", oldNum: null, newNum: newLine, text: line.slice(1) });
        newLine++;
      } else if (line.startsWith("-")) {
        deletions++;
        currentHunk.lines.push({ type: "del", oldNum: oldLine, newNum: null, text: line.slice(1) });
        oldLine++;
      } else if (line.startsWith(" ") || line === "") {
        currentHunk.lines.push({ type: "ctx", oldNum: oldLine, newNum: newLine, text: line.slice(1) });
        oldLine++;
        newLine++;
      }
    }

    // If mode changed but there are also code changes, it's a modify
    if (status === "mode-change" && hunks.length > 0) status = "modified";

    files.push({ path, oldPath, status, binary, additions, deletions, hunks, similarity, oldMode, newMode });
  }
  return files;
}

// ── Diff viewer ──────────────────────────────────────────────────────

const STATUS_LABELS: Record<DiffFileStatus, string> = {
  added: "NEW", deleted: "DELETED", renamed: "RENAMED", modified: "MODIFIED", "mode-change": "MODE",
};
const STATUS_ICONS: Record<string, string> = {
  image: "\u{1F5BC}", video: "\u{1F3AC}", audio: "\u{1F3B5}", pdf: "\u{1F4C4}",
};

function DiffFileSection({ file, nodeId }: { file: DiffFile; nodeId: string | undefined }) {
  const kind = fileKind(file.path);
  const isBinaryKind = kind !== "text";
  const statusLabel = STATUS_LABELS[file.status];

  return (
    <div className={`diff-file diff-file-${file.status}`}>
      <div className="diff-file-header">
        <span className={`diff-file-badge diff-badge-${file.status}`}>{statusLabel}</span>
        {isBinaryKind && <span className="diff-file-type-icon">{STATUS_ICONS[kind] || ""}</span>}
        <span className="diff-file-name">
          {file.oldPath && file.status === "renamed" ? (
            <>{file.oldPath} <span className="diff-rename-arrow">&rarr;</span> {file.path}</>
          ) : file.path}
        </span>
        <span className="diff-file-stats">
          {file.similarity != null && <span className="diff-stat-sim">{file.similarity}%</span>}
          {file.additions > 0 && <span className="diff-stat-add">+{file.additions}</span>}
          {file.deletions > 0 && <span className="diff-stat-del">-{file.deletions}</span>}
          {file.binary && <span className="diff-stat-bin">BIN</span>}
        </span>
      </div>

      {/* Mode change note */}
      {file.oldMode && file.newMode && file.oldMode !== file.newMode && (
        <div className="diff-mode-change">{file.oldMode} &rarr; {file.newMode}</div>
      )}

      {/* Binary file body */}
      {file.binary && (
        <div className="diff-binary-body">
          {kind === "image" && nodeId && file.status !== "deleted" ? (
            <div className="diff-binary-preview">
              <img src={`/api/files/${nodeId}/${file.path}`} alt={file.path} />
            </div>
          ) : kind === "video" && nodeId && file.status !== "deleted" ? (
            <div className="diff-binary-preview">
              <video controls src={`/api/files/${nodeId}/${file.path}`} />
            </div>
          ) : kind === "audio" && nodeId && file.status !== "deleted" ? (
            <div className="diff-binary-preview diff-binary-audio">
              <audio controls src={`/api/files/${nodeId}/${file.path}`} />
            </div>
          ) : (
            <div className="diff-binary-notice">
              Binary file {file.status === "added" ? "added" : file.status === "deleted" ? "deleted" : "changed"}
              {isBinaryKind && ` (${kind})`}
            </div>
          )}
        </div>
      )}

      {/* Code hunks */}
      {file.hunks.length > 0 && (
        <div className="diff-file-body">
          {file.hunks.map((hunk, hi) => (
            <div key={hi} className="diff-hunk-block">
              <div className="diff-hunk-header">{hunk.header}</div>
              {hunk.lines.map((line, li) => (
                <div key={li} className={`diff-line diff-line-${line.type}`}>
                  <span className="diff-ln diff-ln-old">{line.oldNum ?? ""}</span>
                  <span className="diff-ln diff-ln-new">{line.newNum ?? ""}</span>
                  <span className="diff-indicator">{line.type === "add" ? "+" : line.type === "del" ? "-" : " "}</span>
                  <span className="diff-text">{line.text}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function DiffViewer({ diff, nodeId, nodeLabel, isSelfQuote }: { diff: string | undefined; nodeId: string | undefined; nodeLabel: string; isSelfQuote: boolean }) {
  const files = useMemo(() => diff ? parseDiff(diff) : [], [diff]);
  const diffContainerRef = useRef<HTMLDivElement>(null);

  // DOM-based text selection quoting
  useEffect(() => {
    const el = diffContainerRef.current;
    if (!el || !diff || !nodeId) return;

    const btn = document.createElement("button");
    btn.className = "selection-quote-btn";
    btn.textContent = "Quote";
    btn.style.display = "none";
    document.body.appendChild(btn);

    let selectedText = "";
    const hideBtn = () => { btn.style.display = "none"; };

    const onMouseUp = () => {
      // Defer so the browser can collapse the selection first (e.g. click-to-deselect)
      requestAnimationFrame(() => {
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
      });
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
      if (selectedText && nodeId) {
        if (isSelfQuote) {
          actions.appendToInput(`Diff:\n${selectedText}`);
        } else {
          actions.addFileQuote({
            id: `fq-${++_fqId}`,
            nodeId,
            type: "diff",
            content: selectedText,
            label: `diff selection`,
          });
        }
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
  }, [diff, nodeId, nodeLabel, isSelfQuote]);

  if (diff === undefined) return <div className="filebrowser-placeholder">Loading diff...</div>;
  if (diff === "") return <div className="filebrowser-placeholder">No changes</div>;

  const totalAdd = files.reduce((s, f) => s + f.additions, 0);
  const totalDel = files.reduce((s, f) => s + f.deletions, 0);
  const binCount = files.filter(f => f.binary).length;
  const newCount = files.filter(f => f.status === "added").length;
  const delCount = files.filter(f => f.status === "deleted").length;
  const renCount = files.filter(f => f.status === "renamed").length;

  return (
    <div className="diff-viewer" ref={diffContainerRef}>
      <div className="diff-hint">Select text and click Quote to add a diff selection</div>
      <div className="diff-summary">
        <span>{files.length} file{files.length !== 1 ? "s" : ""} changed</span>
        {totalAdd > 0 && <span className="diff-stat-add"> +{totalAdd}</span>}
        {totalDel > 0 && <span className="diff-stat-del"> -{totalDel}</span>}
        {newCount > 0 && <span className="diff-summary-detail"> ({newCount} new)</span>}
        {delCount > 0 && <span className="diff-summary-detail"> ({delCount} deleted)</span>}
        {renCount > 0 && <span className="diff-summary-detail"> ({renCount} renamed)</span>}
        {binCount > 0 && <span className="diff-summary-detail"> ({binCount} binary)</span>}
      </div>
      {files.map((f, i) => <DiffFileSection key={i} file={f} nodeId={nodeId} />)}
    </div>
  );
}

// ── Content viewer ───────────────────────────────────────────────────

function ContentViewer({ nodeId, filePath, content, nodeLabel, isSelfQuote }: {
  nodeId: string;
  filePath: string;
  content: string | undefined;
  nodeLabel: string;
  isSelfQuote: boolean;
}) {
  const kind = fileKind(filePath);
  const rawUrl = `/api/files/${nodeId}/${filePath}`;
  const codeRef = useRef<HTMLDivElement>(null);

  // DOM-based text selection quoting for text files
  useEffect(() => {
    const el = codeRef.current;
    if (!el || !content) return;

    const btn = document.createElement("button");
    btn.className = "selection-quote-btn";
    btn.textContent = "Quote";
    btn.style.display = "none";
    document.body.appendChild(btn);

    let selectedText = "";
    const hideBtn = () => { btn.style.display = "none"; };

    const onMouseUp = () => {
      // Defer so the browser can collapse the selection first (e.g. click-to-deselect)
      requestAnimationFrame(() => {
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
      });
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
        if (isSelfQuote) {
          actions.appendToInput(`File: ${filePath}\n${selectedText}`);
        } else {
          actions.addFileQuote({
            id: `fq-${++_fqId}`,
            nodeId,
            type: "file",
            path: filePath,
            content: selectedText,
            label: `${filePath} (selection)`,
          });
        }
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
  }, [content, nodeId, filePath, nodeLabel, isSelfQuote]);

  if (kind === "image") {
    return (
      <div className="filebrowser-media">
        <img src={rawUrl} alt={filePath} />
      </div>
    );
  }
  if (kind === "video") {
    return (
      <div className="filebrowser-media">
        <video controls src={rawUrl} />
      </div>
    );
  }
  if (kind === "audio") {
    return (
      <div className="filebrowser-media filebrowser-audio">
        <div className="filebrowser-audio-name">{filePath.split("/").pop()}</div>
        <audio controls src={rawUrl} />
      </div>
    );
  }
  if (kind === "pdf") {
    return (
      <div className="filebrowser-pdf">
        <iframe src={rawUrl} title={filePath} />
      </div>
    );
  }

  // Text/code
  if (content === undefined) return <div className="filebrowser-placeholder">Loading...</div>;

  const highlighted = highlightCode(content, filePath);
  const lines = highlighted.split("\n");

  return (
    <div className="filebrowser-code-wrap" ref={codeRef}>
      <pre className="filebrowser-code">
        <code dangerouslySetInnerHTML={{
          __html: lines.map((line, i) =>
            `<span class="filebrowser-line"><span class="filebrowser-ln">${i + 1}</span>${line}</span>`
          ).join("\n"),
        }} />
      </pre>
    </div>
  );
}

// ── Main modal ───────────────────────────────────────────────────────

export default function FilesPanel() {
  const panel = useStore((s) => s.filesPanel);
  const nodes = useStore((s) => s.nodes);
  const nodeFiles = useStore((s) => s.nodeFiles);
  const nodeDiffs = useStore((s) => s.nodeDiffs);
  const fileContents = useStore((s) => s.fileContents);
  const selectedNodeId = useStore((s) => s.selectedNodeId);
  const overlayRef = useRef<HTMLDivElement>(null);

  const nodeId = panel?.nodeId;
  const isSelfQuote = nodeId === selectedNodeId;
  const tab = panel?.tab || "files";
  const selectedFile = panel?.selectedFile || null;
  const node = nodeId ? nodes[nodeId] : null;
  const files = nodeId ? nodeFiles[nodeId] || [] : [];
  const diff = nodeId ? nodeDiffs[nodeId] : undefined;
  const contentKey = selectedFile && nodeId ? `${nodeId}:${selectedFile}` : null;
  const content = contentKey ? fileContents[contentKey] : undefined;

  const fileTree = useMemo(() => buildFileTree(files), [files]);

  const handleClose = useCallback(() => actions.closeFilesPanel(), []);

  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === "Escape") handleClose();
  }, [handleClose]);

  useEffect(() => {
    if (!panel) return;
    window.addEventListener("keydown", handleKeyDown);
    const el = overlayRef.current;
    const stopWheel = (e: WheelEvent) => e.stopPropagation();
    if (el) el.addEventListener("wheel", stopWheel, { passive: false });
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      if (el) el.removeEventListener("wheel", stopWheel);
    };
  }, [panel, handleKeyDown]);

  const handleSelectFile = useCallback((path: string) => {
    if (!nodeId) return;
    actions.selectFile(path);
    const kind = fileKind(path);
    // Only fetch text content for text files (binary files use HTTP endpoint)
    if (kind === "text") {
      const key = `${nodeId}:${path}`;
      if (fileContents[key] === undefined) {
        send({ type: WS.GET_FILE_CONTENT, node_id: nodeId, file_path: path });
      }
    }
  }, [nodeId, fileContents]);

  const handleTabSwitch = useCallback((t: "files" | "diff") => {
    actions.setFilesPanelTab(t);
    if (t === "diff" && nodeId && diff === undefined) {
      send({ type: WS.GET_NODE_DIFF, node_id: nodeId });
    }
  }, [nodeId, diff]);

  if (!panel) return null;

  return createPortal(
    <div ref={overlayRef} className="filebrowser-overlay" onClick={handleClose}>
      <div className="filebrowser" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="filebrowser-header">
          <div className="filebrowser-tabs">
            <button
              className={`filebrowser-tab ${tab === "files" ? "active" : ""}`}
              onClick={() => handleTabSwitch("files")}
            >
              Files
            </button>
            <button
              className={`filebrowser-tab ${tab === "diff" ? "active" : ""}`}
              onClick={() => handleTabSwitch("diff")}
            >
              Diff
            </button>
          </div>
          <span className="filebrowser-title">{node?.label || "Files"}</span>
          {nodeId && (
            <a
              className="filebrowser-dl-all"
              href={`/api/download-zip/${nodeId}`}
              title="Download entire workspace as zip"
              download
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M8 2v9M4 8l4 4 4-4M2 14h12" />
              </svg>
              <span>Download All</span>
            </a>
          )}
          <button className="filebrowser-close" onClick={handleClose}>&times;</button>
        </div>

        {/* Body */}
        <div className="filebrowser-body">
          {tab === "files" && (
            <>
              {/* Sidebar file tree */}
              <div className="filebrowser-sidebar">
                {files.length === 0 ? (
                  <div className="filebrowser-placeholder">No files</div>
                ) : (
                  <FileTreeNode dir={fileTree} selectedFile={selectedFile} onSelect={handleSelectFile} nodeId={nodeId!} nodeLabel={node?.label || "node"} depth={0} isSelfQuote={isSelfQuote} />
                )}
              </div>
              {/* Content area */}
              <div className="filebrowser-content">
                {selectedFile ? (
                  <>
                    <div className="filebrowser-filepath">
                      <span>{selectedFile}</span>
                      <a
                        className="filebrowser-filepath-dl"
                        href={`/api/download/${nodeId}/${selectedFile}`}
                        title="Download file"
                        download
                      >
                        <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M8 2v9M4 8l4 4 4-4M2 14h12" />
                        </svg>
                      </a>
                    </div>
                    <ContentViewer nodeId={nodeId!} filePath={selectedFile} content={content} nodeLabel={node?.label || "node"} isSelfQuote={isSelfQuote} />
                  </>
                ) : (
                  <div className="filebrowser-placeholder">
                    Select a file to view
                  </div>
                )}
              </div>
            </>
          )}
          {tab === "diff" && (
            <div className="filebrowser-content">
              <DiffViewer diff={diff} nodeId={nodeId} nodeLabel={node?.label || "node"} isSelfQuote={isSelfQuote} />
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
