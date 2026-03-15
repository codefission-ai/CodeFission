import { useEffect, useRef, useState, useCallback } from "react";
import TreeList from "./components/TreeList";
import Canvas from "./components/Canvas";
import ProjectSetup from "./components/ProjectSetup";
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
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
      <path d="M9.528 1.718a.75.75 0 01.162.819A8.97 8.97 0 009 6a9 9 0 009 9 8.97 8.97 0 003.463-.69.75.75 0 01.981.98 10.503 10.503 0 01-9.694 6.46c-5.799 0-10.5-4.701-10.5-10.5 0-4.368 2.667-8.112 6.46-9.694a.75.75 0 01.818.162z" />
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
  const creatingProject = useStore((s) => s.creatingProject);
  const treeName = useStore((s) => {
    const tree = s.trees.find((t) => t.id === s.currentTreeId);
    return tree?.name || "";
  });

  // Dark mode
  const [darkMode, setDarkMode] = useState(() => {
    const stored = localStorage.getItem("theme");
    return stored === null ? true : stored === "dark";
  });
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", darkMode ? "dark" : "light");
    localStorage.setItem("theme", darkMode ? "dark" : "light");
  }, [darkMode]);

  // Sidebar open state from store (set by ws.ts based on URL)
  const sidebarOpen = useStore((s) => s.sidebarOpen);

  // Panel widths & collapsed state
  const [sidebarWidth, setSidebarWidth] = useState(220);
  const sidebarCollapsed = !sidebarOpen;
  const setSidebarCollapsed = (v: boolean | ((prev: boolean) => boolean)) => {
    const collapsed = typeof v === "function" ? v(!sidebarOpen) : v;
    actions.setSidebarOpen(!collapsed);
  };
  const sidebarRef = useRef<HTMLDivElement>(null);

  useEffect(() => { connectWs(); }, []);

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.metaKey && e.key === "b") {
        e.preventDefault();
        setSidebarCollapsed((c) => !c);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const startDrag = useCallback(() => {
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    if (sidebarRef.current) sidebarRef.current.style.transition = "none";

    const onMove = (e: MouseEvent) => {
      const w = Math.max(120, Math.min(400, e.clientX));
      if (sidebarRef.current) sidebarRef.current.style.width = w + "px";
    };
    const onUp = (e: MouseEvent) => {
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      if (sidebarRef.current) sidebarRef.current.style.transition = "";
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      setSidebarWidth(Math.max(120, Math.min(400, e.clientX)));
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
        <div className="resize-handle" onMouseDown={startDrag} />
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
            <span className="toolbar-tree-name">{treeName}</span>
          </div>
          <div className="toolbar-right">
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
          {creatingProject ? <ProjectSetup /> : hasTree ? <Canvas /> : (
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
              <div className="logo">CodeFission</div>
              <p className="empty-sub">Create a tree in the sidebar to begin branching conversations.</p>
            </div>
          )}
        </div>
      </div>

      <FilesPanel />
      <SettingsPanel />
    </div>
  );
}
