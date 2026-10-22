import { useTranslation } from "react-i18next";
import { formatDateTime } from "./i18n/formatters";
import React from "react";
import { ArrowLeft, ArrowRight } from "lucide-react";
import MessageBody from "./MessageBody";
import { useWide } from "./useWide";
import { previewOf } from "./messages";

/** The row's one-line summary: number, route, state, time and the start of the message. */
export function MessageSummary({ req }) {
  const { t } = useTranslation();
  return (
    <>
      <span className="message-id">#{req.id}</span>
      <span className="agent-route">
        <span className="agent-badge">{req.sender_id}</span>
        <ArrowRight size={14} style={{ color: "var(--text-muted)" }} aria-hidden="true" />
        <span className="agent-badge">{req.recipient_id}</span>
      </span>
      <span className={`status-badge status-${req.status.toLowerCase()}`}>
        {t(`status.${req.status}`, { defaultValue: req.status })}
      </span>
      <time dateTime={req.created_at}>{formatDateTime(req.created_at)}</time>
      {/* Drawn by CSS from the attribute: the message text itself appears once, in the body. */}
      <span className="message-preview" data-preview={previewOf(req)} />
    </>
  );
}

/** Settled requests, one compact row each; the full message and its audit timeline open on demand (RF-13). */
export default function History({ requests, sessions, focusId, onOpenSession, onOpenMessage, onBack }) {
  const { t } = useTranslation();
  const wide = useWide();
  const settled = requests.filter((r) => r.status !== "PENDING");
  return (
    <div data-testid="history-section">
      {onBack && (
        <button type="button" className="link-button project-detail-back-btn history-back" data-testid="history-back" onClick={onBack}>
          <ArrowLeft size={16} /> {t("settings.title")}
        </button>
      )}
      <h2 className="history-title">{t("history.title")}</h2>
      {settled.length === 0 ? (
        <div className="empty-state" data-testid="empty-history">
          <p>{t("history.empty")}</p>
        </div>
      ) : (
        settled.map((req) => {
          const cls = `card message-row history-row${req.id === focusId ? " card-highlight" : ""}`;
          if (wide) {
            // Two columns: the whole message opens in the right pane.
            return (
              <div key={req.id} id={`request-${req.id}`} className={cls} data-testid={`history-card-${req.id}`}>
                <button type="button" className="message-row-button" onClick={() => onOpenMessage?.(req.id)}>
                  <MessageSummary req={req} />
                </button>
              </div>
            );
          }
          return (
            <details key={req.id} id={`request-${req.id}`} className={cls} data-testid={`history-card-${req.id}`}
              open={req.id === focusId ? true : undefined}>
              <summary><MessageSummary req={req} /></summary>
              <div className="message-row-body">
                <MessageBody req={req} sessions={sessions} onOpenSession={onOpenSession} />
              </div>
            </details>
          );
        })
      )}
    </div>
  );
}
