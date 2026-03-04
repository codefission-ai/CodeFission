import { useEffect, useRef, useState, useCallback } from "react";
import TreeList from "./components/TreeList";
import Canvas from "./components/Canvas";
import ChatPanel from "./components/ChatPanel";
import FilesPanel from "./components/FilesPanel";
import { connectWs } from "./ws";
import { useStore } from "./store";

export default function App() {
  const hasTree = useStore((s) => !!s.currentTreeId);
  const filesPanel = useStore((s) => s.filesPanel);

  // Panel widths & collapsed state
  const [sidebarWidth, setSidebarWidth] = useState(220);
  const [chatWidth, setChatWidth] = useState(380);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [chatCollapsed, setChatCollapsed] = useState(true);

  const sidebarRef = useRef<HTMLDivElement>(null);
  const chatRef = useRef<HTMLDivElement>(null);

  useEffect(() => { connectWs(); }, []);

  const startDrag = useCallback((panel: "sidebar" | "chat") => {
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    const onMove = (e: MouseEvent) => {
      if (panel === "sidebar") {
        const w = Math.max(120, Math.min(400, e.clientX));
        if (sidebarRef.current) sidebarRef.current.style.width = w + "px";
      } else {
        const w = Math.max(240, Math.min(600, window.innerWidth - e.clientX));
        if (chatRef.current) chatRef.current.style.width = w + "px";
      }
    };
    const onUp = (e: MouseEvent) => {
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      if (panel === "sidebar") setSidebarWidth(Math.max(120, Math.min(400, e.clientX)));
      else setChatWidth(Math.max(240, Math.min(600, window.innerWidth - e.clientX)));
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }, []);

  return (
    <div className="app">
      {/* Sidebar toggle when collapsed */}
      {sidebarCollapsed && (
        <button className="panel-toggle left" onClick={() => setSidebarCollapsed(false)}>
          ▶
        </button>
      )}

      {/* Sidebar */}
      <div
        ref={sidebarRef}
        className={`sidebar ${sidebarCollapsed ? "collapsed" : ""}`}
        style={sidebarCollapsed ? undefined : { width: sidebarWidth }}
      >
        <TreeList onCollapse={() => setSidebarCollapsed(true)} />
      </div>

      {/* Sidebar resize handle */}
      {!sidebarCollapsed && (
        <div className="resize-handle" onMouseDown={() => startDrag("sidebar")} />
      )}

      {/* Canvas */}
      <div className="canvas">
        {hasTree ? <Canvas /> : (
          <div className="canvas-empty">
            <div className="empty-icon">
              <svg width="48" height="48" viewBox="0 0 48 48" fill="none">
                <circle cx="24" cy="20" r="6" stroke="currentColor" strokeWidth="1.5" fill="none" />
                <circle cx="12" cy="38" r="4" stroke="currentColor" strokeWidth="1.5" fill="none" />
                <circle cx="36" cy="38" r="4" stroke="currentColor" strokeWidth="1.5" fill="none" />
                <line x1="20" y1="25" x2="14" y2="34" stroke="currentColor" strokeWidth="1.5" />
                <line x1="28" y1="25" x2="34" y2="34" stroke="currentColor" strokeWidth="1.5" />
              </svg>
            </div>
            <div className="logo">RepoEvolve</div>
            <p className="empty-sub">Create a tree in the sidebar to begin branching conversations.</p>
          </div>
        )}
      </div>

      {/* Chat resize handle */}
      {!chatCollapsed && (
        <div className="resize-handle" onMouseDown={() => startDrag("chat")} />
      )}

      {/* Right panel: FilesPanel or ChatPanel */}
      <div
        ref={chatRef}
        className={`chat ${chatCollapsed && !filesPanel ? "collapsed" : ""}`}
        style={chatCollapsed && !filesPanel ? undefined : { width: chatWidth }}
      >
        {filesPanel ? (
          <FilesPanel />
        ) : (
          <ChatPanel onCollapse={() => setChatCollapsed(true)} />
        )}
      </div>

      {/* Chat toggle when collapsed */}
      {chatCollapsed && !filesPanel && (
        <button className="panel-toggle right" onClick={() => setChatCollapsed(false)}>
          ◀
        </button>
      )}
    </div>
  );
}
