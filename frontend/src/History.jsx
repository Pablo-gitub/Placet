import { useTranslation } from "react-i18next";
import { formatDateTime } from "./i18n/formatters";
import React from "react";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { Origin } from "./RequestCard";
import Timeline from "./Timeline";
import Markdown from "./Markdown";
import i18n from "./i18n";

function answerOf(req) {
  return [
    ...(req.selected_options || []),
    ...(req.custom_text ? [req.options?.length ? i18n.t("history.other", { text: req.custom_text }) : i18n.t("history.note", { text: req.custom_text })] : []),
  ].join(" · ");
}

/** Settled requests with their audit timeline (RF-13). */
export default function History({ requests, sessions, focusId, onOpenSession, onBack }) {
  const { t } = useTranslation();
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
        settled.map((req) => (
          <div
            key={req.id}
            id={`request-${req.id}`}
            className={`card${req.id === focusId ? " card-highlight" : ""}`}
            data-testid={`history-card-${req.id}`}
          >
            <div className="card-header">
              <div className="agent-route">
                <span className="agent-badge">{req.sender_id}</span>
                <ArrowRight size={14} style={{ color: "var(--text-muted)" }} />
                <span className="agent-badge">{req.recipient_id}</span>
              </div>
              <span className={`status-badge status-${req.status.toLowerCase()}`}>
                {t(`status.${req.status}`, { defaultValue: req.status })}
              </span>
            </div>
            <Origin req={req} sessions={sessions} onOpenSession={onOpenSession} />
            {req.recipient_id !== "human" && req.delivery && (
              <p className="aspects" data-testid={`aspects-${req.id}`}>
                {t("history.delivery")} {t(`delivery.${req.delivery}`, { defaultValue: req.delivery })} · {t("history.execution")}{" "}
                {t(`execution.${req.execution}`, { defaultValue: req.execution })} · {t("history.reply")} {t(`reply.${req.reply}`, { defaultValue: req.reply })}
              </p>
            )}
            {req.context && (
              <section className="request-section">
                <h3 className="section-label">{t("history.context")}</h3>
                <div className="card-context">
                  <Markdown>{req.context}</Markdown>
                </div>
              </section>
            )}
            <section className="request-section">
              <h3 className="section-label">
                {req.recipient_id === "human"
                  ? t("history.question")
                  : req.edited_prompt
                    ? t("history.editedPrompt", { agent: req.recipient_id })
                    : t("history.prompt", { agent: req.recipient_id })}
              </h3>
              <Markdown>{req.edited_prompt || req.prompt}</Markdown>
            </section>
            {(req.selected_options?.length > 0 || req.custom_text) && (
              <p data-testid={`history-answer-${req.id}`} style={{ fontSize: "0.85rem", marginBottom: "0.6rem" }}>
                <strong>{t("history.answer")}</strong> {answerOf(req)}
              </p>
            )}
            {!req.response && req.progress_text && (
              <p className="history-progress" data-testid={`history-progress-${req.id}`}>
                <strong>{t("history.progress")}</strong> {req.progress_text}
              </p>
            )}
            {req.response && (
              <div className="history-response">
                <strong>{t("history.responder", { agent: req.response.responder_id })}</strong>{" "}
                {req.response.implicit && (
                  <span
                    className="status-badge implicit-badge"
                    data-testid={`implicit-${req.id}`}
                    title={t("history.implicitHint")}
                  >
                    {t("history.implicit")}
                  </span>
                )}{" "}
                {req.response.content}
                {req.sender_id !== "human" && req.recipient_id !== "human" && (
                  <p>{req.return_decision === "forwarded" ? t("return.forwardedTo", { id: req.return_forward_id })
                    : req.delivered_at ? t("return.delivered") : req.return_decision === "rejected"
                    ? t("return.withheld") : req.allow_auto_return || req.return_decision === "approved"
                      ? t("return.queued") : t("return.pending")}</p>
                )}
                {req.return_text != null && req.return_text !== req.response.content && (
                  <p><strong>{t("return.authorizedVersion")}</strong> {req.return_text}</p>
                )}
              </div>
            )}
            <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.6rem" }}>
              {t("history.created")} {formatDateTime(req.created_at)}
            </div>
            <Timeline requestId={req.id} />
          </div>
        ))
      )}
    </div>
  );
}
