import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import Markdown from "./Markdown";
import { apiErrorDescriptor } from "./i18n/errors";
import { sessionName, sessionStatus } from "./sessionInfo";

/** The message as the user wrote or approved it. */
export function MessageText({ req }) {
  return <Markdown>{req.edited_prompt || req.prompt}</Markdown>;
}

/**
 * Cancel (asking first) and forward (to another open session) of a prompt that has not been delivered.
 * The backend refuses both once delivery has started; the reason is shown in place.
 */
export function MessageActions({ req, sessions = [], onDone, canCancel = true, canForward = true, canRetry = false }) {
  const { t } = useTranslation();
  const [mode, setMode] = useState(null); // null | "cancel" | "forward"
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const targets = sessions.filter((s) => typeof s.id === "string" && !s.ended_at && !s.excluded
    && s.id !== req.sender_session_id && s.id !== req.recipient_session_id);

  const send = async (path, body) => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/requests/${req.id}/undelivered/${path}`, {
        method: "POST",
        ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
      });
      if (res.ok) {
        setMode(null);
        onDone?.();
        return;
      }
      const data = await res.json().catch(() => ({}));
      const descriptor = apiErrorDescriptor(data, res.status);
      setError(t(descriptor.key, descriptor.params));
    } catch {
      setError(t("errors.connection"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="message-actions" data-testid={`message-actions-${req.id}`}>
      {mode === null && (
        <div className="actions-row">
          {canRetry && (
            <button type="button" className="btn btn-primary" onClick={() => send("retry")} disabled={busy}>
              {t("undelivered.retry")}
            </button>
          )}
          {canForward && (
            <button type="button" className="btn btn-secondary" onClick={() => setMode("forward")} disabled={busy}>
              {t("undelivered.forward")}
            </button>
          )}
          {canCancel && (
            <button type="button" className="btn btn-secondary btn-danger-outline" onClick={() => setMode("cancel")} disabled={busy}>
              {t("undelivered.cancel")}
            </button>
          )}
        </div>
      )}
      {mode === "cancel" && (
        <div role="group" aria-label={t("undelivered.cancel")}>
          <p>{t("undelivered.cancelConfirm", { id: req.id })}</p>
          <div className="actions-row">
            <button type="button" className="btn btn-danger" disabled={busy} onClick={() => send("cancel")}>
              {t("undelivered.cancelYes")}
            </button>
            <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => setMode(null)}>
              {t("undelivered.back")}
            </button>
          </div>
        </div>
      )}
      {mode === "forward" && (
        <div role="group" aria-label={t("undelivered.forward")}>
          <label className="prompt-editor-label" htmlFor={`forward-to-${req.id}`}>{t("undelivered.forwardTo")}</label>
          <select id={`forward-to-${req.id}`} className="session-select" value={target}
            onChange={(e) => setTarget(e.target.value)}>
            <option value="">{targets.length ? t("undelivered.pick") : t("undelivered.noSession")}</option>
            {targets.map((s) => (
              <option key={s.id} value={s.id}>{s.agent_id} · {sessionName(s)} · {sessionStatus(s).label}</option>
            ))}
          </select>
          <div className="actions-row">
            <button type="button" className="btn btn-primary" disabled={busy || !target}
              onClick={() => send("forward", { session_id: target })}>
              {t("undelivered.forwardGo")}
            </button>
            <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => setMode(null)}>
              {t("undelivered.back")}
            </button>
          </div>
        </div>
      )}
      {error && <p className="composer-note error-text" role="alert">{error}</p>}
    </div>
  );
}
