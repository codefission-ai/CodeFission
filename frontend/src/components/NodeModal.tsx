import { useEffect, useCallback, useRef, useMemo } from "react";
import { createPortal } from "react-dom";
import { renderMarkdown } from "../renderMarkdown";

interface Props {
  nodeId: string;
  userMessage: string;
  assistantResponse: string;
  onClose: () => void;
}

export default function NodeModal({ nodeId, userMessage, assistantResponse, onClose }: Props) {
  const html = useMemo(() => renderMarkdown(assistantResponse, nodeId), [assistantResponse, nodeId]);
  const overlayRef = useRef<HTMLDivElement>(null);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    },
    [onClose],
  );

  useEffect(() => {
    window.addEventListener("keydown", handleKeyDown);

    // Block wheel events at the native level so React Flow can't intercept them
    const el = overlayRef.current;
    const stopWheel = (e: WheelEvent) => e.stopPropagation();
    if (el) el.addEventListener("wheel", stopWheel, { passive: false });

    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      if (el) el.removeEventListener("wheel", stopWheel);
    };
  }, [handleKeyDown]);

  return createPortal(
    <div ref={overlayRef} className="node-modal-overlay" onClick={onClose}>
      <div className="node-modal" onClick={(e) => e.stopPropagation()}>
        <button className="node-modal-close" onClick={onClose}>
          &times;
        </button>
        {userMessage && (
          <div className="node-modal-user">{userMessage}</div>
        )}
        <div
          className="node-modal-response"
          dangerouslySetInnerHTML={{ __html: html }}
        />
      </div>
    </div>,
    document.body,
  );
}
