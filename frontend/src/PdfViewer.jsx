import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Download, X } from "lucide-react";

/** Pages are drawn at this width (CSS px) times the screen's pixel ratio. */
const MAX_PAGE_WIDTH = 900;

/**
 * A PDF shared by an agent, drawn with pdf.js on a white page. Many
 * generated PDFs have no page background: the phone's own viewer shows them
 * on black in dark mode, where dark text is unreadable. pdf.js is loaded
 * only when a PDF is opened, and never runs the PDF's scripts.
 */
export default function PdfViewer({ url, name, onClose }) {
  const pages = useRef(null);
  const [state, setState] = useState({ status: "loading", count: 0 });

  useEffect(() => {
    let cancelled = false;
    let task = null;
    (async () => {
      try {
        const pdfjs = await import("pdfjs-dist");
        const worker = await import("pdfjs-dist/build/pdf.worker.min.mjs?url");
        pdfjs.GlobalWorkerOptions.workerSrc = worker.default;
        task = pdfjs.getDocument({ url, isEvalSupported: false, enableXfa: false });
        const doc = await task.promise;
        if (cancelled) return;
        setState({ status: "ready", count: doc.numPages });
        const width = Math.min(pages.current?.clientWidth || MAX_PAGE_WIDTH, MAX_PAGE_WIDTH);
        const ratio = window.devicePixelRatio || 1;
        for (let n = 1; n <= doc.numPages && !cancelled; n++) {
          const page = await doc.getPage(n);
          const base = page.getViewport({ scale: 1 });
          const viewport = page.getViewport({ scale: (width / base.width) * ratio });
          const canvas = document.createElement("canvas");
          canvas.width = viewport.width;
          canvas.height = viewport.height;
          canvas.style.width = `${viewport.width / ratio}px`;
          canvas.className = "pdf-page";
          canvas.dataset.testid = `pdf-page-${n}`;
          const ctx = canvas.getContext("2d");
          ctx.fillStyle = "#ffffff";
          ctx.fillRect(0, 0, canvas.width, canvas.height);
          pages.current?.appendChild(canvas);
          await page.render({ canvasContext: ctx, canvas, viewport, background: "#ffffff" }).promise;
        }
      } catch (e) {
        if (!cancelled) setState({ status: "error", count: 0, message: String(e?.message || e) });
      }
    })();
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      cancelled = true;
      task?.destroy();
      window.removeEventListener("keydown", onKey);
    };
  }, [url, onClose]);

  return createPortal(
    <div className="attachment-viewer pdf-viewer" role="dialog" aria-modal="true" aria-label={name} data-testid="pdf-viewer">
      <div className="attachment-viewer-bar">
        <span>
          {name}
          {state.count > 1 && ` · ${state.count} pagine`}
        </span>
        <a href={url} download={name} className="icon-button" aria-label="Scarica">
          <Download size={18} />
        </a>
        <button type="button" className="icon-button" aria-label="Chiudi" onClick={onClose}>
          <X size={18} />
        </button>
      </div>
      <div className="pdf-pages" ref={pages}>
        {state.status === "loading" && <p className="chat-status">Carico il PDF…</p>}
        {state.status === "error" && (
          <p className="chat-status" data-testid="pdf-error">
            Il PDF non si apre: il PC non risponde o il file non è più disponibile.
          </p>
        )}
      </div>
    </div>,
    document.body
  );
}
