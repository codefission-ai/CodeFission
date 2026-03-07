import { useEffect, useRef, useState, useCallback } from "react";
import TreeList from "./components/TreeList";
import Canvas from "./components/Canvas";
import ChatPanel from "./components/ChatPanel";
import SettingsPanel from "./components/SettingsPanel";
import FilesPanel from "./components/FilesPanel";
import { connectWs } from "./ws";
import { useStore, actions } from "./store";

function SidebarIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
      <rect x="2" y="3" width="14" height="12" rx="2" />
      <line x1="7" y1="3" x2="7" y2="15" />
    </svg>
  );
}

function ChatIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 4a2 2 0 012-2h8a2 2 0 012 2v6a2 2 0 01-2 2H7l-3 3V12H5a2 2 0 01-2-2V4z" />
    </svg>
  );
}

function GearIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="9" cy="9" r="2.5" />
      <path d="M7.5 2.5h3l.4 1.6a5.5 5.5 0 011.3.7l1.5-.5 1.5 2.6-1.1 1.1a5.5 5.5 0 010 1.5l1.1 1.1-1.5 2.6-1.5-.5a5.5 5.5 0 01-1.3.7l-.4 1.6h-3l-.4-1.6a5.5 5.5 0 01-1.3-.7l-1.5.5-1.5-2.6 1.1-1.1a5.5 5.5 0 010-1.5L2.8 6.9l1.5-2.6 1.5.5a5.5 5.5 0 011.3-.7l.4-1.6z" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M13.5 9.5A6 6 0 016.5 2.5a6 6 0 100 11 6 6 0 007-4z" />
    </svg>
  );
}

function SunIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="8" cy="8" r="3" />
      <line x1="8" y1="1" x2="8" y2="3" />
      <line x1="8" y1="13" x2="8" y2="15" />
      <line x1="1" y1="8" x2="3" y2="8" />
      <line x1="13" y1="8" x2="15" y2="8" />
      <line x1="3.05" y1="3.05" x2="4.46" y2="4.46" />
      <line x1="11.54" y1="11.54" x2="12.95" y2="12.95" />
      <line x1="12.95" y1="3.05" x2="11.54" y2="4.46" />
      <line x1="4.46" y1="11.54" x2="3.05" y2="12.95" />
    </svg>
  );
}

export default function App() {
  const hasTree = useStore((s) => !!s.currentTreeId);
  const treeName = useStore((s) => {
    const tree = s.trees.find((t) => t.id === s.currentTreeId);
    return tree?.name || "";
  });

  // Dark mode
  const [darkMode, setDarkMode] = useState(() => {
    return localStorage.getItem("theme") === "dark";
  });
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", darkMode ? "dark" : "light");
    localStorage.setItem("theme", darkMode ? "dark" : "light");
  }, [darkMode]);

  // Panel widths & collapsed state
  const [sidebarWidth, setSidebarWidth] = useState(220);
  const [chatWidth, setChatWidth] = useState(380);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [chatCollapsed, setChatCollapsed] = useState(true);

  const sidebarRef = useRef<HTMLDivElement>(null);
  const chatRef = useRef<HTMLDivElement>(null);

  useEffect(() => { connectWs(); }, []);

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.metaKey && e.key === "b") {
        e.preventDefault();
        setSidebarCollapsed((c) => !c);
      } else if (e.metaKey && e.key === "l") {
        e.preventDefault();
        setChatCollapsed((c) => !c);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const startDrag = useCallback((panel: "sidebar" | "chat") => {
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    // Disable transitions during drag for instant feedback
    if (panel === "sidebar" && sidebarRef.current) sidebarRef.current.style.transition = "none";
    if (panel === "chat" && chatRef.current) chatRef.current.style.transition = "none";

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
      // Re-enable transitions
      if (panel === "sidebar" && sidebarRef.current) sidebarRef.current.style.transition = "";
      if (panel === "chat" && chatRef.current) chatRef.current.style.transition = "";
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
      {/* Sidebar */}
      <div
        ref={sidebarRef}
        className={`sidebar ${sidebarCollapsed ? "collapsed" : ""}`}
        style={sidebarCollapsed ? undefined : { width: sidebarWidth }}
      >
        <TreeList />
      </div>

      {/* Sidebar resize handle */}
      {!sidebarCollapsed && (
        <div className="resize-handle" onMouseDown={() => startDrag("sidebar")} />
      )}

      {/* Main area: toolbar + canvas */}
      <div className="main-area">
        {/* Toolbar */}
        <div className="toolbar">
          <div className="toolbar-left">
            <button
              className="icon-btn has-tooltip"
              onClick={() => setSidebarCollapsed((c) => !c)}
            >
              <SidebarIcon />
              <span className="tooltip">Sidebar <kbd>{"\u2318"}B</kbd></span>
            </button>
          </div>
          <div className="toolbar-center">
            {treeName && <span className="toolbar-tree-name">{treeName}</span>}
          </div>
          <div className="toolbar-right">
            <button
              className="icon-btn has-tooltip"
              onClick={() => setChatCollapsed((c) => !c)}
            >
              <ChatIcon />
              <span className="tooltip">Chat <kbd>{"\u2318"}L</kbd></span>
            </button>
            <button
              className="theme-toggle has-tooltip"
              onClick={() => setDarkMode((d) => !d)}
              aria-label="Toggle dark mode"
            >
              {darkMode ? <SunIcon /> : <MoonIcon />}
              <span className="tooltip">{darkMode ? "Light mode" : "Dark mode"}</span>
            </button>
            <button
              className="icon-btn has-tooltip"
              onClick={() => actions.toggleSettings()}
            >
              <GearIcon />
              <span className="tooltip">Settings</span>
            </button>
          </div>
        </div>

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
              <div className="logo">Clawtree</div>
              <p className="empty-sub">Create a tree in the sidebar to begin branching conversations.</p>
            </div>
          )}
        </div>
      </div>

      {/* Chat resize handle */}
      {!chatCollapsed && (
        <div className="resize-handle" onMouseDown={() => startDrag("chat")} />
      )}

      {/* Right panel: ChatPanel */}
      <div
        ref={chatRef}
        className={`chat ${chatCollapsed ? "collapsed" : ""}`}
        style={chatCollapsed ? undefined : { width: chatWidth }}
      >
        <ChatPanel />
      </div>

      <FilesPanel />
      <SettingsPanel />
    </div>
  );
}
