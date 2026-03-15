import { useEffect, useCallback, useRef, useMemo } from "react";
import { createPortal } from "react-dom";
import { renderMarkdown } from "../renderMarkdown";

interface Props {
  nodeId: string;
  userMessage: string;
  assistantResponse: string;
  onClose: () => void;
  onQuoteText?: (text: string) => void;
}

export default function NodeModal({ nodeId, userMessage, assistantResponse, onClose, onQuoteText }: Props) {
  const html = useMemo(() => renderMarkdown(assistantResponse, nodeId), [assistantResponse, nodeId]);
  const overlayRef = useRef<HTMLDivElement>(null);
  const responseRef = useRef<HTMLDivElement>(null);
  const quoteBtnRef = useRef<HTMLButtonElement | null>(null);
  const selectedTextRef = useRef("");
  const onQuoteTextRef = useRef(onQuoteText);
  onQuoteTextRef.current = onQuoteText;

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    },
    [onClose],
  );

  // All selection handling via native DOM — no React re-renders
  useEffect(() => {
    const responseEl = responseRef.current;
    if (!responseEl || !onQuoteTextRef.current) return;

    // Create the quote button once, manage via DOM
    const btn = document.createElement("button");
    btn.className = "selection-quote-btn";
    btn.textContent = "Quote";
    btn.style.display = "none";
    document.body.appendChild(btn);
    quoteBtnRef.current = btn;

    const hideBtn = () => { btn.style.display = "none"; };

    const onMouseUp = () => {
      // Defer so the browser can collapse the selection first (e.g. click-to-deselect)
      requestAnimationFrame(() => {
        const sel = window.getSelection();
        const text = sel?.toString().trim();
        if (!text || !sel?.rangeCount) { hideBtn(); return; }
        const range = sel.getRangeAt(0);
        if (!responseEl.contains(range.commonAncestorContainer)) { hideBtn(); return; }
        selectedTextRef.current = text;
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
      if (selectedTextRef.current && onQuoteTextRef.current) {
        onQuoteTextRef.current(selectedTextRef.current);
      }
      hideBtn();
      window.getSelection()?.removeAllRanges();
    };

    responseEl.addEventListener("mouseup", onMouseUp);
    responseEl.addEventListener("mousedown", onMouseDown);
    btn.addEventListener("mousedown", onBtnMouseDown);
    btn.addEventListener("click", onBtnClick);

    return () => {
      responseEl.removeEventListener("mouseup", onMouseUp);
      responseEl.removeEventListener("mousedown", onMouseDown);
      btn.removeEventListener("mousedown", onBtnMouseDown);
      btn.removeEventListener("click", onBtnClick);
      btn.remove();
      quoteBtnRef.current = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

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
          ref={responseRef}
          className="node-modal-response"
          dangerouslySetInnerHTML={{ __html: html }}
        />
      </div>
    </div>,
    document.body,
  );
}
