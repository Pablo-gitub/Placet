import { useTranslation } from "react-i18next";
import React, { Suspense, lazy, useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Download, FileText, ImageOff, X } from "lucide-react";

// pdf.js is large: loaded only when a PDF is opened.
const PdfViewer = lazy(() => import("./PdfViewer"));

const attachmentUrl = (sessionId, id) =>
  `/api/sessions/${encodeURIComponent(sessionId)}/attachments/${encodeURIComponent(id)}`;

/** A PDF the agent shared: opened in Placet's own viewer, or downloaded. */
function FileCard({ sessionId, attachment, onOpen }) {
  const { t } = useTranslation();
  const url = attachmentUrl(sessionId, attachment.id);
  return (
    <div className="attachment-file" data-testid={`attachment-${attachment.id}`}>
      <button type="button" className="attachment-file-open" aria-label={t("viewer.open", { name: attachment.name })}
        data-testid={`attachment-open-${attachment.id}`} onClick={() => onOpen(attachment)}>
        <FileText size={22} />
        <span className="attachment-file-name">{attachment.name}</span>
      </button>
      <a href={url} download={attachment.name} className="icon-button" aria-label={t("viewer.download")}>
        <Download size={16} />
      </a>
    </div>
  );
}

/** One thumbnail: read from the PC on demand; says so when it cannot be. */
function Thumb({ sessionId, attachment, onOpen }) {
  const { t } = useTranslation();
  const [failed, setFailed] = useState(false);
  const url = attachmentUrl(sessionId, attachment.id);
  if (failed) {
    return (
      <div className="attachment-missing" data-testid={`attachment-missing-${attachment.id}`}>
        <ImageOff size={18} />
        <span>{t("viewer.imageMissing")}</span>
      </div>
    );
  }
  return (
    <button
      type="button"
      className="attachment-thumb"
      data-testid={`attachment-${attachment.id}`}
      onClick={() => onOpen(attachment)}
      aria-label={t("viewer.open", { name: attachment.name })}
    >
      <img src={url} alt={attachment.name} loading="lazy" onError={() => setFailed(true)} />
    </button>
  );
}

function Viewer({ sessionId, attachment, onClose }) {
  const { t } = useTranslation();
  const url = attachmentUrl(sessionId, attachment.id);
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return createPortal(
    <div className="attachment-viewer" role="dialog" aria-modal="true" aria-label={attachment.name} data-testid="attachment-viewer"
      onClick={onClose}>
      <div className="attachment-viewer-bar" onClick={(e) => e.stopPropagation()}>
        <span>{attachment.name}</span>
        <a href={url} download={attachment.name} className="icon-button" aria-label={t("viewer.download")} data-testid="attachment-download">
          <Download size={18} />
        </a>
        <button type="button" className="icon-button" aria-label={t("viewer.close")} onClick={onClose}>
          <X size={18} />
        </button>
      </div>
      <img src={url} alt={attachment.name} onClick={(e) => e.stopPropagation()} />
    </div>,
    document.body
  );
}

/** The images of a message (RF-30), opened full screen with a tap. */
export default function Attachments({ sessionId, items }) {
  const [open, setOpen] = useState(null);
  const close = useCallback(() => setOpen(null), []);
  if (!items?.length) return null;
  return (
    <>
      <div className="attachments" data-testid="attachments">
        {items.map((a) =>
          a.kind === "pdf" ? (
            <FileCard key={a.id} sessionId={sessionId} attachment={a} onOpen={setOpen} />
          ) : (
            <Thumb key={a.id} sessionId={sessionId} attachment={a} onOpen={setOpen} />
          )
        )}
      </div>
      {open && open.kind === "pdf" && (
        <Suspense fallback={null}>
          <PdfViewer url={attachmentUrl(sessionId, open.id)} name={open.name} onClose={close} />
        </Suspense>
      )}
      {open && open.kind !== "pdf" && <Viewer sessionId={sessionId} attachment={open} onClose={close} />}
    </>
  );
}
