import { useEffect, useRef, useState, useCallback } from "react";
import TreeList from "./components/TreeList";
import Canvas from "./components/Canvas";
import ChatPanel from "./components/ChatPanel";
import { connectWs } from "./ws";
import { useStore } from "./store";

export default function App() {
  const hasTree = useStore((s) => !!s.currentTreeId);

  // Panel widths & collapsed state
  const [sidebarWidth, setSidebarWidth] = useState(220);
  const [chatWidth, setChatWidth] = useState(380);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [chatCollapsed, setChatCollapsed] = useState(true);

  // Drag state
  const dragging = useRef<"sidebar" | "chat" | null>(null);

  useEffect(() => { connectWs(); }, []);

  const onMouseMove = useCallback((e: MouseEvent) => {
    if (dragging.current === "sidebar") {
      setSidebarWidth(Math.max(120, Math.min(400, e.clientX)));
    } else if (dragging.current === "chat") {
      setChatWidth(Math.max(240, Math.min(600, window.innerWidth - e.clientX)));
    }
  }, []);

  const onMouseUp = useCallback(() => {
    dragging.current = null;
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    window.removeEventListener("mousemove", onMouseMove);
    window.removeEventListener("mouseup", onMouseUp);
  }, [onMouseMove]);

  const startDrag = useCallback((panel: "sidebar" | "chat") => {
    dragging.current = panel;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
  }, [onMouseMove, onMouseUp]);

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
        className={`sidebar ${sidebarCollapsed ? "collapsed" : ""}`}
        style={sidebarCollapsed ? undefined : { width: sidebarWidth }}
      >
        <TreeList onCollapse={() => setSidebarCollapsed(true)} />
      </div>

      {/* Sidebar resize handle */}
      {!sidebarCollapsed && (
        <div
          className={`resize-handle ${dragging.current === "sidebar" ? "dragging" : ""}`}
          onMouseDown={() => startDrag("sidebar")}
        />
      )}

      {/* Canvas */}
      <div className="canvas">
        {hasTree ? <Canvas /> : (
          <div className="canvas-empty">
            <div className="logo">clawtree</div>
            <p>Create a tree to start exploring.</p>
          </div>
        )}
      </div>

      {/* Chat resize handle */}
      {!chatCollapsed && (
        <div
          className={`resize-handle ${dragging.current === "chat" ? "dragging" : ""}`}
          onMouseDown={() => startDrag("chat")}
        />
      )}

      {/* Chat panel */}
      <div
        className={`chat ${chatCollapsed ? "collapsed" : ""}`}
        style={chatCollapsed ? undefined : { width: chatWidth }}
      >
        <ChatPanel onCollapse={() => setChatCollapsed(true)} />
      </div>

      {/* Chat toggle when collapsed */}
      {chatCollapsed && (
        <button className="panel-toggle right" onClick={() => setChatCollapsed(false)}>
          ◀
        </button>
      )}
    </div>
  );
}
