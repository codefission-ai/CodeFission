import { useState } from "react";
import { type ToolCall } from "../store";

const TOOL_CONFIGS: Record<string, { icon: string; getValue: (a: Record<string, unknown>) => string; style: string }> = {
  Bash:       { icon: "$",     getValue: (a) => String(a.command || ""), style: "terminal" },
  bash:       { icon: "$",     getValue: (a) => String(a.command || ""), style: "terminal" },
  Read:       { icon: "Read",  getValue: (a) => String(a.file_path || a.path || ""), style: "file" },
  read_file:  { icon: "Read",  getValue: (a) => String(a.file_path || a.path || ""), style: "file" },
  Write:      { icon: "Write", getValue: (a) => String(a.file_path || ""), style: "file" },
  write_file: { icon: "Write", getValue: (a) => String(a.file_path || ""), style: "file" },
  Edit:       { icon: "Edit",  getValue: (a) => String(a.file_path || ""), style: "file" },
  edit_file:  { icon: "Edit",  getValue: (a) => String(a.file_path || ""), style: "file" },
  Glob:       { icon: "Glob",  getValue: (a) => String(a.pattern || ""), style: "search" },
  Grep:       { icon: "Grep",  getValue: (a) => String(a.pattern || ""), style: "search" },
};

export default function ToolCallLine({ tc }: { tc: ToolCall }) {
  const [open, setOpen] = useState(false);
  const cfg = TOOL_CONFIGS[tc.name];
  const st = tc.status;

  if (cfg) {
    const value = cfg.getValue(tc.arguments);
    return (
      <div className={`tool-line ${cfg.style} ${st}`}>
        <span className="tool-line-label">{cfg.icon}</span>
        <span className="tool-line-value" title={value}>{value}</span>
        <span className={`tool-line-status ${st}`}>
          {st === "running" ? "\u27F3" : st === "done" ? "\u2713" : "\u2717"}
        </span>
        {tc.result && st !== "running" && (
          <div className="tool-line-details">
            <button className="tool-line-toggle" onClick={() => setOpen(!open)}>
              {open ? "\u25BE hide" : "\u25B8 result"}
            </button>
            {open && (
              <pre className={`tool-line-body ${tc.is_error ? "error" : ""}`}>
                {tc.result.length > 2000 ? tc.result.slice(0, 2000) + "\n\u2026" : tc.result}
              </pre>
            )}
          </div>
        )}
      </div>
    );
  }

  const argsStr = Object.keys(tc.arguments).length
    ? JSON.stringify(tc.arguments, null, 2)
    : "";

  return (
    <details className={`tool-generic ${st}`}>
      <summary className="tool-generic-summary">
        <span className="tool-generic-name">{tc.name || "tool"}</span>
        {argsStr && <span className="tool-generic-args">{argsStr.slice(0, 80)}</span>}
        <span className={`tool-line-status ${st}`}>
          {st === "running" ? "\u27F3" : st === "done" ? "\u2713" : "\u2717"}
        </span>
      </summary>
      {tc.result && (
        <pre className={`tool-generic-body ${tc.is_error ? "error" : ""}`}>
          {tc.result.length > 2000 ? tc.result.slice(0, 2000) + "\n\u2026" : tc.result}
        </pre>
      )}
    </details>
  );
}
