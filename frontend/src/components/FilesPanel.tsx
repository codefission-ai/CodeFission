import { useEffect, useCallback, useRef, useMemo } from "react";
import { createPortal } from "react-dom";
import { useStore, actions } from "../store";
import { send, WS } from "../ws";
import hljs from "highlight.js";

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

function FileTreeNode({ dir, selectedFile, onSelect, depth }: {
  dir: TreeDir;
  selectedFile: string | null;
  onSelect: (path: string) => void;
  depth: number;
}) {
  return (
    <>
      {dir.dirs.map((d) => (
        <div key={d.path}>
          <div className="filebrowser-dir" style={{ paddingLeft: depth * 12 + 8 }}>
            {d.name}/
          </div>
          <FileTreeNode dir={d} selectedFile={selectedFile} onSelect={onSelect} depth={depth + 1} />
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
            {name}
          </div>
        );
      })}
    </>
  );
}

// ── Diff viewer ──────────────────────────────────────────────────────

function DiffViewer({ diff }: { diff: string | undefined }) {
  if (diff === undefined) return <div className="filebrowser-placeholder">Loading diff...</div>;
  if (diff === "") return <div className="filebrowser-placeholder">No changes</div>;
  return (
    <pre className="filebrowser-diff">
      {diff.split("\n").map((line, i) => {
        let cls = "";
        if (line.startsWith("+") && !line.startsWith("+++")) cls = "diff-add";
        else if (line.startsWith("-") && !line.startsWith("---")) cls = "diff-del";
        else if (line.startsWith("@@")) cls = "diff-hunk";
        else if (line.startsWith("diff ") || line.startsWith("index ")) cls = "diff-meta";
        return <span key={i} className={cls}>{line}{"\n"}</span>;
      })}
    </pre>
  );
}

// ── Content viewer ───────────────────────────────────────────────────

function ContentViewer({ nodeId, filePath, content }: {
  nodeId: string;
  filePath: string;
  content: string | undefined;
}) {
  const kind = fileKind(filePath);
  const rawUrl = `/api/files/${nodeId}/${filePath}`;

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
    <div className="filebrowser-code-wrap">
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
  const overlayRef = useRef<HTMLDivElement>(null);

  const nodeId = panel?.nodeId;
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
                  <FileTreeNode dir={fileTree} selectedFile={selectedFile} onSelect={handleSelectFile} depth={0} />
                )}
              </div>
              {/* Content area */}
              <div className="filebrowser-content">
                {selectedFile ? (
                  <>
                    <div className="filebrowser-filepath">{selectedFile}</div>
                    <ContentViewer nodeId={nodeId!} filePath={selectedFile} content={content} />
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
              <DiffViewer diff={diff} />
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
