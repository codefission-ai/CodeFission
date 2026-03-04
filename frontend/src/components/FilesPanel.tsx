import { useStore, actions } from "../store";
import { send, WS } from "../ws";

export default function FilesPanel() {
  const panel = useStore((s) => s.filesPanel);
  const nodes = useStore((s) => s.nodes);
  const nodeFiles = useStore((s) => s.nodeFiles);
  const nodeDiffs = useStore((s) => s.nodeDiffs);
  const fileContents = useStore((s) => s.fileContents);

  if (!panel) return null;

  const { nodeId, tab, selectedFile } = panel;
  const node = nodes[nodeId];
  const files = nodeFiles[nodeId] || [];
  const diff = nodeDiffs[nodeId];
  const contentKey = selectedFile ? `${nodeId}:${selectedFile}` : null;
  const content = contentKey ? fileContents[contentKey] : undefined;

  const handleTabSwitch = (t: "files" | "diff") => {
    actions.setFilesPanelTab(t);
    if (t === "diff" && diff === undefined) {
      send({ type: WS.GET_NODE_DIFF, node_id: nodeId });
    }
  };

  const handleSelectFile = (path: string) => {
    actions.selectFile(path);
    const key = `${nodeId}:${path}`;
    if (fileContents[key] === undefined) {
      send({ type: WS.GET_FILE_CONTENT, node_id: nodeId, file_path: path });
    }
  };

  return (
    <div className="files-panel">
      <div className="files-header">
        <span className="files-title">{node?.label || nodeId}</span>
        <div className="files-tabs">
          <button
            className={`files-tab ${tab === "files" ? "active" : ""}`}
            onClick={() => handleTabSwitch("files")}
          >
            Files
          </button>
          <button
            className={`files-tab ${tab === "diff" ? "active" : ""}`}
            onClick={() => handleTabSwitch("diff")}
          >
            Diff
          </button>
        </div>
        <button className="branch-btn" onClick={() => actions.closeFilesPanel()}>
          ✕
        </button>
      </div>

      <div className="files-body">
        {tab === "files" && (
          <>
            {selectedFile && content !== undefined ? (
              <div className="file-viewer">
                <div className="file-viewer-header">
                  <button className="file-viewer-back" onClick={() => actions.selectFile(null)}>
                    ← Back
                  </button>
                  <span className="file-viewer-name">{selectedFile}</span>
                </div>
                <pre className="file-viewer-content">{content}</pre>
              </div>
            ) : selectedFile && content === undefined ? (
              <div className="files-loading">Loading...</div>
            ) : (
              <div className="file-list">
                {files.length === 0 && (
                  <div className="files-empty">No files</div>
                )}
                {files.map((f) => (
                  <div
                    key={f}
                    className="file-entry"
                    onClick={() => handleSelectFile(f)}
                  >
                    {f}
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        {tab === "diff" && (
          <div className="diff-viewer">
            {diff === undefined ? (
              <div className="files-loading">Loading diff...</div>
            ) : diff === "" ? (
              <div className="files-empty">No changes</div>
            ) : (
              <pre className="diff-content">
                {diff.split("\n").map((line, i) => {
                  let cls = "";
                  if (line.startsWith("+") && !line.startsWith("+++")) cls = "diff-add";
                  else if (line.startsWith("-") && !line.startsWith("---")) cls = "diff-del";
                  else if (line.startsWith("@@")) cls = "diff-hunk";
                  else if (line.startsWith("diff ") || line.startsWith("index ")) cls = "diff-meta";
                  return (
                    <span key={i} className={cls}>
                      {line}
                      {"\n"}
                    </span>
                  );
                })}
              </pre>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
