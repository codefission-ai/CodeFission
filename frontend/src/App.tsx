import { useEffect } from "react";
import TreeList from "./components/TreeList";
import Canvas from "./components/Canvas";
import ChatPanel from "./components/ChatPanel";
import { connectWs } from "./ws";
import { useStore } from "./store";

export default function App() {
  const hasTree = useStore((s) => !!s.currentTreeId);

  useEffect(() => { connectWs(); }, []);

  return (
    <div className="app">
      <div className="sidebar">
        <TreeList />
      </div>
      <div className="canvas">
        {hasTree ? <Canvas /> : (
          <div className="canvas-empty">
            <div className="logo">clawtree</div>
            <p>Create a tree to start exploring.</p>
          </div>
        )}
      </div>
      <div className="chat">
        <ChatPanel />
      </div>
    </div>
  );
}
